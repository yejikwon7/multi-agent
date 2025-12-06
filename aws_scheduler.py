import boto3
import os
from datetime import datetime, timezone
from typing import Dict, Any
import uuid
import json
from dotenv import load_dotenv

load_dotenv()

# === 환경변수 로딩 ===
AWS_REGION = os.getenv("AWS_REGION")
LAMBDA_ARN = os.getenv("LAMBDA_ARN")
SCHEDULER_ROLE_ARN = os.getenv("SCHEDULER_ROLE_ARN")
AWS_SES_SENDER = os.getenv("AWS_SES_SENDER")

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

scheduler = boto3.client("scheduler", region_name="us-east-1")
ENABLE_AWS_SCHEDULER = os.getenv("ENABLE_AWS_SCHEDULER")

# === boto3 클라이언트 생성 ===
session_kwargs = {"region_name": AWS_REGION}
if AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY:
    # 로컬에서 env로 자격증명 주는 경우
    session_kwargs["aws_access_key_id"] = AWS_ACCESS_KEY_ID
    session_kwargs["aws_secret_access_key"] = AWS_SECRET_ACCESS_KEY

try:
    scheduler = boto3.client("scheduler", **session_kwargs)
except Exception as e:
    print(f"[WARN] AWS scheduler 클라이언트 생성 실패: {e}")
    scheduler = None

def _ensure_scheduler_available(tag: str) -> bool:
    """실제 스케줄 만들기 전에 공통으로 체크."""
    if not ENABLE_AWS_SCHEDULER:
        print(f"[INFO] ENABLE_AWS_SCHEDULER=False라서 '{tag}' 스케줄 생성은 건너뜀")
        return False

    if scheduler is None:
        print("[WARN] scheduler 클라이언트가 없습니다. AWS 자격증명/리전을 확인하세요.")
        return False

    if not LAMBDA_ARN:
        print("[WARN] LAMBDA_ARN이 설정되지 않아서 스케줄 생성 불가")
        return False

    if not SCHEDULER_ROLE_ARN:
        print("[WARN] SCHEDULER_ROLE_ARN이 설정되지 않아서 스케줄 생성 불가")
        return False

    return True


def create_email_schedule(run_time_utc: datetime, to_email: str, subject: str, body: str, tag: str):
    if run_time_utc.tzinfo is None:
        raise ValueError("run_time_utc는 timezone-aware(UTC) datetime이어야 합니다.")

    if not _ensure_scheduler_available(tag):
        return None

    schedule_expression = _format_at_expression(run_time_utc)
    print(f"[DEBUG] email schedule_expression = {schedule_expression}")

    schedule_name = f"flight-email-reminder-{tag}-{uuid.uuid4().hex[:8]}"

    payload = {
        "to_email": to_email,
        "subject": subject,
        "body": body,
    }

    response = scheduler.create_schedule(
        Name=schedule_name,
        GroupName="default",
        ScheduleExpression=schedule_expression,
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": LAMBDA_ARN,
            "RoleArn": SCHEDULER_ROLE_ARN,
            "Input": json.dumps(payload, ensure_ascii=False),
        },
        Description=f"Email flight reminder ({tag}) at {schedule_expression}",
        State="ENABLED",
    )

    print(f"[AWS] Created email schedule '{schedule_name}' at {schedule_expression}")
    print(response)
    return schedule_name


def create_departure_notification_schedule(
    run_time_utc: datetime,
    tag: str,
    payload: Dict[str, Any],
):
    """
    출국 5시간 전/2시간 전 알림을 위해 EventBridge Scheduler에 스케줄 등록.
    - run_time_utc: UTC 기준 실행 시각
    - tag: "5h_before" 또는 "2h_before" 등 식별자
    - payload: Lambda로 전달할 전체 컨텍스트
      (home_address, best_flight, best_parking, best_departure 등)
    """
    if run_time_utc.tzinfo is None:
        raise ValueError("run_time_utc는 timezone-aware(UTC) datetime이어야 합니다.")

    if not _ensure_scheduler_available(tag):
        return None

    schedule_expression = _format_at_expression(run_time_utc)
    print(f"[DEBUG] email schedule_expression = {schedule_expression}")

    schedule_name = f"icn-departure-reminder-{tag}-{uuid.uuid4().hex[:8]}"

    response = scheduler.create_schedule(
        Name=schedule_name,
        GroupName="default",
        ScheduleExpression=schedule_expression,
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": LAMBDA_ARN,  # 여기 Lambda에서 SNS(SMS/이메일) 발송 + Tmap 호출
            "RoleArn": SCHEDULER_ROLE_ARN,
            "Input": json.dumps(payload, ensure_ascii=False),
        },
        Description=f"ICN departure reminder ({tag}) at {schedule_expression}",
        State="ENABLED",
    )

    print(f"[AWS] Created departure reminder schedule '{schedule_name}' at {schedule_expression}")
    print(response)
    return schedule_name

def _format_at_expression(run_time_utc):
    """
    EventBridge Scheduler용 at() 표현식 포맷터.
    - 입력: tz-aware datetime (UTC 기준)
    - 출력: at(YYYY-MM-DDTHH:MM:SS)  ← 타임존 표기 없음
    """
    if run_time_utc.tzinfo is None:
        # 안전하게 UTC로 가정
        run_time_utc = run_time_utc.replace(tzinfo=timezone.utc)

    # 혹시 다른 타임존이면 UTC로 변환
    run_time_utc = run_time_utc.astimezone(timezone.utc)

    # tzinfo 제거(naive로 만든 다음 문자열 포맷)
    naive_utc = run_time_utc.replace(tzinfo=None)
    return f"at({naive_utc.strftime('%Y-%m-%dT%H:%M:%S')})"

