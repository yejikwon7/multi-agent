import os
from crewai import LLM

# os.environ['OPENAI_API_KEY'] = "sk-proj-w8FCNpB28gOCxrDPJolbCImfoIwoPxkXaSHNz4OkiL-tSaJS6bZ4q-74lshhZ6aANKUWIsGl5ZT3BlbkFJr7M24RUYx9sXnKVXS5VJAFjQbcNivrIX6gcuPQ2EV4U_9Qn1a9xg2YMV3KPpZxNzrAjIyjgBo"
# set OPENAI-API_KEY = "sk-proj-w8FCNpB28gOCxrDPJolbCImfoIwoPxkXaSHNz4OkiL-tSaJS6bZ4q-74lshhZ6aANKUWIsGl5ZT3BlbkFJr7M24RUYx9sXnKVXS5VJAFjQbcNivrIX6gcuPQ2EV4U_9Qn1a9xg2YMV3KPpZxNzrAjIyjgBo"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
FLIGHT_MCP_URL = os.getenv("FLIGHT_MCP_URL")
TMAP_API_KEY = os.getenv("TMAP_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY 환경 변수를 먼저 설정하세요.")

worker_llm = LLM(model="openai/gpt-4o-mini")
manager_llm = LLM(model="openai/gpt-4o")