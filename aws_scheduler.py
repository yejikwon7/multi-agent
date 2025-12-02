import boto3
from datetime import datetime, timezone
import uuid
import json

AWS_REGION = "us-east-1"  # 실제 사용하는 리전으로 변경
LAMBDA_ARN = "arn:aws:sns:us-east-1:957103509117:agent_flight:89f34e0e-2285-4371-8166-50410b528b47"
SCHEDULER_ROLE_ARN = "arn:aws:iam::123456789012:role/EventBridgeSchedulerToLambdaRole"        # Scheduler → Lambda 권한 가진 Role ARN

scheduler = boto3.client("scheduler", region_name=AWS_REGION)

def create_email_schedule(run_time_utc: datetime, to_email: str, subject: str, body: str, tag: str):
    """
    EventBridge Scheduler에 '특정 시각(run_time_utc)'에 Lambda 호출하는 스케줄 생성.
    - run_time_utc: timezone-aware(UTC) datetime
    - to_email: 수신자 이메일 주소
    - subject, body: 이메일 제목/본문
    - tag: 스케줄 식별용(예: "5h_before", "2h_before")
    """
    if run_time_utc.tzinfo is None:
        raise ValueError("run_time_utc는 timezone-aware(UTC) datetime이어야 합니다.")

    # ISO8601 형식으로 변환: "2025-11-29T23:00:00Z"
    run_at_str = run_time_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    schedule_name = f"flight-email-reminder-{tag}-{uuid.uuid4().hex[:8]}"

    payload = {
        "to_email": to_email,
        "subject": subject,
        "body": body,
    }

    response = scheduler.create_schedule(
        Name=schedule_name,
        GroupName="default",
        ScheduleExpression=f"at({run_at_str})",
        FlexibleTimeWindow={
            "Mode": "OFF"
        },
        Target={
            "Arn": LAMBDA_ARN,
            "RoleArn": SCHEDULER_ROLE_ARN,
            "Input": json.dumps(payload, ensure_ascii=False)
        },
        Description=f"Email flight reminder ({tag}) at {run_at_str}",
        State="ENABLED",
    )

    print(f"[AWS] Created email schedule '{schedule_name}' at {run_at_str}")
    print(response)
    return schedule_name
