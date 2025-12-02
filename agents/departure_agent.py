# agents/departure_agent.py
from crewai import Agent
from config import worker_llm

def create_departure_agent(departure_tools):
    """
    3번 에이전트: 출국장/보안 검색대 혼잡도, 대기 시간,
    터미널 정보 등을 조회하여 '어느 출국장으로 가야 할지' 추천.
    이때 주차장 에이전트가 찾아준 주차장 정보도 함께 고려.
    """
    return Agent(
        role="인천공항 출국장 추천 에이전트",
        goal=(
            "icn-mcp의 출국장/보안 검색 관련 도구를 사용하여 "
            "사용자 출발 시간 기준으로 가장 한가하고 동선이 좋은 출국장을 추천한다. "
            "가능하면 추천 주차장과의 거리도 함께 고려한다."
        ),
        backstory=(
            "당신은 공항 운영 데이터를 잘 아는 컨시어지이다. "
            "실시간 혼잡도, 대기 시간, 터미널/출국장 위치를 종합해 최적의 출국 루트를 제안한다."
        ),
        llm=worker_llm,
        tools=departure_tools,
        verbose=True,
        allow_delegation=False,
        memory=True,
    )
