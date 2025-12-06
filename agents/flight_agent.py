from crewai import Agent
from config import worker_llm

def create_flight_agent(flight_tools):
    """
    최적 항공편 추천 에이전트.
    사용자의 여행 프로필(출발지, 목적지, 날짜 등)을 기반으로
    MCP의 항공편 검색 도구(search_flight_offers / discover_flights 등)를 사용하여
    가장 적절한 항공편 옵션을 추천한다.
    """
    return Agent(
        role="최적 항공편 추천 에이전트",
        goal=(
            "사용자의 여행 일자, 출발지, 목적지, 인원 정보를 기반으로 "
            "최적의 항공편 후보(가격, 경유 여부, 소요시간 등)를 추천한다. "
            "icn-mcp 항공편 관련 툴(search_flight_offers 등)을 적극 활용하라."
        ),
        backstory=(
            "당신은 국제선 항공권 검색 전문가이다. "
            "항공사별 운항 스케줄, 가격, 경유 정보를 분석하여 최적 항공편을 제안한다."
        ),
        llm=worker_llm,
        tools=flight_tools,
        verbose=True,
        allow_delegation=False,
        memory=True,
    )
