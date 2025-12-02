import json
from crewai import Crew, Process, Task
from datetime import datetime
import html
import os

from config import manager_llm, worker_llm
from tools.mcp_loader import load_flight_mcp_tools, close_all_mcp_adapters
from tools.tmap_tool import TmapTrafficTool

from agents.user_profile_agent import create_user_profile_agent
from agents.parking_agent import create_parking_agent
from agents.departure_agent import create_departure_agent
from agents.notification_agent import create_notification_agent
from agents.flight_agent import create_flight_agent

MEMORY_FILE = "user_memory.json"

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
        home_address = input("거주지/출발지 (예: 서울시 강남구 대치동): ").strip() or "서울시 강남구 대치동"

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
                "실제로 툴(search_flight_offers 등)을 호출해라.\n\n"
                "다음은 사용자 프로필이다. 이를 바탕으로 최적 항공편을 추천해라.\n\n"
                "### 사용자 프로필(JSON)\n"
                "{{user_profile}}\n\n"
                "icn-mcp 항공편 관련 MCP 툴(search_flight_offers, discover_flights)을 사용하여 "
                "출발일, 목적지, 인원 수에 맞는 항공편 목록을 조회하고, "
                "가장 적합한 항공편 1~3개를 추천하라.\n\n"
                "추천 기준:\n"
                "- 가격\n"
                "- 직항 여부\n"
                "- 총 소요시간\n"
                "- 출발/도착 시간의 편리성\n\n"
                "최종 출력은 요약 설명 + JSON 구조(항공편 코드, 가격, 경유 정보)를 포함해야 한다."
            ),
            agent=flight_agent,
            context=[user_profile_task],
            expected_output="추천 항공편 요약 + JSON"
        )

        parking_task = Task(
            description=(
                "다음은 사용자 프로필이다. 이를 기반으로 인천공항 주차장을 추천해라.\n\n"
                "### 사용자 프로필(JSON)\n"
                "{{user_profile}}\n\n"
                "icn-mcp의 주차장 관련 툴(get_parking_status)을 최소 1회 시도하여 "
                "가장 여유 있는 주차장과, 추천 이유(위치/혼잡도/동선)를 한국어로 정리해라. "
                "최종 결과는 요약 텍스트 + 간단한 JSON 구조(추천 주차장 코드/이름/예상 혼잡도)를 함께 반환해라.\n\n"
                "만약 MCP 툴 호출이 실패하면, 실시간 정보는 사용할 수 없다고 가정하고 "
                "일반적인 인천공항 주차장 특성을 기반으로 최선의 추론을 하라. "
                "툴을 한 번도 호출하지 않은 상태에서 바로 일반적인 특성을 말하면 안 된다."
            ),
            agent=parking_agent,
            context=[user_profile_task],
            expected_output="추천 주차장 요약 + JSON",
        )

        departure_task = Task(
            description=(
                "다음은 사용자 프로필과 주차장 추천 결과이다.\n\n"
                "### 사용자 프로필(JSON)\n"
                "{{user_profile}}\n\n"
                "### 주차장 추천 결과\n"
                "{{parking_result}}\n\n"
                "icn-mcp의 출국장/보안 검색 관련 툴을 사용하여, "
                "가장 한가하고 동선이 좋은 출국장을 추천하고, "
                "예상 대기시간과 추천 이유를 한국어로 정리해라. "
                "최종 결과는 요약 텍스트 + 간단한 JSON(추천 출국장 ID/터미널/예상 대기시간)을 함께 반환해라.\n\n"
                "만약 MCP 툴 호출이 실패하면, 실시간 혼잡도 정보 없이 "
                "일반적인 인천공항 출국장 특성을 기반으로 최선의 추론을 하라."
            ),
            agent=departure_agent,
            context=[user_profile_task, parking_task],
            expected_output="추천 출국장 요약 + JSON",
        )

        notif_task = Task(
            description=(
                "당신은 출국 알림 에이전트이다.\n\n"
                "다음은 지금까지의 정보이다.\n\n"
                "### 사용자 프로필(JSON)\n"
                "{{user_profile}}\n\n"
                "### 주차장 추천 결과\n"
                "{{parking_result}}\n\n"
                "### 출국장 추천 결과\n"
                "{{departure_result}}\n\n"
                "위 정보를 바탕으로, 출국 5시간 전 알림과 2시간 전 알림을 각각 설계하라.\n\n"
                "⚠️ 중요: MCP 툴이 연결되어 있다면, 실제로 툴을 호출해야 한다. "
                "'Simulated Tool Call'처럼 툴 호출을 흉내 내는 텍스트나 가짜 JSON을 쓰지 말고, "
                "CrewAI 도구 호출 메커니즘을 사용해라.\n\n"
                "1) 5시간 전 알림 (교통/출발 시각/주차 중심)\n"
                "tmap_traffic을 호출하여 "
                "사용자의 집 주소에서 인천공항까지의 예상 이동 시간과 교통 상황을 조회하라.\n"
                "- MCP 툴이 실패하면, 일반적인 서울→인천공항 자가용 이동 시간(약 1~1.5시간)에 "
                "주차·수속 여유 시간을 더해 안전한 '집 출발 권장 시각'을 계산하라.\n"
                "- 5시간 전 알림 메시지에는 다음을 포함하라.\n"
                "  * 권장 집 출발 시각\n"
                "  * 추천 이동 수단(자가용/대중교통 등)과 이유\n"
                "  * 도착 후 사용할 주차장(예: 제1여객터미널 장기주차장 P4) 안내\n"
                "  * 출국장은 한 줄 정도로만 간단히 언급(예: '출국은 6번 게이트를 이용하시면 됩니다.')\n\n"
                "2) 2시간 전 알림 (출국장/대기시간/터미널 내 동선 중심)\n"
                "- 이 시점에는 사용자가 이미 공항에 있거나 거의 도착한 상황을 가정하라.\n"
                "- 출국장 추천 결과를 기준으로, 어느 층/어느 게이트로 이동해야 하는지, "
                "보안 검색 예상 대기시간이 얼마인지, 유아동반/교통약자 레인이 있는지 등 "
                "터미널 내부 동선과 수속 전략에 집중하여 안내하라.\n"
                "- 교통 상황/집 출발 시각은 한두 문장 정도로만 짧게 언급(예: "
                "'아직 출발하지 않으셨다면 매우 촉박합니다' 수준)하고, "
                "메시지의 초점은 출국장/동선/대기시간에 두어라.\n"
                "- 2시간 전 알림 메시지에는 최소한 다음을 포함하라.\n"
                "  * 어느 주차장에 주차하고 어디로 이동해야 하는지(층/게이트)\n"
                "  * 추천 출국장/보안검색대, 예상 대기시간\n"
                "  * 유아/어린이 동반 시 이용할 수 있는 우선 레인 등 편의 정보\n"
                "  * 지금 시점에서 해야 할 행동(예: 체크인 카운터 이동, 보안 검색대 진입 등)\n\n"
                "최종 출력 형식은 다음을 따르라.\n"
                "### 5시간 전 알림\n"
                "- 메시지 본문...\n\n"
                "### 2시간 전 알림\n"
                "- 메시지 본문...\n"
            ),
            agent=notification_agent,
            context=[user_profile_task, parking_task, departure_task],
            expected_output="출국 5시간 전/2시간 전 한국어 알림 메시지",
        )

        # ========================
        # 4) Crew 실행
        # ========================
        crew = Crew(
            agents=[
                user_profile_agent,
                parking_agent,
                departure_agent,
                notification_agent,
                flight_agent,
            ],
            tasks=[
                user_profile_task,
                parking_task,
                departure_task,
                notif_task,
                flight_task,
            ],
            process=Process.sequential,
            # manager_llm=manager_llm,
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

    finally:
        # ✅ 여기서 MCP 어댑터 정리 (이벤트 루프 정상 종료)
        close_all_mcp_adapters()

def save_html_report(summary: dict, html_filename: str):
    """summary(dict)를 예쁘게 HTML 리포트로 저장"""

    def pretty_json(obj):
        try:
            return html.escape(json.dumps(obj, ensure_ascii=False, indent=2))
        except TypeError:
            # json 직렬화 안 되는 경우 방어코드
            return html.escape(str(obj))

    user_hint = pretty_json(summary.get("user_input_hint"))
    tasks = summary.get("tasks", {})

    user_profile = pretty_json(tasks.get("user_profile"))
    parking      = pretty_json(tasks.get("parking"))
    departure    = pretty_json(tasks.get("departure"))
    notification = pretty_json(tasks.get("notification"))
    flight       = pretty_json(tasks.get("flight"))

    final_output = html.escape(summary.get("final_output", ""))

    html_str = f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <title>인천공항 출국 플래너 결과</title>
  <style>
    body {{
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background-color: #f5f5f7;
      margin: 0;
      padding: 24px;
    }}
    h1 {{
      margin-top: 0;
    }}
    .container {{
      max-width: 1080px;
      margin: 0 auto;
    }}
    .card {{
      background: #ffffff;
      border-radius: 12px;
      padding: 16px 20px;
      margin-bottom: 16px;
      box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }}
    .card h2 {{
      margin-top: 0;
      font-size: 18px;
      border-bottom: 1px solid #eee;
      padding-bottom: 6px;
    }}
    .meta {{
      font-size: 13px;
      color: #777;
      margin-bottom: 12px;
    }}
    pre {{
      background: #111827;
      color: #e5e7eb;
      padding: 12px 14px;
      border-radius: 8px;
      overflow-x: auto;
      font-size: 13px;
      line-height: 1.5;
    }}
    details {{
      margin-top: 6px;
    }}
    summary {{
      cursor: pointer;
      font-weight: 500;
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>인천공항 출국 플래너 결과</h1>
    <div class="meta">생성 시각: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>

    <div class="card">
      <h2>1. 사용자 입력 힌트 (콘솔 입력)</h2>
      <pre>{user_hint}</pre>
    </div>

    <div class="card">
      <h2>2. 사용자 프로필 (user_profile_task)</h2>
      <pre>{user_profile}</pre>
    </div>

    <div class="card">
      <h2>3. 주차장 추천 결과 (parking_task)</h2>
      <pre>{parking}</pre>
    </div>

    <div class="card">
      <h2>4. 출국장 추천 결과 (departure_task)</h2>
      <pre>{departure}</pre>
    </div>

    <div class="card">
      <h2>5. 출국 알림 메시지 (notif_task)</h2>
      <pre>{notification}</pre>
    </div>

    <div class="card">
      <h2>6. 항공편 추천 결과 (flight_task)</h2>
      <pre>{flight}</pre>
    </div>

    <div class="card">
      <h2>7. Crew 최종 출력</h2>
      <details>
        <summary>펼쳐보기</summary>
        <pre>{final_output}</pre>
      </details>
    </div>
  </div>
</body>
</html>
"""

    with open(html_filename, "w", encoding="utf-8") as f:
        f.write(html_str)


if __name__ == "__main__":
    run_airport_multi_agent()
