import os
from crewai import LLM
from dotenv import load_dotenv

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
TMAP_API_KEY = os.getenv("TMAP_API_KEY")

if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY 환경 변수를 먼저 설정하세요.")

worker_llm = LLM(
    model="gemini-2.5-flash",
    api_key=GOOGLE_API_KEY,
    provider="google"
)

print(worker_llm.call("간단하게 자기소개 한 문장 해줘."))

manager_llm = LLM(
    model="gemini-2.5-flash",
    api_key=GOOGLE_API_KEY,
    provider="google"
)

AWS_REGION = os.getenv("AWS_REGION")
LAMBDA_ARN = os.getenv("LAMBDA_ARN")
SCHEDULER_ROLE_ARN = os.getenv("SCHEDULER_ROLE_ARN")
AWS_SES_SENDER = os.getenv("AWS_SES_SENDER")
ENABLE_AWS_SCHEDULER = os.getenv("ENABLE_AWS_SCHEDULER")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")