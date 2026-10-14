from __future__ import annotations

import json
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .db import Database


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat(sep=" ")
    if hasattr(value, "item"):
        return value.item()
    return str(value)


class AuditRun:
    """Persists run/step audit records to the database and a JSONL log."""

    def __init__(
        self,
        db: Database,
        query: str,
        scope: dict[str, Any],
        log_path: str | Path,
        source_id: str = "industrial_pdm",
    ):
        self.db = db
        self.query = query
        self.scope = scope
        self.source_id = source_id
        self.log_path = Path(log_path)
        self.run_id = str(uuid.uuid4())
        self.started_at = utc_now()
        self._started_clock = time.perf_counter()

    def _write_jsonl(self, payload: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=json_default) + "\n")

    def start(self) -> "AuditRun":
        self.db.execute(
            "INSERT INTO analysis_runs "
            "(run_id, source_id, requested_query, scope_json, status, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                self.run_id,
                self.source_id,
                self.query,
                json.dumps(self.scope, ensure_ascii=False, default=json_default),
                "running",
                self.started_at,
            ),
        )
        self._write_jsonl(
            {
                "record_type": "analysis_run",
                "event": "started",
                "run_id": self.run_id,
                "source_id": self.source_id,
                "query": self.query,
                "scope": self.scope,
                "timestamp": self.started_at,
            }
        )
        return self

    @contextmanager
    def step(
        self,
        step_name: str,
        tool_name: str | None = None,
        input_summary: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        started_at = utc_now()
        clock = time.perf_counter()
        state: dict[str, Any] = {"output_summary": {}}
        try:
            yield state
        except Exception as exc:
            finished_at = utc_now()
            duration_ms = round((time.perf_counter() - clock) * 1000)
            self._insert_step(
                step_name,
                tool_name,
                "failed",
                started_at,
                finished_at,
                duration_ms,
                input_summary,
                state.get("output_summary"),
                str(exc),
            )
            raise
        else:
            finished_at = utc_now()
            duration_ms = round((time.perf_counter() - clock) * 1000)
            self._insert_step(
                step_name,
                tool_name,
                "succeeded",
                started_at,
                finished_at,
                duration_ms,
                input_summary,
                state.get("output_summary"),
                None,
            )

    def _insert_step(
        self,
        step_name: str,
        tool_name: str | None,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: int,
        input_summary: dict[str, Any] | None,
        output_summary: dict[str, Any] | None,
        error_message: str | None,
    ) -> None:
        self.db.execute(
            "INSERT INTO analysis_step_logs "
            "(run_id, step_name, tool_name, status, started_at, finished_at, duration_ms, "
            "input_summary_json, output_summary_json, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.run_id,
                step_name,
                tool_name,
                status,
                started_at,
                finished_at,
                duration_ms,
                json.dumps(input_summary or {}, ensure_ascii=False, default=json_default),
                json.dumps(output_summary or {}, ensure_ascii=False, default=json_default),
                error_message,
            ),
        )
        self._write_jsonl(
            {
                "record_type": "analysis_step",
                "run_id": self.run_id,
                "step_name": step_name,
                "tool_name": tool_name,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
                "input_summary": input_summary or {},
                "output_summary": output_summary or {},
                "error_message": error_message,
            }
        )

    def finish(self, status: str = "succeeded", error_message: str | None = None) -> None:
        finished_at = utc_now()
        duration_ms = round((time.perf_counter() - self._started_clock) * 1000)
        self.db.execute(
            "UPDATE analysis_runs SET status = ?, finished_at = ?, duration_ms = ?, error_message = ? "
            "WHERE run_id = ?",
            (status, finished_at, duration_ms, error_message, self.run_id),
        )
        self._write_jsonl(
            {
                "record_type": "analysis_run",
                "event": "finished",
                "run_id": self.run_id,
                "status": status,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
                "error_message": error_message,
            }
        )
