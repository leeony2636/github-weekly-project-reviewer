import os
import time
from contextlib import contextmanager
from threading import Lock
from typing import Any

from langfuse import get_client


# 이번 실행에서 생성된 Langfuse generation을 저장한다.
#
# (trace_id, observation_id)
#
# 여러 모델이 동시에 실행될 수 있으므로 Lock을 사용한다.
_OBSERVATIONS: set[tuple[str, str]] = set()
_OBSERVATIONS_LOCK = Lock()


def get_langfuse_client():
    """
    Langfuse 환경변수가 모두 있을 때만 클라이언트를 반환한다.

    GitHub Actions에서는 Langfuse가 필수지만,
    로컬 테스트에서는 환경변수가 없을 수 있으므로 None을 허용한다.
    """
    required = (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
    )

    if not all(os.getenv(name) for name in required):
        return None

    return get_client()


@contextmanager
def generation_context(
    *,
    name: str,
    model: str,
    input_data: Any,
    metadata: dict[str, Any] | None = None,
):
    """
    Langfuse generation 추적을 시작한다.

    생성된 trace_id와 observation_id를 저장해서
    실행 종료 시 실제 Langfuse 저장 여부를 확인한다.
    """
    client = get_langfuse_client()

    # 로컬 테스트 등 Langfuse 설정이 없으면
    # 기존 Reviewer 실행은 그대로 진행한다.
    if client is None:
        yield None
        return

    with client.start_as_current_observation(
        name=name,
        as_type="generation",
        model=model,
        input=input_data,
        metadata=metadata or {},
    ) as generation:

        trace_id = getattr(generation, "trace_id", None)
        observation_id = getattr(generation, "id", None)

        # 실제 생성된 observation만 기록한다.
        if trace_id and observation_id:
            with _OBSERVATIONS_LOCK:
                _OBSERVATIONS.add(
                    (trace_id, observation_id)
                )

        yield generation


def flush_langfuse(
    timeout_seconds: int = 45,
    poll_interval_seconds: float = 1.0,
) -> None:
    """
    Langfuse 데이터를 확실하게 처리한다.

    1. client.flush()로 남은 데이터를 Langfuse API에 전송한다.
    2. 이번 실행에서 생성된 observation들이
       실제 Langfuse API에서 조회되는지 확인한다.
    3. 전부 확인되면 즉시 종료한다.
    4. 제한시간 안에 확인되지 않으면 오류를 발생시킨다.

    따라서 기존의 고정 sleep 30초가 필요하지 않다.
    """
    client = get_langfuse_client()

    # Langfuse 설정이 없는 로컬 실행은 그대로 종료한다.
    if client is None:
        return

    # --------------------------------------------------
    # 1. 클라이언트에 남아 있는 데이터를 Langfuse로 전송
    # --------------------------------------------------
    #
    # 기존 코드와 다르게 예외를 숨기지 않는다.
    # flush 실패 시 GitHub Actions도 실패해야 한다.
    client.flush()

    # --------------------------------------------------
    # 2. 이번 실행의 observation 목록 복사
    # --------------------------------------------------
    with _OBSERVATIONS_LOCK:
        pending = set(_OBSERVATIONS)

    # 모델 호출이 전혀 없었다면 확인할 데이터도 없다.
    if not pending:
        print("Langfuse: 확인할 observation이 없습니다.")
        return

    total = len(pending)

    print(
        f"Langfuse 저장 확인 시작: "
        f"{total}개 observation"
    )

    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    # --------------------------------------------------
    # 3. 실제 Langfuse ingestion 완료 확인
    # --------------------------------------------------
    while pending:

        # 같은 trace에 여러 generation이 있을 수 있으므로
        # trace_id 단위로 한 번씩 조회한다.
        trace_ids = {
            trace_id
            for trace_id, _ in pending
        }

        for trace_id in trace_ids:
            try:
                response = client.api.observations.get_many(
                    trace_id=trace_id,
                    fields="core",
                    limit=1000,
                )

                visible_ids = {
                    observation.id
                    for observation in response.data
                }

                # 실제 조회되는 observation만 pending에서 제거한다.
                pending = {
                    (pending_trace_id, observation_id)
                    for pending_trace_id, observation_id in pending
                    if not (
                        pending_trace_id == trace_id
                        and observation_id in visible_ids
                    )
                }

            except Exception as exc:
                # 서버 내부 ingestion이 아직 끝나지 않았을 수 있다.
                # 바로 실패하지 않고 제한시간 안에서 다시 확인한다.
                last_error = exc

        # --------------------------------------------------
        # 전부 확인됨
        # --------------------------------------------------
        if not pending:
            print(
                f"Langfuse 저장 확인 완료: "
                f"{total}개 observation"
            )
            return

        # --------------------------------------------------
        # 제한시간 초과
        # --------------------------------------------------
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Langfuse 저장 확인 실패: "
                f"{len(pending)}개 observation이 "
                f"{timeout_seconds}초 안에 "
                "Langfuse에서 조회되지 않았습니다."
            ) from last_error

        # 기존 고정 30초 대기 대신
        # 1초마다 실제 저장 여부를 확인한다.
        time.sleep(poll_interval_seconds)