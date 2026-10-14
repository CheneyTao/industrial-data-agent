from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import Engine, event
from sqlalchemy.engine import Connection

from dataagent.errors import DataAgentError

T = TypeVar("T")
logger = logging.getLogger(__name__)


async def run_read_operation(engine: Engine, operation: Callable[[Connection], T], seconds: float) -> T:
    """Cancel the owned DB operation, then join its worker before reporting completion.

    Never terminate a server/process. The connection context rolls back and releases
    its read transaction on its owning thread, including cancellation and failures.
    """
    stopped = threading.Event()
    guard = threading.Lock()
    state: dict[str, Any] = {}

    def execute() -> T:
        with engine.connect() as connection:
            raw = connection.connection.driver_connection
            with guard:
                state["raw"] = raw

            def check_cancel(*args: Any) -> None:
                if stopped.is_set():
                    raise DataAgentError("Query cancelled", status_code=409)

            event.listen(connection, "before_cursor_execute", check_cancel)
            try:
                check_cancel()
                if engine.dialect.name == "mysql":
                    connection.exec_driver_sql("SET SESSION MAX_EXECUTION_TIME = %s", (int(seconds * 1000),))
                elif engine.dialect.name == "postgresql":
                    connection.exec_driver_sql(
                        "SELECT set_config('statement_timeout', %s, true)", (str(int(seconds * 1000)),)
                    )
                return operation(connection)
            finally:
                with guard:
                    state.clear()

    def interrupt() -> None:
        # Keep the original connection checked out until cancellation is sent;
        # otherwise a pooled connection could be reused by an unrelated query.
        with guard:
            raw = state.get("raw")
            if raw is None:
                return
            if engine.dialect.name == "mysql" and callable(getattr(raw, "thread_id", None)):
                connection_id = int(raw.thread_id())
                with engine.connect() as control:
                    control.exec_driver_sql(f"KILL QUERY {connection_id}")
            elif callable(getattr(raw, "interrupt", None)):
                raw.interrupt()
            elif callable(getattr(raw, "cancel", None)):
                raw.cancel()
            # Unsupported legacy drivers wait for their driver/query timeout.
            # Do not falsely declare cancellation while a worker still runs.

    worker = asyncio.create_task(asyncio.to_thread(execute))
    try:
        return await asyncio.wait_for(asyncio.shield(worker), seconds)
    except (TimeoutError, asyncio.CancelledError):
        stopped.set()
        try:
            await asyncio.to_thread(interrupt)
        except Exception:
            logger.warning("Driver cancellation failed; waiting for query cleanup", exc_info=True)
        await asyncio.gather(worker, return_exceptions=True)
        raise
