from typing import Dict, List
from crewai_tools import MCPServerAdapter
import os

TRANSPORT = "streamable-http"


def _connect_single_server(name: str, url: str) -> List:
    """
    단일 MCP 서버에 연결해서 '툴 리스트'를 리턴한다.
    url이 비어 있으면 건너뜀.
    """
    if not url:
        print(f"[MCP][경고] '{name}' MCP URL이 비어 있습니다. 이 서버는 건너뜁니다.")
        return []

    params = {"url": url, "transport": TRANSPORT}
    print(f"[MCP] '{name}' 서버 연결 시도: ")

    # 🔹 context manager 안에서만 MCP 서버 연결 유지
    with MCPServerAdapter(params) as tools:
        tools = list(tools)  # generator일 수도 있으니 리스트로 고정
        tool_names = [t.name for t in tools]
        print(f"  - 로드된 툴 수: {len(tools)}")
        print(f"  - 툴 목록: {tool_names}")
        return tools


def load_flight_mcp_tools() -> Dict[str, List]:
    """
    여러 MCP 서버에 연결해서:
      - 서버별 툴 목록(by_server)
      - 목적별 툴 목록(parking/departure/flight/amadeus)
    을 모두 리턴.
    """

    # 🔹 .env에서 URL 읽어오기
    MCP_SERVER_URLS: Dict[str, str] = {
        "icn":     os.getenv("ICN_MCP_URL"),      # 인천공항 MCP
        "flight":  os.getenv("FLIGHT_MCP_URL"),   # flight-mcp
        "fli":     os.getenv("FLI_MCP_URL"),      # fli (검색 엔진)
        "amadeus": os.getenv("AMADEUS_MCP_URL"),  # amadeus MCP
    }

    by_server: Dict[str, List] = {}
    all_tools: List = []

    # 🔹 여러 MCP 서버 연결
    for name, url in MCP_SERVER_URLS.items():
        tools = _connect_single_server(name, url)
        if not tools:
            continue
        by_server[name] = tools
        all_tools.extend(tools)

    # 🔹 목적별 분류
    parking_tools: List = []
    departure_tools: List = []
    flight_tools: List = []
    amadeus_tools: List = []

    for tool in all_tools:
        n = tool.name.lower()

        # 주차장 관련
        if "parking" in n or "park" in n or "lot" in n:
            parking_tools.append(tool)

        # 출국장/보안검색/터미널 관련
        if "departure" in n or "security" in n or "terminal" in n or "gate" in n:
            departure_tools.append(tool)

        # 항공편/스케줄/상태 관련
        if "flight" in n or "schedule" in n or "status" in n or "fli" in n:
            flight_tools.append(tool)

        # Amadeus / 요금 / 오퍼 관련
        if "amadeus" in n or "fare" in n or "price" in n or "offer" in n:
            amadeus_tools.append(tool)

    print("\n[MCP] 목적별 툴 분류 결과")
    print("  - parking_tools :", [t.name for t in parking_tools])
    print("  - departure_tools:", [t.name for t in departure_tools])
    print("  - flight_tools   :", [t.name for t in flight_tools])
    print("  - amadeus_tools  :", [t.name for t in amadeus_tools])

    return {
        "all": all_tools,
        "by_server": by_server,
        "parking": parking_tools,
        "departure": departure_tools,
        "flight": flight_tools,
        "amadeus": amadeus_tools,
    }
