"""
Centralised trace/debug logger for the GE User Level Analytics agent.

Every tool call emits structured log lines to:
  • stdout  – captured by Vertex AI Agent Engine and visible in Cloud Logging
              under the agent's default log stream.
  • Cloud Logging (best-effort) – written to the log name
              ``gemini-enterprise-agent-trace`` in the configured project.

Log format (stdout):
    [TRACE][<level>] <tag> | <key>=<value> ...

Usage inside a tool function::

    from agent.tools.trace import tracer

    with tracer.span("query_inactive_users", inactivity_days=inactivity_days) as span:
        ...do work...
        span.ok(row_count=len(rows))   # on success
        # exceptions are caught, logged, and re-raised automatically
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_LOG_NAME = "gemini-enterprise-agent-trace"
_AGENT_TAG = "ge-user-level-analytics"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt(level: str, tag: str, **fields: Any) -> str:
    parts = " | ".join(f"{k}={json.dumps(v, default=str)}" for k, v in fields.items())
    return f"[TRACE][{level}] {tag} | {parts}"


def _print(line: str) -> None:
    print(line, flush=True)


def _cloud_log(severity: str, tag: str, fields: dict[str, Any]) -> None:
    """Best-effort write to Cloud Logging — never raises."""
    try:
        from google.cloud import logging as cloud_logging  # noqa: PLC0415

        project_id = os.environ.get("GCP_PROJECT_ID", "")
        if not project_id:
            return
        client = cloud_logging.Client(project=project_id)
        entry = {"tag": tag, "agent": _AGENT_TAG, **fields}
        client.logger(_LOG_NAME).log_struct(entry, severity=severity)
    except Exception:  # noqa: BLE001
        pass  # never let trace code break the tool itself


# ---------------------------------------------------------------------------
# Span context manager
# ---------------------------------------------------------------------------


class _Span:
    def __init__(self, tag: str, inputs: dict[str, Any]) -> None:
        self.tag = tag
        self.inputs = inputs
        self._t0 = time.perf_counter()

    def _elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)

    def ok(self, **result_fields: Any) -> None:
        """Call at the successful end of a span."""
        elapsed = self._elapsed_ms()
        fields = {"elapsed_ms": elapsed, "status": "ok", **result_fields}
        _print(_fmt("END", self.tag, **fields))
        _cloud_log("INFO", self.tag, {"event": "tool_end", **fields})

    def fail(self, exc: BaseException) -> None:
        """Call when an exception is caught; logs the full traceback."""
        elapsed = self._elapsed_ms()
        tb = traceback.format_exc()
        fields = {
            "elapsed_ms": elapsed,
            "status": "error",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": tb,
        }
        _print(_fmt("ERR", self.tag, **fields))
        _cloud_log("ERROR", self.tag, {"event": "tool_error", **fields})


class _Tracer:
    """Singleton tracer used by all tools."""

    def __init__(self) -> None:
        self._env_logged = False

    def _log_env_once(self) -> None:
        """Dump key env vars on first tool call — helps diagnose config issues."""
        if self._env_logged:
            return
        self._env_logged = True
        safe_env = {
            "GCP_PROJECT_ID": os.environ.get("GCP_PROJECT_ID", "<not set>"),
            "GCP_LOCATION": os.environ.get("GCP_LOCATION", "<not set>"),
            "LOG_BUCKET": os.environ.get("LOG_BUCKET", "<not set>"),
            "LOG_VIEW": os.environ.get("LOG_VIEW", "<not set>"),
            "INACTIVITY_THRESHOLD_DAYS": os.environ.get("INACTIVITY_THRESHOLD_DAYS", "<not set>"),
            "WORKSPACE_DOMAIN": os.environ.get("WORKSPACE_DOMAIN", "<not set>"),
            "WORKSPACE_ADMIN_EMAIL": os.environ.get("WORKSPACE_ADMIN_EMAIL", "<not set>"),
            "NOTIFICATION_SENDER_EMAIL": os.environ.get("NOTIFICATION_SENDER_EMAIL", "<not set>"),
            "ORG_ADMIN_EMAILS": os.environ.get("ORG_ADMIN_EMAILS", "<not set>"),
            "GOOGLE_APPLICATION_CREDENTIALS": os.environ.get(
                "GOOGLE_APPLICATION_CREDENTIALS", "<not set>"
            ),
            "GEMINI_ENTERPRISE_PRODUCT_ID": os.environ.get(
                "GEMINI_ENTERPRISE_PRODUCT_ID", "<not set>"
            ),
            "GEMINI_ENTERPRISE_SKU_ID": os.environ.get("GEMINI_ENTERPRISE_SKU_ID", "<not set>"),
            "python_version": sys.version,
        }
        _print(_fmt("ENV", "agent_startup", **safe_env))
        _cloud_log("INFO", "agent_startup", {"event": "env_snapshot", **safe_env})

    @contextmanager
    def span(self, tag: str, **input_fields: Any) -> Generator[_Span, None, None]:
        """
        Context manager that logs CALL on entry, and END/ERR on exit.

        Usage::

            with tracer.span("my_tool", arg1=arg1, arg2=arg2) as span:
                result = do_work()
                span.ok(row_count=len(result))
        """
        self._log_env_once()
        _print(_fmt("CALL", tag, **input_fields))
        _cloud_log("DEBUG", tag, {"event": "tool_call", **input_fields})
        span = _Span(tag, input_fields)
        try:
            yield span
        except Exception as exc:  # noqa: BLE001
            span.fail(exc)
            raise

    def log(self, tag: str, message: str, **fields: Any) -> None:
        """Emit a freeform debug line mid-span."""
        _print(_fmt("DBG", tag, message=message, **fields))


tracer = _Tracer()
