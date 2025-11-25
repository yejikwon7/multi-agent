import os
import re
import requests
from typing import Any, Dict, Optional, Type
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from datetime import datetime
from zoneinfo import ZoneInfo

TMAP_API_KEY = os.getenv("TMAP_API_KEY")

class TmapTrafficToolInput(BaseModel):
    origin_address: str = Field(
        ...,
        description="출발지 전체 주소 또는 동 단위까지 (예: '서울시 서대문구 연희동')"
    )
    # 출발 시각은 선택 – 없으면 '현재 시각'으로 처리
    departure_time: Optional[str] = Field(
        None,
        description="출발 예정 시각 (예: '2025-11-30 07:30', YYYY-MM-DD HH:MM 형식, 미입력 시 현재 시각)"
    )

class TmapTrafficTool(BaseTool):
    """
    집 → 인천공항 구간의 교통량/예상 소요 시간 등을 조회하는 툴 (스켈레톤).
    실제 엔드포인트/파라미터는 Tmap 문서 보고 채우면 됨.
    """
    name: str = "tmap_traffic"
    description: str = (
        "사용자의 출발지(집)에서 인천공항까지의 교통량과 예상 소요시간을 조회한다. "
        "입력: origin_address, departure_time(YYYY-MM-DD HH:MM 형식 권장). "
        "출력: 예상 이동 시간, 거리, 주요 교통상황 요약."
    )

    args_schema: Type[TmapTrafficToolInput] = TmapTrafficToolInput

    def _normalize_address(self, raw: str) -> str:
        """
        '서울시 서대문구 연희동에서 인천공항 갈 때' 같은 문장에서
        앞부분 주소만 남기려고 아주 단순 전처리하는 함수.
        """
        if not raw:
            return raw

        text = raw.strip()

        # '에서', '부터' 등으로 잘라서 앞부분만 사용
        for token in ["에서", "부터", "출발", "기점", "까지", "로부터"]:
            idx = text.find(token)
            if idx > 0:
                text = text[:idx].strip()
                break

        # 공백 여러 개 -> 하나
        text = re.sub(r"\s+", " ", text)

        return text

    def _run(
        self,
        origin_address: str,
        departure_time: Optional[str] = None,
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

        try:
            # 1) 출발지 주소 → 좌표 (POI 검색 or 지오코딩)
            geo_url = "https://apis.openapi.sk.com/tmap/geo/fullAddrGeo"
            geo_headers = {"appKey": TMAP_API_KEY}
            geo_params = {"fullAddr": origin_address}
            geo_resp = requests.get(geo_url, headers=geo_headers, params=geo_params, timeout=5)
            geo_resp.raise_for_status()
            geo_data = geo_resp.json()

            # 아주 단순하게 첫 결과만 사용 (실제에선 예외 처리 필요)
            coord_info = geo_data["coordinateInfo"]["coordinate"][0]
            startX = float(coord_info["newLon"])
            startY = float(coord_info["newLat"])

            # 인천공항 좌표는 하드코딩(간단 버전) — 필요시 따로 geocode 가능
            endX = 126.4505
            endY = 37.4602

            # 2) 자동차 경로 안내 호출 (POST)
            route_url = "https://apis.openapi.sk.com/tmap/routes?version=1&format=json"
            route_headers = {
                "appKey": TMAP_API_KEY,
                "Content-Type": "application/json"
            }
            route_body = {
                "startX": startX,
                "startY": startY,
                "endX": endX,
                "endY": endY,
                "reqCoordType": "WGS84GEO",
                "resCoordType": "WGS84GEO",
                "trafficInfo": "Y",
                "startName": origin_address,
                "endName": "인천국제공항",
            }

            route_resp = requests.post(route_url, headers=route_headers, json=route_body, timeout=10)
            route_resp.raise_for_status()
            route_data = route_resp.json()

            # 3) 거리/시간 파싱 (Tmap 응답 구조에 맞게 수정 필요)
            props = route_data["features"][0]["properties"]
            total_time_sec = props["totalTime"]
            total_dist_m = props["totalDistance"]

            eta_minutes = round(total_time_sec / 60)
            distance_km = round(total_dist_m / 1000, 1)

            traffic_summary = f"예상 소요 {eta_minutes}분, 약 {distance_km}km. 주요 구간 일부 정체."

            return {
                "status": "ok",
                "eta_minutes": eta_minutes,
                "distance_km": distance_km,
                "traffic_summary": traffic_summary,
                "raw": route_data,
            }

        except Exception as e:
            return {
                "status": "error",
                "message": f"Tmap API 호출 실패: {e}",
            }
