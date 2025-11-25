import json
from crewai import Crew, Process, Task
import os
from datetime import datetime

from config import manager_llm, worker_llm
from crewai_tools import MCPServerAdapter
from tools.mcp_loader import load_flight_mcp_tools

from agents.user_profile_agent import create_user_profile_agent
from agents.parking_agent import create_parking_agent
from agents.departure_agent import create_departure_agent
from agents.notification_agent import create_notification_agent
from agents.flight_agent import create_flight_agent


def run_airport_multi_agent():
    # ========================
    # 0) 콘솔에서 사용자 입력 받기
    # ========================
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
    # 1) MCP 서버 연결 (★여기부터가 핵심★)
    # ========================
    TRANSPORT = "streamable-http"

    icn_url     = os.getenv("ICN_MCP_URL")
    flight_url  = os.getenv("FLIGHT_MCP_URL")
    fli_url     = os.getenv("FLI_MCP_URL")
    amadeus_url = os.getenv("AMADEUS_MCP_URL")

    # 여러 MCP 서버를 동시에 열어두고, 그 안에서 Crew 전체를 실행
    with (
        MCPServerAdapter({"url": icn_url, "transport": TRANSPORT}) as icn_tools,
        MCPServerAdapter({"url": flight_url, "transport": TRANSPORT}) as flight_tools,
        MCPServerAdapter({"url": fli_url, "transport": TRANSPORT}) as fli_tools,
        MCPServerAdapter({"url": amadeus_url, "transport": TRANSPORT}) as amadeus_tools,
    ):
        # generator일 수 있으니 리스트로 고정
        icn_tools     = list(icn_tools) if icn_tools else []
        flight_tools  = list(flight_tools) if flight_tools else []
        fli_tools     = list(fli_tools) if fli_tools else []
        amadeus_tools = list(amadeus_tools) if amadeus_tools else []

        # ========== 1-1) 목적별 툴 분류 ==========
        parking_tools   = []
        departure_tools = []
        flight_tools_all = []  # flight MCP + fli MCP + amadeus MCP까지 한 번에 보고 싶으면

        all_tools = icn_tools + flight_tools + fli_tools + amadeus_tools

        for tool in all_tools:
            n = tool.name.lower()
            # 주차장
            if "parking" in n or "park" in n or "lot" in n:
                parking_tools.append(tool)
            # 출국장/보안/터미널
            if "departure" in n or "security" in n or "terminal" in n or "gate" in n:
                departure_tools.append(tool)
            # 항공편/스케줄/상태
            if "flight" in n or "schedule" in n or "status" in n or "fli" in n:
                flight_tools_all.append(tool)
            # (필요하면 amadeus 전용 분류도 추가 가능)

        print("\n[MCP] 목적별 툴 분류 결과")
        print("  - parking_tools :", [t.name for t in parking_tools])
        print("  - departure_tools:", [t.name for t in departure_tools])
        print("  - flight_tools   :", [t.name for t in flight_tools_all])

        # ========================
        # 2) 에이전트 생성 (기존 그대로, tools에 위에서 분류한 것 사용)
        # ========================
        user_profile_agent = create_user_profile_agent()
        parking_agent      = create_parking_agent(parking_tools)
        departure_agent    = create_departure_agent(departure_tools)
        notification_agent = create_notification_agent()
        flight_agent       = create_flight_agent(flight_tools_all)

        # ========================
        # 3) Task 정의 (★여기는 네가 올린 코드 그대로 사용★)
        # ========================

        # (1) 사용자 프로필 Task
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

        # (2) 항공편 추천 Task
        flight_task = Task(
            description=(
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

        # (3) 주차장 Task
        parking_task = Task(
            description=(
                "다음은 사용자 프로필이다. 이를 기반으로 인천공항 주차장을 추천해라.\n\n"
                "### 사용자 프로필(JSON)\n"
                "{{user_profile}}\n\n"
                "icn-mcp의 주차장 관련 툴(get_parking_status)을 **반드시 먼저 호출**하여 "
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

        # (4) 출국장 Task
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

        # (5) 알림 Task
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
                "위 정보를 참고하여, 현재 시각 기준으로 '출국 5시간 전'과 '2시간 전'에 "
                "사용자에게 보낼 한국어 알림 메시지를 각각 만들어라.\n\n"
                "- 필요 시 tmap_traffic 툴을 사용하여, 사용자의 집 주소에서 인천공항까지의 "
                "예상 이동 시간과 교통 상황을 조회해도 된다.\n"
                "- MCP 툴 호출이 실패하면, 일반적인 서울→인천공항 자가용 이동 시간(약 1~1.5시간)을 "
                "기준으로 안전 마진을 두고 출발 시각을 제안하라.\n"
                "- 알림 메시지에는 최소한 다음 정보가 포함되어야 한다.\n"
                "  * 몇 시까지 집을 출발해야 안전한지 (5시간 전 알림, 2시간 전 알림 각각)\n"
                "  * 추천 이동 수단(자가용/대중교통 등)과 이유\n"
                "  * 공항 도착 후 어느 주차장, 어느 출국장으로 가야 하는지\n"
                "  * 예상 교통 혼잡/출국장 대기 상황에 대한 간단한 설명\n\n"
                "최종 출력은 다음 형식을 권장한다.\n"
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
            # manager_llm=manager_llm,  # 필요하면 다시 활성화
            verbose=True,
        )

        # 🔹 1) Crew 실행
        result = crew.kickoff()

        print("\n===== 최종 알림 메시지 =====\n")
        print(result)

        # 🔹 2) 각 Task의 결과를 한 번에 JSON으로 정리
        def safe_output(task):
            # CrewAI 버전에 따라 .output 이면 TextOutput, 없으면 None일 수도 있어서 방어적으로
            if getattr(task, "output", None) is None:
                return None
            # text 계열이면 .raw, 아니면 그냥 str()
            raw = getattr(task.output, "raw", None)
            return raw if raw is not None else str(task.output)

        summary = {
            "user_input_hint": user_input_hint,  # 콘솔에서 받은 원본 힌트
            "tasks": {
                "user_profile": safe_output(user_profile_task),  # 사용자 프로필 JSON
                "parking": safe_output(parking_task),  # 주차장 추천 결과
                "departure": safe_output(departure_task),  # 출국장 추천 결과
                "notification": safe_output(notif_task),  # 5시간/2시간 전 알림
                "flight": safe_output(flight_task),  # 항공편 추천 결과
            },
            "final_output": str(result),  # crew.kickoff() 최종 결과 텍스트
        }

        # 🔹 3) 파일명 만들어서 저장
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"airport_planner_result_{ts}.json"

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

        print(f"\n[INFO] 결과 JSON 파일 저장 완료: {filename}")



if __name__ == "__main__":
    run_airport_multi_agent()
