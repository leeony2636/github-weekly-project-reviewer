import os
from contextlib import nullcontext
from typing import Any

from langfuse import get_client


def get_langfuse_client():
    """
    Langfuse 환경변수가 모두 있을 때만 클라이언트를 반환한다.
    설정이 없으면 None을 반환해서 기존 Reviewer 실행을 막지 않는다.
    """
    required = (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
    )

    if not all(os.getenv(name) for name in required):
        return None

    return get_client()


def generation_context(
    *,
    name: str,
    model: str,
    input_data: Any,
    metadata: dict[str, Any] | None = None,
):
    """
    Langfuse가 설정되어 있으면 generation 추적을 시작하고,
    아니면 빈 context를 반환한다.
    """
    client = get_langfuse_client()

    if client is None:
        return nullcontext(None)

    return client.start_as_current_observation(
        name=name,
        as_type="generation",
        model=model,
        input=input_data,
        metadata=metadata or {},
    )


def flush_langfuse() -> None:
    """
    실행 종료 전에 남은 Langfuse 기록을 전송한다.
    실패해도 Reviewer 전체 실행에는 영향을 주지 않는다.
    """
    try:
        client = get_langfuse_client()

        if client is not None:
            client.flush()
    except Exception:
        # Langfuse 장애 때문에 기존 Reviewer가 실패하면 안 된다.
        pass