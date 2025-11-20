from typing import Dict, List
from crewai_tools import MCPServerAdapter

def load_flight_mcp_tools() -> Dict[str, List]:
    """
    비행기 관련 MCP 서버에서 툴을 로드해서 용도별로 나눠서 리턴.
    실제 툴 이름은 smithery에서 확인한 이름에 맞춰 필터링하면 됨.
    """

    MCP_SERVER_URLS: Dict[str, str] = {
        "icn" : "https://server.smithery.ai/@AITutor3/icn-mcp/mcp",
        "fli" : "https://server.smithery.ai/@gvzq/flight-mcp/mcp",
        "google" : "https://server.smithery.ai/@punitarani/fli/mcp",
        "amadeus" : "https://server.smithery.ai/@almogqwinz/mcp-amadeus-api/mcp",
    }

    TRANSPORT = "streamable-http"

    def _connect_single_server(name: str, url: str):
        """단일 MCP 서버에 연결해서 MCPServerAdapter 객체를 리턴."""
        params = {"url": url, "transport": TRANSPORT}
        print(f"[MCP] '{name}' 서버 연결 시도: {url}")

        adapter = MCPServerAdapter(params)

        tool_names = [t.name for t in adapter]
        print(f"  - 로드된 툴 수: {len(adapter)}")
        print(f"  - 툴 목록: {tool_names}")

        return adapter

    def load_flight_mcp_tools() -> Dict[str, List]:
        """
        여러 MCP 서버에 모두 연결해서:
          - 서버별 툴 목록
          - 목적별(주차장/출국장/항공편/아마데우스) 툴 목록
        을 한 번에 리턴한다.
        """
        by_server: Dict[str, List] = {}
        all_tools: List = []

        # ❷ 여러 MCP 서버에 순차적으로 연결
        for name, url in MCP_SERVER_URLS.items():
            adapter = _connect_single_server(name, url)
            tools = list(adapter)
            by_server[name] = tools
            all_tools.extend(tools)

        # ❸ 목적별로 툴 분류 (툴 이름에 따라 대충 나누는 예시)
        parking_tools: List = []
        departure_tools: List = []
        flight_tools: List = []
        amadeus_tools: List = []

        for tool in all_tools:
            n = tool.name.lower()

            # 주차장 관련 (예: parking, park, lot, icn_parking 등)
            if "parking" in n or "park" in n or "lot" in n:
                parking_tools.append(tool)

            # 출국장/보안 검색대 관련 (예: departure, security, terminal, gate 등)
            if "departure" in n or "security" in n or "terminal" in n or "gate" in n:
                departure_tools.append(tool)

            # 항공편/스케줄/상태 관련 (예: flight, schedule, status, fli 등)
            if "flight" in n or "schedule" in n or "status" in n or "fli" in n:
                flight_tools.append(tool)

            # Amadeus / 요금 / 예약 관련
            if "amadeus" in n or "fare" in n or "price" in n or "offer" in n:
                amadeus_tools.append(tool)

        print("\n[MCP] 목적별 툴 분류 결과")
        print("  - parking_tools :", [t.name for t in parking_tools])
        print("  - departure_tools:", [t.name for t in departure_tools])
        print("  - flight_tools   :", [t.name for t in flight_tools])
        print("  - amadeus_tools  :", [t.name for t in amadeus_tools])

        return {
            # 전체
            "all": all_tools,

            # 서버별
            "by_server": by_server,

            # 목적별 (에이전트에서 여기 키들을 사용)
            "parking": parking_tools,
            "departure": departure_tools,
            "flight": flight_tools,
            "amadeus": amadeus_tools,
        }
