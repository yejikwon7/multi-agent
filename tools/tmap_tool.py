# tools/tmap_tool.py
import os
import requests
from typing import Any, Dict
from crewai_tools import BaseTool

TMAP_API_KEY = os.getenv("TMAP_API_KEY")


class TmapTrafficTool(BaseTool):
    """
    집 → 인천공항 구간의 교통량/예상 소요 시간 등을 조회하는 툴 (스켈레톤).
    실제 엔드포인트/파라미터는 Tmap 문서 보고 채우면 됨.
    """
    name = "tmap_traffic"
    description = (
        "사용자의 출발지(집)에서 인천공항까지의 교통량과 예상 소요시간을 조회한다. "
        "입력: origin_address, departure_time(YYYY-MM-DD HH:MM 형식 권장). "
        "출력: 예상 이동 시간, 거리, 주요 교통상황 요약."
    )

    def _run(
        self,
        origin_address: str,
        departure_time: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not TMAP_API_KEY:
            # 키 없으면 더미 값 리턴 (테스트용)
            return {
                "status": "mock",
                "message": "TMAP_API_KEY 미설정 – 더미 응답",
                "eta_minutes": 70,
                "distance_km": 62,
                "traffic_summary": "경부고속도로·서울외곽순환 일부 정체, 전체적으로 보통 수준.",
            }

        # === 여기서 실제 Tmap API 호출 ===
        # (아래는 예시 형태로만 보여주는 스켈레톤)
        try:
            # TODO: Tmap 경로 안내 API 엔드포인트/파라미터 맞게 채우기
            url = "https://apis.openapi.sk.com/tmap/routes"
            headers = {"appKey": TMAP_API_KEY}
            params = {
                "startName": origin_address,
                "endName": "인천국제공항",
                "departure_time": departure_time,
            }
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            # TODO: data에서 ETA/거리/교통 요약 파싱
            return {
                "status": "ok",
                "raw": data,
                "eta_minutes": 70,  # data에서 파싱
                "distance_km": 62,  # data에서 파싱
                "traffic_summary": "교통 정보 파싱 결과 요약 텍스트",
            }
        except Exception as e:
            return {
                "status": "error",
                "message": f"Tmap API 호출 실패: {e}",
            }
