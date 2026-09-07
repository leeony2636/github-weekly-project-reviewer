import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reviewer.orchestrator import ReviewRun


RUN_STATUSES = {
    "running",
    "completed",
    "failed",
    "blocked_sensitive_content",
    "blocked_incomplete_diff",
}

MAX_CACHE_PAYLOAD_BYTES = 2_000_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TraceStore:
    def __init__(
        self,
        database_path: str | Path,
    ) -> None:
        self.database_path = str(database_path)

        if self.database_path != ":memory:":
            path = Path(self.database_path)
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        self.connection = sqlite3.connect(
            self.database_path,
        )
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS review_runs (
                run_id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                pull_request_number INTEGER NOT NULL,
                diff_sha256 TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                successful_providers TEXT NOT NULL,
                provider_failures TEXT NOT NULL,
                raw_finding_count INTEGER NOT NULL,
                invalid_line_count INTEGER NOT NULL,
                invalid_evidence_count INTEGER NOT NULL DEFAULT 0,
                consensus_count INTEGER NOT NULL,
                degraded INTEGER NOT NULL DEFAULT 0,
                quota_snapshot TEXT NOT NULL DEFAULT '{}',
                error_type TEXT
            );

            CREATE TABLE IF NOT EXISTS consensus_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                line_number INTEGER NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                reason TEXT NOT NULL,
                evidence TEXT NOT NULL DEFAULT '',
                evidence_hash TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL,
                providers TEXT NOT NULL,
                match_count INTEGER NOT NULL,
                FOREIGN KEY(run_id)
                    REFERENCES review_runs(run_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS
                idx_consensus_findings_run_id
            ON consensus_findings(run_id);

            CREATE TABLE IF NOT EXISTS review_cache (
                cache_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )

        self._ensure_column(
            "review_runs",
            "diff_sha256",
            "TEXT NOT NULL DEFAULT ''",
        )
        self._ensure_column(
            "review_runs",
            "invalid_evidence_count",
            "INTEGER NOT NULL DEFAULT 0",
        )
        self._ensure_column(
            "review_runs",
            "degraded",
            "INTEGER NOT NULL DEFAULT 0",
        )
        self._ensure_column(
            "review_runs",
            "quota_snapshot",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        self._ensure_column(
            "consensus_findings",
            "evidence",
            "TEXT NOT NULL DEFAULT ''",
        )
        self._ensure_column(
            "consensus_findings",
            "evidence_hash",
            "TEXT NOT NULL DEFAULT ''",
        )

        self.connection.commit()

    def _ensure_column(
        self,
        table_name: str,
        column_name: str,
        definition: str,
    ) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(
                f"PRAGMA table_info({table_name})"
            ).fetchall()
        }

        if column_name not in columns:
            self.connection.execute(
                f"""
                ALTER TABLE {table_name}
                ADD COLUMN {column_name} {definition}
                """
            )

    def start_run(
        self,
        *,
        repository: str,
        pull_request_number: int,
        diff_sha256: str = "",
    ) -> str:
        if not repository.strip():
            raise ValueError("repository가 비어 있습니다.")

        if pull_request_number < 1:
            raise ValueError(
                "pull_request_number는 1 이상이어야 합니다."
            )

        run_id = uuid.uuid4().hex

        self.connection.execute(
            """
            INSERT INTO review_runs (
                run_id,
                repository,
                pull_request_number,
                diff_sha256,
                started_at,
                status,
                successful_providers,
                provider_failures,
                raw_finding_count,
                invalid_line_count,
                invalid_evidence_count,
                consensus_count,
                degraded,
                quota_snapshot
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                repository,
                pull_request_number,
                diff_sha256,
                utc_now(),
                "running",
                "[]",
                "{}",
                0,
                0,
                0,
                0,
                0,
                "{}",
            ),
        )
        self.connection.commit()

        return run_id

    def complete_run(
        self,
        *,
        run_id: str,
        result: ReviewRun,
    ) -> None:
        with self.connection:
            cursor = self.connection.execute(
                """
                UPDATE review_runs
                SET finished_at = ?,
                    status = ?,
                    successful_providers = ?,
                    provider_failures = ?,
                    raw_finding_count = ?,
                    invalid_line_count = ?,
                    invalid_evidence_count = ?,
                    consensus_count = ?,
                    degraded = ?,
                    quota_snapshot = ?,
                    error_type = NULL
                WHERE run_id = ?
                """,
                (
                    utc_now(),
                    "completed",
                    json.dumps(
                        result.successful_providers,
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        result.provider_failures,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    result.raw_finding_count,
                    result.invalid_line_count,
                    result.invalid_evidence_count,
                    len(result.consensus_findings),
                    int(result.degraded),
                    json.dumps(
                        result.quota_snapshot,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    run_id,
                ),
            )

            if cursor.rowcount != 1:
                raise ValueError(
                    f"존재하지 않는 run_id입니다: {run_id}"
                )

            self.connection.executemany(
                """
                INSERT INTO consensus_findings (
                    run_id,
                    file_name,
                    line_number,
                    category,
                    severity,
                    message,
                    reason,
                    evidence,
                    evidence_hash,
                    confidence,
                    providers,
                    match_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        run_id,
                        finding.file,
                        finding.line,
                        finding.category,
                        finding.severity,
                        finding.message,
                        finding.reason,
                        finding.evidence,
                        finding.evidence_hash,
                        finding.confidence,
                        json.dumps(
                            finding.providers,
                            ensure_ascii=False,
                        ),
                        finding.match_count,
                    )
                    for finding in result.consensus_findings
                ],
            )

    def fail_run(
        self,
        *,
        run_id: str,
        status: str = "failed",
        error: Exception,
    ) -> None:
        if status not in RUN_STATUSES - {
            "running",
            "completed",
        }:
            raise ValueError(
                f"허용되지 않은 실패 상태입니다: {status}"
            )

        cursor = self.connection.execute(
            """
            UPDATE review_runs
            SET finished_at = ?,
                status = ?,
                error_type = ?
            WHERE run_id = ?
            """,
            (
                utc_now(),
                status,
                type(error).__name__,
                run_id,
            ),
        )

        if cursor.rowcount != 1:
            raise ValueError(
                f"존재하지 않는 run_id입니다: {run_id}"
            )

        self.connection.commit()

    def get_run(
        self,
        run_id: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT *
            FROM review_runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()

        return dict(row) if row is not None else None

    def get_consensus_findings(
        self,
        run_id: str,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM consensus_findings
            WHERE run_id = ?
            ORDER BY id
            """,
            (run_id,),
        ).fetchall()

        return [dict(row) for row in rows]

    def get_cached_payload(
        self,
        cache_key: str,
    ) -> dict[str, Any] | None:
        self._validate_cache_key(cache_key)

        row = self.connection.execute(
            """
            SELECT payload_json
            FROM review_cache
            WHERE cache_key = ?
            """,
            (cache_key,),
        ).fetchone()

        if row is None:
            return None

        try:
            payload = json.loads(row["payload_json"])
        except json.JSONDecodeError as exc:
            raise ValueError(
                "캐시 데이터가 올바른 JSON이 아닙니다."
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "캐시 데이터의 최상위 값은 객체여야 합니다."
            )

        return payload

    def save_cached_payload(
        self,
        *,
        cache_key: str,
        payload: dict[str, Any],
    ) -> None:
        self._validate_cache_key(cache_key)

        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        if len(encoded.encode("utf-8")) > MAX_CACHE_PAYLOAD_BYTES:
            raise ValueError(
                "캐시 데이터가 허용 크기를 초과했습니다."
            )

        self.connection.execute(
            """
            INSERT INTO review_cache (
                cache_key,
                created_at,
                payload_json
            )
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key)
            DO UPDATE SET
                created_at = excluded.created_at,
                payload_json = excluded.payload_json
            """,
            (
                cache_key,
                utc_now(),
                encoded,
            ),
        )
        self.connection.commit()

    @staticmethod
    def _validate_cache_key(cache_key: str) -> None:
        if not cache_key.strip():
            raise ValueError("cache_key가 비어 있습니다.")

        if len(cache_key) > 256:
            raise ValueError(
                "cache_key는 256자를 초과할 수 없습니다."
            )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "TraceStore":
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc: Any,
        traceback: Any,
    ) -> None:
        self.close()