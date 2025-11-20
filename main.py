# main.py
import warnings
from datetime import datetime
from crewai import Task, Crew, Process, Agent
from config import manager_llm
from tools.mcp_loader import load_flight_mcp_tools
from agents.user_profile_agent import create_user_profile_agent
from agents.parking_agent import create_parking_agent
from agents.departure_agent import create_departure_agent
from agents.notification_agent import create_notification_agent

warnings.filterwarnings("ignore")


def build_agents_and_tasks():
    # 1) MCP 툴 로드
    tool_groups = load_flight_mcp_tools()

    # 2) 에이전트 구성
    user_agent = create_user_profile_agent()
    parking_agent = create_parking_agent(tool_groups["parking"])
    departure_agent = create_departure_agent(tool_groups["departure"])
    notify_agent = create_notification_agent()

    # 3) 매니저(오케스트레이터) 에이전트
    manager_agent = Agent(
        role="여행 컨시어지 매니저",
        goal=(
            "사용자의 항공편 출국 여정을 전반적으로 관리한다. "
            "사용자 정보 수집 → 주차장/출국장 추천 → 알림 메시지 생성을 "
            "순서대로 조율하고, 결과를 일관된 형태로 정리해준다."
        ),
        backstory=(
            "당신은 멀티 에이전트 시스템의 총괄 매니저이다. "
            "각 에이전트가 제 역할을 잘 할 수 있도록 지시하고 결과를 취합한다."
        ),
        llm=manager_llm,
        verbose=True,
        allow_delegation=True,
    )

    # 4) Task 정의

    # (1) 사용자 프로필 생성
    profile_task = Task(
        description=(
            "사용자에게 자연스럽게 질문을 던져 여행 정보를 수집하고, "
            "아래 항목을 반드시 포함하는 JSON을 만들어라.\n"
            "필수 필드: origin_address, departure_date, departure_time, "
            "destination, adults, children, seat_class, "
            "needs_parking(true/false), prefers_public_transport(true/false), "
            "airline, flight_number(가능하면), terminal(알고 있으면).\n"
            "마지막에는 반드시 JSON만 출력하라."
        ),
        expected_output="사용자 여행 프로필 JSON",
        agent=user_agent,
    )

    # (2) 주차장 정보 조회
    parking_task = Task(
        description=(
            "사용자 프로필(JSON)을 입력으로 받아, "
            "icn-mcp 주차장 관련 도구를 사용해 출국 시점 기준으로 "
            "가장 여유 있는 주차장을 1~2곳 추천하라. "
            "주차장 이름, 위치(간단 설명), 현재/예상 혼잡도, 장기/단기 구분을 포함해라. "
            "출력은 요약 설명 + machine-readable JSON 둘 다 제공해라."
        ),
        expected_output="추천 주차장 리스트(자연어 설명 + JSON)",
        agent=parking_agent,
        context=[profile_task],
    )

    # (3) 출국장 정보 조회 + 추천
    departure_task = Task(
        description=(
            "사용자 프로필과 추천 주차장 정보를 바탕으로, "
            "icn-mcp 출국장/보안검색 관련 도구를 사용해 "
            "가장 한가하고 동선이 좋은 출국장을 1곳 추천하라. "
            "추천 근거로 현재/예상 대기시간, 해당 출국장이 연결된 터미널, "
            "추천 주차장과의 거리/편의성 등을 제시하라. "
            "출력은 자연어 설명 + JSON(추천 출국장, 예상 대기시간 등) 형식으로."
        ),
        expected_output="추천 출국장 정보(자연어+JSON)",
        agent=departure_agent,
        context=[profile_task, parking_task],
    )

    # (4) 출국 알림 메시지 생성
    notify_task = Task(
        description=(
            "사용자 프로필, 추천 주차장, 추천 출국장 정보를 입력으로 사용하라. "
            "또한 Tmap 교통량 툴을 사용하여 사용자의 집에서 인천공항까지 "
            "출발 시각 기준 교통 상황과 예상 소요 시간을 조회하라.\n\n"
            "1) '출국 5시간 전'에 보낼 알림 메시지와\n"
            "2) '출국 2시간 전'에 보낼 알림 메시지를 각각 작성하라.\n\n"
            "각 메시지는 다음을 포함해야 한다:\n"
            "- 몇 시까지 집에서 출발하는 것이 좋은지(여유 + 버퍼 포함)\n"
            "- 추천 주차장 이름과 간단 설명\n"
            "- 추천 출국장 번호/위치와 예상 대기시간\n"
            "- 교통 상황 요약\n\n"
            "출력 형식:\n"
            "{\n"
            '  \"five_hours_before\": \"...메시지...\",\n'
            '  \"two_hours_before\": \"...메시지...\"\n'
            "}\n"
        ),
        expected_output="5시간 전 / 2시간 전 알림 메시지 JSON",
        agent=notify_agent,
        context=[profile_task, parking_task, departure_task],
    )

    return {
        "agents": {
            "user": user_agent,
            "parking": parking_agent,
            "departure": departure_agent,
            "notify": notify_agent,
            "manager": manager_agent,
        },
        "tasks": [profile_task, parking_task, departure_task, notify_task],
        "manager": manager_agent,
    }


def run_crew():
    setup = build_agents_and_tasks()
    manager_agent = setup["manager"]
    tasks = setup["tasks"]
    agents = list(setup["agents"].values())  # 중복 제거

    crew = Crew(
        agents=agents,
        tasks=tasks,
        process=Process.hierarchical,
        manager_agent=manager_agent,
        verbose=True,
    )

    print("\n" + "=" * 40)
    print("✈️  인천공항 멀티 에이전트 여행 컨시어지 실행")
    print("=" * 40 + "\n")

    result = crew.kickoff()
    print("\n\n===== 최종 결과 =====")
    print(result)


if __name__ == "__main__":
    run_crew()
