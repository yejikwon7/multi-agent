# agents/notification_agent.py
from crewai import Agent
from config import worker_llm
from tools.tmap_tool import TmapTrafficTool

def create_notification_agent():
    """
    4번 에이전트: 사용자 프로필 + 주차장 + 출국장 + 교통량 정보를 종합해서
    '출국 5시간 전 / 2시간 전'에 어떤 내용을 알려줄지 메시지를 만드는 역할.
    실제 스케줄링(5시간/2시간 전 트리거)은 외부에서 cron, lambda, airflow 등으로 처리하고,
    이 에이전트는 "지금 시각 기준"으로 보낼 메시지를 생성한다고 보면 됨.
    """
    tmap_tool = TmapTrafficTool()

    return Agent(
        role="출국 알림 에이전트",
        goal=(
            "사용자의 집-인천공항 교통 정보(Tmap), 추천 주차장/출국장 정보를 종합해 "
            "출국 5시간 전과 2시간 전에 보낼 알림 메시지를 생성한다. "
            "메시지는 한국어로, 핵심 정보(몇 시에 집을 나가야 하는지, 어느 주차장/출국장으로 가야 하는지)를 "
            "명확하게 포함해야 한다."
        ),
        backstory=(
            "당신은 사용자가 항공편에 지각하지 않도록 돕는 스마트 알림 비서이다. "
            "교통량, 공항 혼잡도, 개인 선호를 모두 고려해서 안전한 출발 시각과 동선을 안내한다."
        ),
        llm=worker_llm,
        tools=[tmap_tool],   # 필요 시 flight/icn MCP 도 같이 넣어도 됨
        verbose=True,
        allow_delegation=False,
    )
