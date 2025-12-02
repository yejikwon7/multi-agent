from typing import Dict, List
from crewai_tools import MCPServerAdapter
import os

TRANSPORT = "streamable-http"

# ✅ 실제로 사용하도록 변경
ACTIVE_MCP_ADAPTERS: List["StableMCPServerAdapter"] = []


class StableMCPServerAdapter(MCPServerAdapter):
    def __init__(self, params):
        # timeout 기본값 주고 super 호출
        params["timeout"] = params.get("timeout", 120)  # 2분
        super().__init__(params)


def _connect_single_server(name: str, url: str) -> List:
    """
    단일 MCP 서버에 연결해서 tools 리스트를 리턴한다.
    ✅ 여기서는 어댑터의 __enter__만 호출하고 __exit__은 호출하지 않는다.
       (즉, 연결/이벤트 루프를 열어둔 채로 툴을 사용하기 위함)
    """
    if not url:
        raise RuntimeError(f"[MCP][에러] '{name}' MCP URL이 비어 있습니다. .env를 확인하세요.")

    params = {"url": url, "transport": TRANSPORT}
    print(f"[MCP] '{name}' 서버 연결 시도")

    try:
        # 🔹 컨텍스트 매니저(with) 대신 직접 __enter__ 호출
        adapter = StableMCPServerAdapter(params)
        tools_gen = adapter.__enter__()  # 원래 with가 해주던 일을 직접 호출
        ACTIVE_MCP_ADAPTERS.append(adapter)  # 나중에 닫기 위해 저장

        tools = list(tools_gen)
    except Exception as e:
        raise RuntimeError(f"[MCP][에러] '{name}' 서버 연결 실패: {e}") from e

    if not tools:
        raise RuntimeError(f"[MCP][에러] '{name}' MCP에서 로드된 툴이 하나도 없습니다.")

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
    transport_tools: List = []

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

        # 교통/경로 관련 (tmap_traffic 같은 툴용)
        if "traffic" in n or "tmap" in n or "route" in n:
            transport_tools.append(tool)

    print("\n[MCP] 목적별 툴 분류 결과")
    print("  - parking_tools :", [t.name for t in parking_tools])
    print("  - departure_tools:", [t.name for t in departure_tools])
    print("  - flight_tools   :", [t.name for t in flight_tools])
    print("  - amadeus_tools  :", [t.name for t in amadeus_tools])
    print("  - transport_tools:", [t.name for t in transport_tools])

    return {
        "all": all_tools,
        "by_server": by_server,
        "parking": parking_tools,
        "departure": departure_tools,
        "flight": flight_tools,
        "amadeus": amadeus_tools,
        "transport": transport_tools,
    }


def close_all_mcp_adapters():
    """
    프로그램 종료 시 MCP 어댑터들을 정리해서 이벤트 루프를 닫아준다.
    """
    global ACTIVE_MCP_ADAPTERS
    for adapter in ACTIVE_MCP_ADAPTERS:
        try:
            adapter.__exit__(None, None, None)
        except Exception as e:
            print(f"[MCP] MCP 어댑터 종료 중 오류: {e}")
    ACTIVE_MCP_ADAPTERS = []
