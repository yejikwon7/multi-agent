from crewai import Agent
from config import worker_llm

def create_parking_agent(parking_tools):
    """
    2번 에이전트: icn-mcp의 '주차장 관련 툴'을 사용해서
    현재/예상 주차 가능 대수, 한가한 주차장 구역 등을 조회.
    """
    description = (
        "인천공항 주차장의 실시간/예상 혼잡도를 조회하고, "
        "사용자가 출국하는 시점에 가장 여유 있는 주차장을 추천한다. "
        "입력으로 사용자 프로필(출발 시각, 터미널, 장기/단기 주차 선호 등)을 사용한다."
    )

    return Agent(
        role="인천공항 주차장 정보 에이전트",
        goal=(
            "icn-mcp에서 제공되는 주차장 관련 도구를 활용해 "
            "출국 시간 기준으로 가장 여유 있고 동선이 좋은 주차장을 추천한다."
        ),
        backstory=description,
        llm=worker_llm,
        tools=[],
        verbose=True,
        allow_delegation=False,
    )
