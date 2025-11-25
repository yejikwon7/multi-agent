from crewai import Agent
from config import worker_llm

def create_user_profile_agent():
    """
    1번 에이전트: 사용자에게 여행지/출발일/인원/좌석 등 조건을 질문하고
    구조화된 JSON 형태로 사용자 프로필을 만드는 역할.
    이 프로필은 다른 에이전트에게 공유됨.
    """
    return Agent(
        role="사용자 정보 학습 에이전트",
        goal=(
            "대화형으로 사용자의 여행 계획과 선호(출발일, 목적지, 동행인 수, 좌석 등급, "
            "주차 여부, 대중교통 선호도 등)를 질문해 구조화된 JSON 프로필로 정리하고, "
            "다른 에이전트가 그대로 활용할 수 있게 깔끔하게 제공한다."
        ),
        backstory=(
            "당신은 여행 컨시어지 시스템의 인입창구이다. "
            "사용자의 요구를 최대한 놓치지 않고 캡처해 다른 에이전트에게 넘겨주는 역할을 맡고 있다."
        ),
        llm=worker_llm,
        tools=[],            # 외부 툴 필요 없음 (純대화)
        verbose=True,
        allow_delegation=False,
        memory=True,         # 사용자 정보 기억
    )
