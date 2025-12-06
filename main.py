import json
from crewai import Crew, Process, Task
import html
from typing import Optional, Dict, Any
import os, re
from datetime import datetime, timedelta, timezone  # 기존에 datetime만 있다면 timedelta, timezone 추가
from zoneinfo import ZoneInfo

from config import manager_llm, worker_llm
from tools.mcp_loader import load_flight_mcp_tools, close_all_mcp_adapters
from tools.tmap_tool import TmapTrafficTool

from agents.user_profile_agent import create_user_profile_agent
from agents.parking_agent import create_parking_agent
from agents.departure_agent import create_departure_agent
from agents.notification_agent import create_notification_agent
from agents.flight_agent import create_flight_agent
from aws_scheduler import create_departure_notification_schedule
from aws_scheduler import create_email_schedule

MEMORY_FILE = "user_memory.json"

def _json_default(o):
    """datetime 같은 JSON 직렬화 안되는 타입을 문자열로 바꿔주는 헬퍼."""
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)

def save_flight_task(user_memory: Dict[str, Any], flight_task: Dict[str, Any]) -> None:
    """
    user_memory.json에 flight_task를 저장할 때는
    무조건 순수 dict를 json.dumps 해서 넣는다.
    """
    user_memory["flight_task"] = json.dumps(
        flight_task,
        ensure_ascii=False,
        default=_json_default,
    )

def load_flight_task(user_memory: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    user_memory에서 flight_task를 안전하게 읽어오는 함수.
    - dict로 이미 들어있으면 그대로 반환
    - str이면 json.loads 시도
    - 그 외/실패 시 None 반환
    """
    raw = user_memory.get("flight_task")

    if raw is None:
        print("[WARN] user_memory에 flight_task가 없습니다.")
        return None

    if isinstance(raw, dict):
        # 이미 dict 형태로 저장되어 있는 경우
        return raw

    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"[WARN] flight_task JSON 파싱 실패: {e}")
            print(f"[DEBUG] flight_task raw value: {raw[:200]}")
            return None

    print(f"[WARN] flight_task 타입이 이상합니다: {type(raw)}")
    return None

def extract_json_from_text(text: str) -> dict:
    code_match = re.search(r"```json(.*?)```", text, re.S | re.I)
    if code_match:
        candidate = code_match.group(1).strip()
    else:
        # 그냥 처음 '{'부터 마지막 '}'까지 잡기 (대충이지만 대부분 케이스는 커버)
        brace_match = re.search(r"\{.*\}", text, re.S)
        if not brace_match:
            raise ValueError("JSON 블록을 찾지 못함")
        candidate = brace_match.group(0)

    return json.loads(candidate)


def load_user_memory():
    """user_memory.json을 읽어서 dict로 반환. 없으면 기본 구조 반환."""
    if not os.path.exists(MEMORY_FILE):
        return {"trip_history": []}
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # 파일 깨졌을 때 방어
        return {"trip_history": []}

def save_user_memory(memory: dict):
    """메모리 dict를 user_memory.json에 저장."""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

def parse_json_safe(raw):
    """
    Task.output.raw 가 문자열 / dict / 기타 형태일 때,
    가능하면 JSON(dict)으로 파싱해서 돌려주는 헬퍼.
    실패하면 None.
    """
    if raw is None:
        return None

    # 이미 dict면 그대로 사용
    if isinstance(raw, dict):
        return raw

    # 문자열이면 여러 형태를 순서대로 시도
    if isinstance(raw, str):
        text = raw.strip()

        # 1) ```json ...``` 코드블록 우선 시도
        try:
            return extract_json_from_text(text)
        except Exception:
            pass

        # 2) 그냥 json.loads(text) 시도
        try:
            return json.loads(text)
        except Exception:
            pass

        # 3) 마지막 fallback: 가장 바깥 {...}만 잘라서 시도
        try:
            start = text.index("{")
            end = text.rfind("}")
            return json.loads(text[start:end+1])
        except Exception:
            return None

    # 그 외 타입은 지원 안 함
    return None


def extract_alert_bodies(notification_raw: str):
    """
    notif_task 결과에서
    ### 5시간 전 알림
    ...
    ### 2시간 전 알림
    ...
    형식을 기준으로 두 알림 본문을 분리.

    둘 다 못 뽑으면 (None, None) 리턴.
    """
    if not notification_raw:
        return None, None

    text = str(notification_raw).replace("\r\n", "\n")

    if "### 5시간 전 알림" not in text:
        return None, None

    # 5시간 전 부분 기준으로 split
    parts = text.split("### 5시간 전 알림", 1)
    if len(parts) < 2:
        return None, None

    after_5 = parts[1]

    # 다시 2시간 전 기준으로 split
    subparts = after_5.split("### 2시간 전 알림", 1)
    section_5 = subparts[0].strip()
    section_2 = subparts[1].strip() if len(subparts) > 1 else ""

    # 맨 앞에 불릿(- ) 정도는 그냥 둬도 되지만, 깔끔하게 하고 싶으면 추가 처리 가능
    return section_5, section_2 or None

def schedule_email_alerts_from_summary(summary: dict):
    """
    summary(dict)를 받아서,
    - 사용자 이메일
    - 첫 번째 추천 항공편 출발 시각
    - notif_task 결과(5h/2h 알림 문구)
    를 기반으로 EventBridge Scheduler에 5h/2h 전 이메일 스케줄을 등록한다.
    """

    user_hint = summary.get("user_input_hint", {})
    contact = user_hint.get("contact", {})
    to_email = contact.get("email")

    if not to_email:
        print("[INFO] 이메일 주소가 없어서 알림 스케줄을 만들지 않습니다.")
        return

    tasks = summary.get("tasks", {})
    flight_raw = tasks.get("flight")
    notif_raw = tasks.get("notification")

    # 1) flight_task JSON 파싱
    flight_json = parse_json_safe(flight_raw)
    print("[DEBUG] schedule_email_alerts flight_json:", flight_json)

    flights = []

    # dict 형태인 경우
    if isinstance(flight_json, dict):
        # 1) wrapper 형태 (best_flights / flights / recommendations / results)
        if any(k in flight_json for k in ("best_flights", "flights", "recommendations", "results")):
            flights = (
                    flight_json.get("best_flights")
                    or flight_json.get("flights")
                    or flight_json.get("recommendations")
                    or flight_json.get("results")
                    or []
            )
        else:
            # 2) 이미 "단일 항공편" 객체인 경우 (지금 네 케이스)
            #    예: {"airline": "...", "flight_number": "...", "departure_time_local": "...", ...}
            flights = [flight_json]

    # list 형태인 경우 (이미 여러 개 항공편 배열인 경우)
    elif isinstance(flight_json, list):
        flights = flight_json

    if not flights:
        print("[WARN] 항공편 리스트가 비어 있어 스케줄 생성 불가")
        print(f"[DEBUG] flight_raw (앞 200자): {str(flight_raw)[:200]}")
        return

    first = flights[0]
    flight_id = first.get("flight_id") or first.get("id") or first.get("flight_number") or "UNKNOWN"

    # 출발 시각 문자열 (예: "2025-12-10T09:30")
    departure_time_str = (
        first.get("departure_time_local")
        or first.get("departure_time")
        or first.get("departure")
    )

    if not departure_time_str:
        print("[WARN] departure_time 필드를 찾을 수 없습니다. flight JSON 구조를 확인하세요.")
        return

    # 2) 출발 시각을 datetime으로 파싱 (ICN 출발 기준 Asia/Seoul 가정)
    try:
        # "YYYY-MM-DDTHH:MM" / "YYYY-MM-DD HH:MM" 둘 다 어느 정도 처리
        departure_time_str = departure_time_str.replace(" ", "T")
        dep_local = datetime.fromisoformat(departure_time_str)
    except Exception:
        print(f"[WARN] departure_time 파싱 실패: {departure_time_str}")
        return

    dep_local = dep_local.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    dep_utc = dep_local.astimezone(timezone.utc)

    # 3) 5시간 전 / 2시간 전 시각 계산
    notify_5h = dep_utc - timedelta(hours=5)
    notify_2h = dep_utc - timedelta(hours=2)

    now_utc = datetime.now(timezone.utc)
    if notify_5h <= now_utc:
        print(f"[WARN] 5시간 전 알림 시각({notify_5h})이 이미 지났습니다. 생성하지 않습니다.")
    if notify_2h <= now_utc:
        print(f"[WARN] 2시간 전 알림 시각({notify_2h})이 이미 지났습니다. 생성하지 않습니다.")

    # 4) notif_task 결과에서 본문 추출
    body_5h, body_2h = extract_alert_bodies(notif_raw)
    if not body_5h or not body_2h:
        print("[WARN] notif_task 결과에서 5시간/2시간 전 본문을 제대로 추출하지 못했습니다.")
        print("      일단 전체 notif_task 텍스트를 그대로 본문으로 사용합니다.")
        body_5h = str(notif_raw)
        body_2h = str(notif_raw)

    subject_5h = f"[인천공항] 출국 5시간 전 알림 ({flight_id})"
    subject_2h = f"[인천공항] 출국 2시간 전 알림 ({flight_id})"

    # 5) EventBridge Scheduler로 스케줄 생성
    if notify_5h > now_utc:
        create_email_schedule(
            run_time_utc=notify_5h,
            to_email=to_email,
            subject=subject_5h,
            body=body_5h,
            tag="5h_before",
        )

    if notify_2h > now_utc:
        create_email_schedule(
            run_time_utc=notify_2h,
            to_email=to_email,
            subject=subject_2h,
            body=body_2h,
            tag="2h_before",
        )


def append_trip_memory(user_input_hint: dict, summary: dict):
    """이번 여행 정보를 trip_history에 한 건 추가."""
    memory = load_user_memory()
    history = memory.get("trip_history", [])

    entry = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "trip": user_input_hint.get("trip", {}),
        "passengers": user_input_hint.get("passengers", {}),
        "home_address": user_input_hint.get("home_address"),
        # 필요하면 아래처럼 요약도 같이 저장 가능
        "parking_raw": summary["tasks"].get("parking"),
        "departure_raw": summary["tasks"].get("departure"),
        "flight_raw": summary["tasks"].get("flight"),
    }

    history.append(entry)
    # 너무 길어지지 않게 최근 20건만 유지
    memory["trip_history"] = history[-20:]
    save_user_memory(memory)


def run_airport_multi_agent():
    # ✅ MCP 어댑터 정리를 위해 try/finally 사용
    try:
        # ========================
        # 0) 메모리에서 이전 여행 기록 불러오기
        # ========================
        memory = load_user_memory()
        last_trip_entry = None
        if memory.get("trip_history"):
            last_trip_entry = memory["trip_history"][-1]
            trip = last_trip_entry.get("trip", {})
            last_from = trip.get("from", "서울")
            last_to = trip.get("to", "")
            last_dep = trip.get("departure_date")
            last_ret = trip.get("return_date")

            print("저는 구글에서 훈련된 대규모 언어 모델입니다.")
            print("이전에 아래와 같은 여행을 계획하셨네요:")
            print(f" - {last_dep} ~ {last_ret}: {last_from} → {last_to}")
            print("이번에도 비슷한 일정으로 가시나요? 직전 정보를 기본값으로 불러옵니다.\n")
        else:
            last_from = "서울"
            last_to = "시드니"
            last_dep = "2025-11-23"
            last_ret = None

        print("=== 인천공항 출국 플래너 ===")
        print("아래 질문에 답해주면, 그 정보를 바탕으로 멀티 에이전트가 전체 플로우를 계산합니다.\n")

        from_city = input("출발 도시 (예: 서울): ").strip() or "서울"
        to_city = input("도착 도시 (예: 시드니): ").strip() or "시드니"
        dep_date = input("출발일 (YYYY-MM-DD): ").strip() or "2025-11-23"
        ret_date = input("귀국일 (YYYY-MM-DD, 없으면 엔터): ").strip() or None
        home_address = input("거주지/출발지 (예: 서울시 서대문구 연희동): ").strip() or "서울시 강남구 대치동"

        email = input("알림을 받을 이메일 주소(없으면 엔터): ").strip() or None

        adults = input("성인 인원 수 (기본 2): ").strip()
        adults = int(adults) if adults.isdigit() else 2

        children = input("어린이 인원 수 (기본 0): ").strip()
        children = int(children) if children.isdigit() else 0

        infants = input("유아 인원 수 (기본 0): ").strip()
        infants = int(infants) if infants.isdigit() else 0

        user_input_hint = {
            "trip": {
                "from": from_city,
                "to": to_city,
                "departure_date": dep_date,
                "return_date": ret_date,
            },
            "passengers": {
                "adults": adults,
                "children": children,
                "infants": infants,
            },
            "parking": {
                "need_parking": True,
                "parking_type": "장기",
                "preferred_terminal": "T1",
            },
            "home_address": home_address,
            "transport_preference": "자가용 우선",
            "contact": {
                "email": email,
            },
        }

        user_input_hint_str = json.dumps(user_input_hint, ensure_ascii=False, indent=2)

        # ========================
        # 1) MCP 툴 한 번에 로드
        # ========================
        tool_buckets = load_flight_mcp_tools()
        parking_tools   = tool_buckets.get("parking", [])
        departure_tools = tool_buckets.get("departure", [])
        flight_tools    = tool_buckets.get("flight", [])
        amadeus_tools   = tool_buckets.get("amadeus", [])
        transport_tools = tool_buckets.get("transport", [])

        tmap_tool = TmapTrafficTool()
        transport_tools.append(tmap_tool)

        flight_tools_for_agent = flight_tools + [
            t for t in amadeus_tools if t not in flight_tools
        ]

        # ========================
        # 2) 에이전트 생성
        # ========================
        user_profile_agent = create_user_profile_agent()
        parking_agent      = create_parking_agent(parking_tools)
        departure_agent    = create_departure_agent(departure_tools)
        notification_agent = create_notification_agent(transport_tools)
        flight_agent       = create_flight_agent(flight_tools_for_agent)


        # ========================
        # 3) Task 정의
        # ========================

        user_profile_task = Task(
            description=(
                "다음은 사용자가 콘솔에서 직접 입력한 여행 정보 힌트이다.\n\n"
                f"{user_input_hint_str}\n\n"
                "위 정보를 최대한 그대로 활용하되, 필요한 경우 합리적인 가정을 통해 "
                "부족한 필드를 보완해서 최종 여행 프로필 JSON을 만들어라.\n\n"
                "주의사항:\n"
                "- 가상의 대화 예시를 길게 쓰지 말고, 최종 JSON만 출력하라.\n"
                "- 필드는 다음 예시를 참조하되, 실제 값은 위 힌트를 우선 사용하라.\n\n"
                "예시 형식:\n"
                "{\n"
                '  \"trip\": {\n'
                f'    \"from\": \"{from_city}\",\n'
                f'    \"to\": \"{to_city}\",\n'
                f'    \"departure_date\": \"{dep_date}\",\n'
                f'    \"return_date\": \"{ret_date}\"\n'
                "  },\n"
                '  \"passengers\": {\n'
                f'    \"adults\": {adults},\n'
                f'    \"children\": {children},\n'
                f'    \"infants\": {infants}\n'
                "  },\n"
                '  \"parking\": {\n'
                '    \"need_parking\": true,\n'
                '    \"parking_type\": \"장기\",\n'
                '    \"preferred_terminal\": \"T1\"\n'
                "  },\n"
                f'  \"home_address\": \"{home_address}\",\n'
                '  \"transport_preference\": \"자가용 우선\"\n'
                "}\n\n"
                "최종 출력은 반드시 JSON만 출력해라."
            ),
            agent=user_profile_agent,
            expected_output="사용자 여행 계획과 선호가 담긴 JSON 프로필",
        )

        flight_task = Task(
            description=(
                "당신은 실제 항공편 검색 MCP 툴을 사용하는 최적 항공편 추천 에이전트이다.\n"
                "⚠️ MCP 툴이 연결되어 있다면, 'Simulated Tool Call'처럼 흉내내지 말고 "
                "실제로 툴(search_flight_offers, discover_flights 등)을 호출해라.\n\n"
                "다음은 사용자 프로필이다.\n\n"
                "### 사용자 프로필(JSON)\n"
                "{{user_profile}}\n\n"
                "위 정보를 바탕으로 출발일, 목적지, 인원 수에 맞는 항공편 목록을 조회하고, "
                "최적의 항공편 1~3개를 추천하라.\n\n"
                "최종 출력은 자연어 텍스트를 섞지 말고, 아래와 같은 JSON만 출력해야 한다.\n"
                "{\n"
                '  \"selection_reason\": \"왜 이 항공편들을 골랐는지 한국어로 3~5문장 설명\",\n'
                '  \"best_flights\": [\n'
                "    {\n"
                '      \"airline\": \"Korean Air\",\n'
                '      \"flight_number\": \"KE123\",\n'
                '      \"is_nonstop\": true,\n'
                '      \"departure_airport\": \"ICN\",\n'
                '      \"arrival_airport\": \"SYD\",\n'
                '      \"departure_time_local\": \"2025-11-23T10:15:00+09:00\",\n'
                '      \"arrival_time_local\": \"2025-11-23T21:35:00+11:00\",\n'
                '      \"duration_minutes\": 800,\n'
                '      \"price_total\": 1234560,\n'
                '      \"cabin\": \"ECONOMY\"\n'
                "    }\n"
                "  ]\n"
                "}\n\n"
                "반드시 위와 같이 best_flights 배열 안에 최소 1개 이상의 항공편을 넣고, "
                "각 항공편에 departure_time_local을 ISO8601 형식(예: 2025-11-23T10:15:00+09:00)으로 포함해라.\n"
                "JSON 외의 텍스트는 출력하지 말 것."
            ),
            agent=flight_agent,
            context=[user_profile_task],
            expected_output="selection_reason + best_flights JSON",
        )

        parking_task = Task(
            description=(
                "다음은 사용자 프로필이다. 이를 기반으로 인천공항 주차장을 추천해라.\n\n"
                "### 사용자 프로필(JSON)\n"
                "{{user_profile}}\n\n"
                "icn-mcp의 주차장 관련 툴(get_parking_status 등)을 최소 1회 호출해서 "
                "출국 시점 기준으로 가장 여유 있는 주차장을 선택해라.\n\n"
                "최종 출력은 **설명 텍스트와 JSON을 섞지 말고**, 반드시 아래 형식의 JSON만 출력하라.\n"
                "예시 형식:\n"
                "{\n"
                '  \"summary\": \"추천 이유를 한국어로 3~5문장 정리\",\n'
                '  \"best_parking\": {\n'
                '    \"name_ko\": \"제1여객터미널 장기주차장 P3\",\n'
                '    \"code\": \"P3\",\n'
                '    \"type\": \"장기\",\n'
                '    \"terminal\": \"T1\",\n'
                '    \"expected_congestion\": \"보통\",  \n'
                '    \"note\": \"터미널까지 도보 8~10분, 셔틀 운행\"\n'
                "  }\n"
                "}\n\n"
                "위 예시와 비슷한 구조를 유지하되 실제 값은 MCP 툴 응답을 기반으로 채워라. "
                "JSON 외의 자연어 텍스트는 출력하지 말 것."
            ),
            agent=parking_agent,
            context=[user_profile_task],
            expected_output="추천 주차장 summary + best_parking JSON",
        )

        departure_task = Task(
            description=(
                "다음은 사용자 프로필과 주차장 추천 결과이다.\n\n"
                "### 사용자 프로필(JSON)\n"
                "{{user_profile}}\n\n"
                "### 주차장 추천 결과(JSON)\n"
                "{{parking_result}}\n\n"
                "위 parking_result.best_parking 정보를 활용하여, "
                "동선이 좋은 출국장/보안검색대를 추천하라. "
                "예를 들어 T1 장기주차장 P3면 제1여객터미널 출국장 중 가까운 구역을 우선 고려해야 한다.\n\n"
                "icn-mcp의 출국장/보안 검색 관련 툴을 사용하여, "
                "예상 대기시간과 함께 가장 한가하고 동선이 좋은 출국장을 추천하라.\n\n"
                "최종 출력은 아래 JSON 형식만 사용해야 한다.\n"
                "{\n"
                '  \"summary\": \"추천 출국장/보안검색대와 동선을 한국어로 3~6문장 요약\",\n'
                '  \"best_departure_gate\": {\n'
                '    \"terminal\": \"T1\",\n'
                '    \"gate_id\": \"6번\",\n'
                '    \"security_lane\": \"일반\",\n'
                '    \"estimated_wait_minutes\": 25\n'
                "  },\n"
                '  \"parking_link\": {\n'
                '    \"parking_name_ko\": \"제1여객터미널 장기주차장 P3\",\n'
                '    \"parking_code\": \"P3\",\n'
                '    \"walking_time_from_parking_minutes\": 10\n'
                "  }\n"
                "}\n\n"
                "JSON 외의 설명 텍스트는 출력하지 말 것."
            ),
            agent=departure_agent,
            context=[user_profile_task, parking_task],
            expected_output="추천 출국장 summary + best_departure_gate + parking_link JSON",
        )

        notif_task = Task(
            description=(
                "당신은 출국 알림 에이전트이다.\n\n"
                "다음은 지금까지의 정보이다.\n\n"
                "### 사용자 프로필(JSON)\n"
                "{{user_profile}}\n\n"
                "### 주차장 추천 결과(JSON)\n"
                "{{parking_result}}\n\n"
                "### 출국장 추천 결과(JSON)\n"
                "{{departure_result}}\n\n"
                "### 항공편 추천 결과(JSON)\n"
                "{{flight_result}}\n\n"
                "flight_result.best_flights[0].departure_time_local을 기준으로, "
                "출국 5시간 전 알림과 2시간 전 알림에 들어갈 메시지 템플릿을 생성하라.\n\n"
                "1) 5시간 전 알림\n"
                "- 사용자가 아직 집에 있을 가능성이 크다고 가정한다.\n"
                "- Tmap 교통 Tool(tmap_traffic)을 사용하여 home_address → 인천공항(해당 터미널)까지의 "
                "실시간 교통 정보를 조회하고, 그 결과를 바탕으로 '지금 교통상황', '권장 출발 시각'을 안내하라.\n"
                "- 주차장 JSON(best_parking)을 참고하여 어떤 주차장으로 가야 하는지도 함께 알려라.\n\n"
                "2) 2시간 전 알림\n"
                "- 사용자가 공항 인근 또는 공항에 도착했을 가능성이 크다고 가정한다.\n"
                "- 출국장 JSON(best_departure_gate, parking_link)을 기반으로, "
                "어느 층/어느 게이트로 이동해야 하는지, 보안 검색 예상 대기시간 등을 안내하라.\n"
                "- Tmap 교통 Tool은 '아직 출발 전일 수 있는 예외 상황'만 짧게 언급하는 수준으로 사용해도 된다.\n\n"
                "최종 출력 형식은 다음을 따르라.\n"
                "### 5시간 전 알림\n"
                "- 한국어 메시지 본문 한 덩어리 (여러 문장 가능)\n\n"
                "### 2시간 전 알림\n"
                "- 한국어 메시지 본문 한 덩어리 (여러 문장 가능)\n"
            ),
            agent=notification_agent,
            context=[user_profile_task, parking_task, departure_task, flight_task],
            expected_output="출국 5시간 전/2시간 전 한국어 알림 메시지 템플릿",
        )

        # ========================
        # 4) Crew 실행
        # ========================
        crew = Crew(
            agents=[
                user_profile_agent,
                flight_agent,
                parking_agent,
                departure_agent,
                notification_agent,
            ],
            tasks=[
                user_profile_task,
                flight_task,
                parking_task,
                departure_task,
                notif_task,
            ],
            process=Process.sequential,
            verbose=True,
        )

        result = crew.kickoff()
        print("\n===== 최종 알림 메시지 =====\n")
        print(result)

        # 결과 JSON 저장
        def safe_output(task):
            if getattr(task, "output", None) is None:
                return None
            raw = getattr(task.output, "raw", None)
            return raw if raw is not None else str(task.output)

        # ========================
        # 5) 최적 항공편 기준 5h/2h 출국 알림 스케줄 생성
        # ========================
        user_profile_json = parse_json_safe(safe_output(user_profile_task))
        parking_json      = parse_json_safe(safe_output(parking_task))
        departure_json    = parse_json_safe(safe_output(departure_task))
        flight_json       = parse_json_safe(safe_output(flight_task))
        print("[DEBUG] flight_json after parse:", flight_json)

        try:
            best_flights = (flight_json or {}).get("best_flights", [])
            best_flight = best_flights[0] if best_flights else None
        except Exception:
            best_flight = None

        if best_flight and user_profile_json:
            dep_time_str = best_flight.get("departure_time_local")
            home_address = user_profile_json.get("home_address")
            email = user_input_hint.get("contact", {}).get("email")

            if dep_time_str and home_address and email:
                try:
                    # ISO8601 문자열 → datetime (KST 가정)
                    dep_dt_local = datetime.fromisoformat(dep_time_str)
                    if dep_dt_local.tzinfo is None:
                        dep_dt_local = dep_dt_local.replace(tzinfo=ZoneInfo("Asia/Seoul"))

                    # 5시간 전 / 2시간 전 (로컬)
                    dt_5h_local = dep_dt_local - timedelta(hours=5)
                    dt_2h_local = dep_dt_local - timedelta(hours=2)

                    # UTC로 변환 (EventBridge는 UTC 기준)
                    dt_5h_utc = dt_5h_local.astimezone(timezone.utc)
                    dt_2h_utc = dt_2h_local.astimezone(timezone.utc)

                    # Lambda에 넘길 공통 payload
                    common_payload = {
                        "type": "ICN_DEPARTURE_REMINDER",
                        "home_address": home_address,
                        "email": email,
                        "user_profile": user_profile_json,
                        "best_flight": best_flight,
                        "best_parking": (parking_json or {}).get("best_parking"),
                        "best_departure_gate": (departure_json or {}).get("best_departure_gate"),
                        "parking_link": (departure_json or {}).get("parking_link"),
                    }

                    create_departure_notification_schedule(
                        run_time_utc=dt_5h_utc,
                        tag="5h_before",
                        payload={**common_payload, "tag": "5h_before"},
                    )
                    create_departure_notification_schedule(
                        run_time_utc=dt_2h_utc,
                        tag="2h_before",
                        payload={**common_payload, "tag": "2h_before"},
                    )
                except Exception as e:
                    print(f"[WARN] 출국 알림 스케줄 생성 실패: {e}")
            else:
                print("[INFO] 출국 알림 스케줄 생성을 건너뜀 (departure_time_local/home_address/email 누락)")


        summary = {
            "user_input_hint": user_input_hint,
            "tasks": {
                "user_profile": safe_output(user_profile_task),
                "parking": safe_output(parking_task),
                "departure": safe_output(departure_task),
                "notification": safe_output(notif_task),
                "flight": safe_output(flight_task),
            },
            "final_output": str(result),
        }

        # # ========================
        # # 🔔 테스트용 수동 이메일 알림 스케줄
        # # ========================
        # print("\n=== 테스트 이메일 스케줄 설정 ===")
        # print("예: 지금 시각이 2025-12-04 10:20라면, 5분 뒤인 2025-12-04 10:25처럼 입력해봐.\n")
        #
        # test_time_str = input("테스트 이메일 알림 시각 (YYYY-MM-DD HH:MM, 비우면 스킵): ").strip()
        #
        # if test_time_str:
        #     to_email = input("알림을 받을 이메일 주소: ").strip()
        #
        #     try:
        #         # 입력한 시각을 KST(UTC+9)로 가정
        #         local_dt = datetime.strptime(test_time_str, "%Y-%m-%d %H:%M")
        #         local_dt = local_dt.replace(tzinfo=timezone(timedelta(hours=9)))
        #         run_time_utc = local_dt.astimezone(timezone.utc)
        #
        #         subject = "[테스트] 인천공항 출국 알림"
        #         body = (
        #             "이 메일은 EventBridge Scheduler → Lambda → 이메일 연동을 테스트하기 위한 메시지입니다.\n\n"
        #             f"- 요청된 발송 시각 (KST): {test_time_str}\n"
        #             f"- 출발 도시: {user_input_hint['trip']['from']}\n"
        #             f"- 도착 도시: {user_input_hint['trip']['to']}\n"
        #             f"- 출발일: {user_input_hint['trip']['departure_date']}\n"
        #         )
        #
        #         # tag는 스케줄 식별용 라벨
        #         create_email_schedule(
        #             run_time_utc=run_time_utc,
        #             to_email=to_email,
        #             subject=subject,
        #             body=body,
        #             tag="manual_test"
        #         )
        #
        #         print(f"\n[AWS] {test_time_str} (KST)에 발송될 테스트 이메일 스케줄을 생성했습니다.")
        #         print("    → 시간이 되면 agent-flight-notification Lambda가 실행되고,")
        #         print("      그 Lambda가 SNS/SES를 통해 메일을 보냅니다.\n")
        #
        #     except Exception as e:
        #         print(f"[에러] 테스트 알림 스케줄 생성 중 오류: {e}")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"airport_planner_result_{ts}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        print(f"\n[INFO] 결과 JSON 파일 저장 완료: {filename}")

        html_filename = f"airport_planner_result_{ts}.html"
        save_html_report(summary, html_filename)
        print(f"[INFO] HTML 리포트 저장 완료: {html_filename}")

        append_trip_memory(user_input_hint, summary)
        print(f"[INFO] user_memory.json에 여행 이력이 업데이트되었습니다.")

        try:
            schedule_email_alerts_from_summary(summary)
        except Exception as e:
            print(f"[WARN] 이메일 알림 스케줄 생성 중 오류 발생: {e}")

    finally:
        close_all_mcp_adapters()

def save_html_report(summary: dict, html_filename: str):
    """summary(dict)를 예쁘게 HTML 리포트로 저장"""

    def pretty_json(obj):
        try:
            return html.escape(json.dumps(obj, ensure_ascii=False, indent=2))
        except TypeError:
            return html.escape(str(obj))

    def as_dict(raw):
        """Task raw output을 dict로 파싱 (실패하면 None)."""
        data = parse_json_safe(raw)
        return data if isinstance(data, dict) else None

    def nl2br(text: str | None) -> str:
        if not text:
            return ""
        return "<br>".join(html.escape(text).splitlines())

    # ---- 원자료 꺼내기 ----
    user_hint = summary.get("user_input_hint", {})  # 이미 dict
    tasks = summary.get("tasks", {})

    user_profile_raw = tasks.get("user_profile")
    parking_raw = tasks.get("parking")
    departure_raw = tasks.get("departure")
    notification_raw = tasks.get("notification")
    flight_raw = tasks.get("flight")

    user_profile = as_dict(user_profile_raw)
    parking = as_dict(parking_raw)
    departure = as_dict(departure_raw)
    flight = as_dict(flight_raw)

    body_5h, body_2h = extract_alert_bodies(notification_raw)

    final_output = html.escape(summary.get("final_output", ""))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- 사용자 입력 요약용 필드 뽑기 ----
    trip = user_hint.get("trip", {}) or {}
    passengers = user_hint.get("passengers", {}) or {}
    contact = user_hint.get("contact", {}) or {}

    trip_from = trip.get("from", "-")
    trip_to = trip.get("to", "-")
    dep_date = trip.get("departure_date", "-")
    ret_date = trip.get("return_date") or "-"

    ad = passengers.get("adults", 0)
    ch = passengers.get("children", 0)
    inf = passengers.get("infants", 0)

    home_address = user_hint.get("home_address", "-")
    transport_pref = user_hint.get("transport_preference", "-")
    email = contact.get("email", "-")

    # ---- parking/departure/flight 필드 정리 ----
    best_parking = (parking or {}).get("best_parking") or {}
    parking_summary = (parking or {}).get("summary", "")

    best_gate = (departure or {}).get("best_departure_gate") or {}
    parking_link = (departure or {}).get("parking_link") or {}
    departure_summary = (departure or {}).get("summary", "")

    best_flights = (flight or {}).get("best_flights") or []

    user_hint_json = pretty_json(user_hint)
    user_profile_json = pretty_json(user_profile or user_profile_raw)
    parking_json = pretty_json(parking or parking_raw)
    departure_json = pretty_json(departure or departure_raw)
    notification_json = pretty_json(notification_raw)
    flight_json = pretty_json(flight or flight_raw)

    # ---- HTML 생성 ----
    html_str = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <title>인천공항 출국 플래너 결과</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #020617;
      --accent: #38bdf8;
      --accent-soft: rgba(56, 189, 248, 0.15);
      --text-main: #e5e7eb;
      --text-muted: #9ca3af;
      --border-subtle: #1f2937;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #1e293b 0, #020617 55%, #000000 100%);
      margin: 0;
      padding: 24px;
      color: var(--text-main);
    }}
    .container {{
      max-width: 1120px;
      margin: 0 auto;
    }}
    h1 {{
      margin: 0 0 4px;
      font-size: 24px;
      letter-spacing: 0.02em;
    }}
    .subtitle {{
      font-size: 13px;
      color: var(--text-muted);
      margin-bottom: 16px;
    }}
    .meta-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 20px;
      font-size: 12px;
      color: var(--text-muted);
    }}
    .meta-chip {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 8px;
      border-radius: 999px;
      border: 1px solid var(--border-subtle);
      background: rgba(15,23,42,0.8);
    }}
    .meta-dot {{
      width: 6px;
      height: 6px;
      border-radius: 999px;
      background: var(--accent);
    }}
    .grid {{
      display: grid;
      grid-template-columns: minmax(0, 2fr) minmax(0, 1.3fr);
      gap: 16px;
      align-items: flex-start;
    }}
    @media (max-width: 900px) {{
      .grid {{
        grid-template-columns: minmax(0, 1fr);
      }}
    }}
    .card {{
      background: linear-gradient(135deg, rgba(15,23,42,0.96), rgba(15,23,42,0.98));
      border-radius: 14px;
      padding: 14px 16px;
      margin-bottom: 14px;
      border: 1px solid rgba(31,41,55,0.9);
      box-shadow: 0 18px 45px rgba(0,0,0,0.45);
    }}
    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 8px;
    }}
    .card-title {{
      font-size: 15px;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .card-title span.icon {{
      width: 20px;
      height: 20px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
    }}
    .card-tag {{
      font-size: 11px;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid rgba(75,85,99,0.8);
      color: var(--text-muted);
    }}
    details {{
      margin-top: 6px;
      font-size: 12px;
    }}
    summary {{
      cursor: pointer;
      font-weight: 500;
      font-size: 12px;
      color: var(--accent);
    }}
    .field-grid {{
      display: grid;
      grid-template-columns: 90px 1fr;
      row-gap: 4px;
      column-gap: 8px;
      font-size: 13px;
    }}
    .field-label {{
      color: var(--text-muted);
    }}
    .field-value {{
      font-weight: 500;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 8px;
      font-size: 11px;
      border-radius: 999px;
      background: rgba(15,118,110,0.15);
      border: 1px solid rgba(34,197,94,0.4);
      color: #bbf7d0;
      margin-right: 6px;
    }}
    .timeline {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-top: 8px;
      padding-left: 4px;
      border-left: 2px solid rgba(55,65,81,0.8);
    }}
    .tl-item {{
      position: relative;
      padding-left: 14px;
    }}
    .tl-item::before {{
      content: "";
      position: absolute;
      left: -6px;
      top: 3px;
      width: 10px;
      height: 10px;
      border-radius: 999px;
      background: var(--accent);
      box-shadow: 0 0 0 4px var(--accent-soft);
    }}
    .tl-time {{
      font-size: 11px;
      color: var(--text-muted);
      margin-bottom: 4px;
    }}
    .tl-text {{
      font-size: 13px;
    }}
    .rank-list {{
      font-size: 13px;
      padding-left: 4px;
    }}
    .rank-item {{
      margin-bottom: 6px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>인천공항 출국 플래너 결과</h1>
    <div class="subtitle">멀티 에이전트 &amp; MCP 기반 ICN 출국 동선 플래너</div>
    <div class="meta-bar">
      <div class="meta-chip">
        <span class="meta-dot"></span>
        생성 시각: {now_str}
      </div>
      <div class="meta-chip">
        ✈️  사용자 입력 &amp; 에이전트 결과 요약
      </div>
    </div>

    <div class="grid">
      <div>
        <!-- 1. 사용자 입력 요약 -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">
              <span class="icon">1</span>
              사용자 입력 요약
            </div>
            <div class="card-tag">콘솔 입력</div>
          </div>
          <div class="field-grid">
            <div class="field-label">출발지</div>
            <div class="field-value">{html.escape(trip_from)}</div>
            <div class="field-label">도착지</div>
            <div class="field-value">{html.escape(trip_to)}</div>
            <div class="field-label">출발일</div>
            <div class="field-value">{html.escape(dep_date)}</div>
            <div class="field-label">귀국일</div>
            <div class="field-value">{html.escape(ret_date)}</div>
            <div class="field-label">인원</div>
            <div class="field-value">성인 {ad} · 어린이 {ch} · 유아 {inf}</div>
            <div class="field-label">출발 주소</div>
            <div class="field-value">{html.escape(home_address)}</div>
            <div class="field-label">이동 수단</div>
            <div class="field-value">{html.escape(transport_pref)}</div>
            <div class="field-label">이메일</div>
            <div class="field-value">{html.escape(email)}</div>
          </div>
          <details>
            <summary>원본 입력 JSON 보기</summary>
            <pre>{user_hint_json}</pre>
          </details>
        </div>

        <!-- 2. 사용자 프로필 -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">
              <span class="icon">2</span>
              사용자 프로필 (user_profile_task)
            </div>
            <div class="card-tag">LLM 정제 프로필</div>
          </div>
          <div class="field-grid">
            <div class="field-label">출발지</div>
            <div class="field-value">{html.escape((user_profile or {}).get("trip", {}).get("from", trip_from))}</div>
            <div class="field-label">도착지</div>
            <div class="field-value">{html.escape((user_profile or {}).get("trip", {}).get("to", trip_to))}</div>
            <div class="field-label">출발일</div>
            <div class="field-value">{html.escape((user_profile or {}).get("trip", {}).get("departure_date", dep_date))}</div>
            <div class="field-label">귀국일</div>
            <div class="field-value">{html.escape((user_profile or {}).get("trip", {}).get("return_date", ret_date or "-"))}</div>
          </div>
          <details>
            <summary>원본 JSON 보기</summary>
            <pre>{user_profile_json}</pre>
          </details>
        </div>

        <!-- 3. 주차장 추천 -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">
              <span class="icon">3</span>
              주차장 추천 결과 (parking_task)
            </div>
            <div class="card-tag">best_parking</div>
          </div>
          <div style="font-size:13px; margin-bottom:6px;">
            {html.escape(parking_summary or "")}
          </div>
          <div class="field-grid">
            <div class="field-label">이름</div>
            <div class="field-value">{html.escape(best_parking.get("name_ko", "-"))}</div>
            <div class="field-label">코드</div>
            <div class="field-value">{html.escape(best_parking.get("code", "-"))}</div>
            <div class="field-label">종류</div>
            <div class="field-value">{html.escape(best_parking.get("type", "-"))}</div>
            <div class="field-label">터미널</div>
            <div class="field-value">{html.escape(best_parking.get("terminal", "-"))}</div>
            <div class="field-label">혼잡도</div>
            <div class="field-value">{html.escape(best_parking.get("expected_congestion", "-"))}</div>
          </div>
          <details>
            <summary>원본 JSON 보기</summary>
            <pre>{parking_json}</pre>
          </details>
        </div>

        <!-- 4. 출국장 추천 -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">
              <span class="icon">4</span>
              출국장 추천 결과 (departure_task)
            </div>
            <div class="card-tag">best_departure_gate + parking_link</div>
          </div>
          <div style="font-size:13px; margin-bottom:6px;">
            {html.escape(departure_summary or "")}
          </div>
          <div class="field-grid">
            <div class="field-label">터미널</div>
            <div class="field-value">{html.escape(best_gate.get("terminal", "-"))}</div>
            <div class="field-label">출국장</div>
            <div class="field-value">{html.escape(best_gate.get("gate_id", "-"))}</div>
            <div class="field-label">보안검색</div>
            <div class="field-value">{html.escape(best_gate.get("security_lane", "-"))}</div>
            <div class="field-label">예상 대기</div>
            <div class="field-value">{best_gate.get("estimated_wait_minutes", "-")} 분</div>
            <div class="field-label">주차장</div>
            <div class="field-value">{html.escape(parking_link.get("parking_name_ko", "-"))}</div>
            <div class="field-label">도보 시간</div>
            <div class="field-value">{parking_link.get("walking_time_from_parking_minutes", "-")} 분</div>
          </div>
          <details>
            <summary>원본 JSON 보기</summary>
            <pre>{departure_json}</pre>
          </details>
        </div>
      </div>

      <div>
        <!-- 알림 타임라인 -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">
              <span class="icon">A</span>
              출국 알림 타임라인
            </div>
            <div class="card-tag">5시간 전 · 2시간 전</div>
          </div>
          <div class="timeline">
            <div class="tl-item">
              <div class="tl-time">T - 5h 알림 (집 출발 가이드)</div>
              <div class="tl-text">{nl2br(body_5h or "5시간 전 알림 메시지를 생성하지 못했습니다.")}</div>
            </div>
            <div class="tl-item">
              <div class="tl-time">T - 2h 알림 (공항 내 동선 가이드)</div>
              <div class="tl-text">{nl2br(body_2h or "2시간 전 알림 메시지를 생성하지 못했습니다.")}</div>
            </div>
          </div>
          <details>
            <summary>알림 원문 전체 보기</summary>
            <pre>{notification_json}</pre>
          </details>
        </div>

        <!-- 항공편 추천 -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">
              <span class="icon">5</span>
              항공편 추천 결과 (flight_task)
            </div>
            <div class="card-tag">best_flights</div>
          </div>
          <div class="rank-list">
"""

    # 항공편 목록 1위/2위/3위 렌더링
    for idx, fitem in enumerate(best_flights, start=1):
        airline = html.escape(str(fitem.get("airline", "-")))
        fno = html.escape(str(fitem.get("flight_number", "-")))
        dep_air = html.escape(str(fitem.get("departure_airport", "-")))
        arr_air = html.escape(str(fitem.get("arrival_airport", "-")))
        dep_t = html.escape(str(fitem.get("departure_time_local", "-")))
        arr_t = html.escape(str(fitem.get("arrival_time_local", "-")))
        price = fitem.get("price_total", "-")
        nonstop = "직항" if fitem.get("is_nonstop") else "경유"

        html_str += f"""
            <div class="rank-item">
              <strong>{idx}위.</strong> {airline} {fno} ({nonstop})<br>
              &nbsp;&nbsp;출발: {dep_air} {dep_t}<br>
              &nbsp;&nbsp;도착: {arr_air} {arr_t}<br>
              &nbsp;&nbsp;예상 요금: {price}
            </div>
"""

    if not best_flights:
        html_str += """
            <div class="rank-item">추천 항공편 정보를 찾지 못했습니다.</div>
"""

    html_str += f"""
          </div>
          <details>
            <summary>원본 JSON 보기</summary>
            <pre>{flight_json}</pre>
          </details>
        </div>

        <!-- Crew 최종 출력 -->
        <div class="card">
          <div class="card-header">
            <div class="card-title">
              <span class="icon">★</span>
              Crew 최종 출력
            </div>
            <div class="card-tag">LLM 종합 요약</div>
          </div>
          <details>
            <summary>펼쳐보기</summary>
            <pre>{final_output}</pre>
          </details>
        </div>
      </div>
    </div>
  </div>
</body>
</html>
"""

    with open(html_filename, "w", encoding="utf-8") as f:
        f.write(html_str)


if __name__ == "__main__":
    run_airport_multi_agent()
