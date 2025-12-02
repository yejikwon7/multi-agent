from __future__ import annotations

from crewai import Agent
from config import worker_llm
from tools.tmap_tool import TmapTrafficTool


def create_notification_agent(transport_tools) -> Agent:
    """
    4번 에이전트: 출국 알림 에이전트

    - 이 에이전트는 다음 네 가지 정보를 종합해서
      "지금 시점에 사용자에게 어떤 알림 문구를 보내야 하는지"를 생성한다.
        1) 사용자 프로필 (출발 시간, 동행인 수, 교통수단 선호 등)
        2) 주차장 에이전트 결과 (추천 주차장, 혼잡도 등)
        3) 출국장 에이전트 결과 (추천 출국장, 예상 대기 시간 등)
        4) Tmap 교통 정보 (집 → 인천공항 이동 시간/거리/교통 상황)

    - ⚠ 주의:
      1), 2), 3) 은 "툴"이 아니라 **이전 Task 결과**로 제공된다.
      즉, Crew를 구성할 때 Task description/context에
      JSON이나 요약 텍스트 형태로 넣어줘야 한다.
      이 파일에서는 Tmap API만 Tool로 연결한다.
    """
    # Tmap 교통 정보 조회용 Tool 인스턴스 생성
    tools = list(transport_tools) if transport_tools else []

    # ✅ 로컬 Tmap 툴 명시적으로 추가
    tools.append(TmapTrafficTool())

    return Agent(
        role="출국 알림 에이전트",
        goal=(
            "사용자의 집-인천공항 교통 정보(Tmap)와, "
            "다른 에이전트가 생성한 사용자 프로필/주차장/출국장 정보를 종합하여 "
            "출국 5시간 전과 2시간 전에 보낼 알림 메시지를 생성한다. "
            "알림에는 출발 권장 시각, 추천 이동 수단, 추천 주차장·출국장, "
            "현재 및 예측 교통 상황을 한국어로 명확하게 포함해야 한다."
        ),
        backstory=(
            "당신은 사용자가 국제선 비행기에 지각하지 않도록 돕는 스마트 알림 비서이다. "
            "교통량, 공항 혼잡도, 개인 선호를 모두 고려해서, "
            "‘언제 집에서 출발해야 안전한지’, ‘어느 주차장·출국장을 이용하면 좋은지’를 "
            "간결하고 이해하기 쉬운 한국어 메시지로 안내한다. "
            "이전 단계에서 제공된 사용자 프로필, 주차장 추천 결과, 출국장 추천 결과를 "
            "충실히 반영해야 하며, Tmap 교통 정보를 활용해 현실적인 이동 시간을 추정한다."
        ),
        llm=worker_llm,
        tools=tools,
        verbose=True,
        allow_delegation=False,
        memory=True,
    )
