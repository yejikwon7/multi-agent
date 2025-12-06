import os
import re
import requests
from typing import Any, Dict, Optional, Type
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from datetime import datetime
from zoneinfo import ZoneInfo

# TMAP_API_KEY = os.getenv("TMAP_API_KEY")


class TmapTrafficToolInput(BaseModel):
    origin_address: str = Field(
        ...,
        description="출발지 전체 주소 또는 동 단위까지 (예: '서울시 서대문구 연희동')"
    )
    terminal: Optional[str] = Field(
        "T1",
        description="도착 터미널 (T1 또는 T2). 기본값 T1."
    )
    departure_time: Optional[str] = Field(
        None,
        description="출발 예정 시각 (예: '2025-11-30 07:30', YYYY-MM-DD HH:MM 형식, 미입력 시 현재 시각)"
    )


class TmapTrafficTool(BaseTool):
    """
    집 → 인천공항 구간의 교통량/예상 소요 시간 등을 조회하는 툴.
    - Tmap 지오코딩(fullAddrGeo)로 출발지 주소를 좌표로 변환
    - 자동차 경로안내(/tmap/routes)로 실시간 교통정보(trafficInfo=Y)를 반영한 경로/시간 조회
    """
    name: str = "tmap_traffic"
    description: str = (
        "사용자의 출발지(집)에서 인천국제공항까지의 실시간 교통을 반영한 예상 소요시간과 거리를 조회한다. "
        "입력: origin_address, departure_time(YYYY-MM-DD HH:MM 형식 권장). "
        "출력: 예상 이동 시간(분), 거리(km), 실시간 교통을 반영한 요약 설명."
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

    def _safe_float(self, d: Dict[str, Any], *keys: str) -> Optional[float]:
        """
        coord_info 같은 dict에서 newLon/newLat, lon/lat 등 여러 후보 키를 차례대로 시도.
        값이 빈 문자열이거나 숫자로 변환 안 되면 다음 키로 넘어감.
        """
        for key in keys:
            if key not in d:
                continue
            v = d.get(key)
            if v in (None, ""):
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    def _run(
        self,
        origin_address: str,
        terminal: Optional[str] = "T1",  # ← 이 줄 추가
        departure_time: Optional[str] = None,
    ) -> Dict[str, Any]:

        api_key = os.getenv("TMAP_API_KEY")
        # 0) API 키 체크
        if not api_key:
            return {
                "status": "error",
                "message": "TMAP_API_KEY가 설정되어 있지 않습니다. .env에 TMAP_API_KEY를 설정해 주세요.",
            }

        terminal = (terminal or "T1").upper()

        # 출발지 주소 정리
        origin_norm = self._normalize_address(origin_address)

        # departure_time은 현재는 경로 API에 직접 반영되진 않지만,
        # 형식 검증 정도는 해 둘 수 있음 (에러 내기보다는 참고용으로만 쓴다).
        departure_str = departure_time
        parsed_departure: Optional[datetime] = None
        if departure_time:
            try:
                # "YYYY-MM-DD HH:MM" 형식 가정
                departure_time = departure_time.strip()
                parsed_departure = datetime.strptime(departure_time, "%Y-%m-%d %H:%M")
                # 한국 기준 타임존 부여 (Tmap은 출발시각 파라미터를 별도로 받지 않아서
                # 현재는 로직/로그 용으로만 사용)
                parsed_departure = parsed_departure.replace(tzinfo=ZoneInfo("Asia/Seoul"))
            except Exception:
                # 형식이 이상해도 굳이 에러 내진 않고, 그냥 무시하고 진행
                parsed_departure = None

        try:
            # 1) 출발지 주소 → 좌표 (fullAddrGeo)
            geo_url = "https://apis.openapi.sk.com/tmap/geo/fullAddrGeo"
            geo_headers = {"appKey": api_key}
            geo_params = {"fullAddr": origin_norm}
            geo_resp = requests.get(geo_url, headers=geo_headers, params=geo_params, timeout=5)
            geo_resp.raise_for_status()
            geo_data = geo_resp.json()

            # coordinateInfo 구조에서 첫 번째 좌표 사용
            coord_list = (
                geo_data.get("coordinateInfo", {})
                .get("coordinate", [])
            )
            if not coord_list:
                return {
                    "status": "error",
                    "message": f"지오코딩 결과가 없습니다: {origin_norm}",
                    "raw_geo": geo_data,
                }

            coord_info = coord_list[0]

            # newLon/newLat → 없거나 ''이면 lon/lat도 시도
            startX = self._safe_float(coord_info, "newLon", "lon")
            startY = self._safe_float(coord_info, "newLat", "lat")

            if startX is None or startY is None:
                return {
                    "status": "error",
                    "message": f"좌표 파싱 실패(newLon/newLat, lon/lat 모두 사용 불가): {coord_info}",
                    "raw_geo": geo_data,
                }

            # 2) 인천공항 좌표 (WGS84 lon/lat)
            if terminal == "T2":
                endX = 126.4524
                endY = 37.4690
            else:  # 기본 T1
                endX = 126.4505
                endY = 37.4602

            # 3) 자동차 경로 안내 호출 (실시간 교통 반영)
            #    문서 예시: https://apis.openapi.sk.com/tmap/tmap/routes?version=1
            #    여기서는 format=json 명시.
            route_url = "https://apis.openapi.sk.com/tmap/tmap/routes?version=1&format=json"
            route_headers = {
                "appKey": api_key,
                "Content-Type": "application/json"
            }
            route_body: Dict[str, Any] = {
                "startX": startX,
                "startY": startY,
                "endX": endX,
                "endY": endY,
                "reqCoordType": "WGS84GEO",
                "resCoordType": "WGS84GEO",
                "trafficInfo": "Y",  # ✅ 실시간 교통 반드시 사용
                "carType": 0,        # 승용차
                "sort": "index",
                "detailPosFlag": "2",
                "endRpFlag": "G",
                "startName": origin_norm,
                "endName": "인천국제공항",
            }
            # totalValue=2를 쓰면 더 간단한 응답을 받을 수 있지만,
            # 여기서는 전체 경로 정보(traffic 등)를 유지하기 위해 생략.

            route_resp = requests.post(route_url, headers=route_headers, json=route_body, timeout=10)
            route_resp.raise_for_status()
            route_data = route_resp.json()

            features = route_data.get("features", [])
            if not features:
                return {
                    "status": "error",
                    "message": "자동차 경로안내 응답에 features가 없습니다.",
                    "raw_route": route_data,
                }

            # 4) 거리/시간 파싱
            # - 문서상 totalDistance/totalTime은 보통 출발지(pointType=S)에 포함되거나
            #   totalValue=2 사용 시 첫 Feature의 properties에 포함.
            props_with_total = None
            for f in features:
                props = f.get("properties", {})
                if "totalTime" in props and "totalDistance" in props:
                    props_with_total = props
                    break

            if not props_with_total:
                # 혹시 totalValue=2 형식을 따로 요청한 경우도 첫 번째 feature에만 값이 있을 수 있음
                first_props = features[0].get("properties", {})
                if "totalTime" in first_props and "totalDistance" in first_props:
                    props_with_total = first_props

            if not props_with_total:
                return {
                    "status": "error",
                    "message": "totalTime/totalDistance 정보를 찾지 못했습니다.",
                    "raw_route": route_data,
                }

            total_time_sec = props_with_total.get("totalTime")
            total_dist_m = props_with_total.get("totalDistance")

            if total_time_sec is None or total_dist_m is None:
                return {
                    "status": "error",
                    "message": f"totalTime/totalDistance가 None입니다: {props_with_total}",
                    "raw_route": route_data,
                }

            eta_minutes = round(float(total_time_sec) / 60)
            distance_km = round(float(total_dist_m) / 1000, 1)

            if parsed_departure:
                human_time = parsed_departure.strftime("%Y-%m-%d %H:%M")
                traffic_summary = (
                    f"{human_time} 기준, 실시간 교통을 반영한 예상 소요 시간은 약 {eta_minutes}분, "
                    f"이동 거리는 약 {distance_km}km입니다."
                )
            else:
                traffic_summary = (
                    f"현재 시각 기준, 실시간 교통을 반영한 예상 소요 시간은 약 {eta_minutes}분, "
                    f"이동 거리는 약 {distance_km}km입니다."
                )

            # 필요하면 departure_time/현재 시각을 반영한 부가 설명도 붙일 수 있음
            return {
                "status": "ok",
                "eta_minutes": eta_minutes,
                "distance_km": distance_km,
                "traffic_summary": traffic_summary,
                "query": {
                    "origin": origin_norm,
                    "departure_time": departure_str,
                },
                "raw": route_data,  # 디버깅용 원본 응답
            }

        except requests.HTTPError as e:
            # HTTP 에러 (4xx/5xx)
            return {
                "status": "error",
                "message": f"Tmap HTTP 오류: {e}",
            }
        except Exception as e:
            # 그 외 모든 에러
            return {
                "status": "error",
                "message": f"Tmap API 호출 실패: {e}",
            }
