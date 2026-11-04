"""Standard server logs for this app: one line per task, step and provider call, on the server's output.

    2026-10-15T07:21:07.412Z INFO  <app>.provider task=<task> provider=openai op=tts model=gpt-4o-mini-tts status=success duration_ms=1840 bytes=69120

ISO 8601 UTC timestamps with milliseconds, the level, the logger name, then key=value fields.
Output only (no database, no UI). Secrets are masked and prompts / document text are never logged,
only their sizes. Logging never raises: it can't change what the app does.

Usage::

    from <package>.std_logs import call, task, event, run_task, mark_failed

    async with task("generate", project_id=p.id):          # a job / request-level unit of work
        async with call("openai", "tts", model="gpt-4o-mini-tts", chars=len(text)) as info:
            r = await client.post(...)
            info["http_status"] = r.status_code             # add response details; the error is logged on failure

    event("upload", status="success", bytes=n)             # a one-off record
"""

from __future__ import annotations

import contextvars
import logging
import re
import sys
import time
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Iterator

ROOT = "pltt_video_studio_2"  # the app's own top-level logger name, e.g. "newsletter"
_provider_log = logging.getLogger(f"{ROOT}.provider")
_task_log = logging.getLogger(f"{ROOT}.task")


def iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


class IsoFormatter(logging.Formatter):
    """``<ISO-8601 UTC> <LEVEL> <logger> <message>``."""

    def format(self, record: logging.LogRecord) -> str:
        line = f"{iso(datetime.fromtimestamp(record.created, tz=timezone.utc))} {record.levelname:<5} {record.name} {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def setup_logging(level: int = logging.INFO) -> None:
    """Standard format for this app's loggers (idempotent: safe on every reload; other apps untouched)."""
    root = logging.getLogger(ROOT)
    if any(getattr(h, "_std_logs", False) for h in root.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(IsoFormatter())
    handler._std_logs = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


_SECRET = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._\-]+|((?:api[_-]?key|xi-api-key|key|token|secret|password|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s\"',&]+"
    r"|\b(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,})")


def redact(text: Any, limit: int = 300) -> str:
    s = str(text or "")
    s = _SECRET.sub(lambda m: (m.group(1) or m.group(2) or "") + "***", s)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _fmt(v: Any) -> str:
    s = str(v)
    return f'"{s}"' if (" " in s or not s) else s


_task: contextvars.ContextVar[dict] = contextvars.ContextVar(f"{ROOT}_std_task", default={})
_task_info: contextvars.ContextVar[dict | None] = contextvars.ContextVar(f"{ROOT}_std_task_info", default=None)


def _emit(kind: str, name: str, status: str, *, level: str | None = None, provider: str = "", model: str = "",
          duration_ms: int | None = None, message: str = "", detail: dict | None = None) -> None:
    try:
        ctx = _task.get()
        level = level or ("ERROR" if status == "failed" else "INFO")
        fields = [("task", ctx.get("task")), ("project", ctx.get("id")), ("kind", kind), ("name", name),
                  ("provider", provider), ("model", model), ("status", status), ("duration_ms", duration_ms)]
        taken = {k for k, _ in fields}
        fields += [(f"{k}_" if k in taken else k, redact(v, 120) if isinstance(v, str) else v) for k, v in (detail or {}).items()]
        line = " ".join(f"{k}={_fmt(v)}" for k, v in fields if v not in (None, ""))
        if message:
            line += f" msg={_fmt(redact(message))}"
        (_provider_log if kind == "provider" else _task_log).log(getattr(logging, level, logging.INFO), line)
    except Exception:  # logging must never break the app
        pass


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _failure(exc: BaseException) -> tuple[str, str]:
    name = type(exc).__name__
    return ("cancelled", name) if "cancel" in name.lower() else ("failed", f"{name}: {exc}")


@asynccontextmanager
async def call(provider: str, operation: str, /, *, model: str = "", **detail: Any) -> AsyncIterator[dict]:
    """One provider call (AI model, voice, image/video service, storage, external API). Yields a dict for response
    details; logs success with its duration, or the error (which always propagates unchanged)."""
    info: dict = dict(detail)
    t0 = time.perf_counter()
    try:
        yield info
    except BaseException as exc:
        status, msg = _failure(exc)
        _emit("provider", operation, status, level="WARNING" if status == "cancelled" else "ERROR", provider=provider,
              model=str(info.pop("model", model) or ""), duration_ms=_ms(t0), message=msg, detail=info)
        raise
    _emit("provider", operation, str(info.pop("status", "success")), provider=provider,
          model=str(info.pop("model", model) or ""), duration_ms=_ms(t0), message=str(info.pop("message", "")), detail=info)


@contextmanager
def call_sync(provider: str, operation: str, /, *, model: str = "", **detail: Any) -> Iterator[dict]:
    """`call` for synchronous code."""
    info: dict = dict(detail)
    t0 = time.perf_counter()
    try:
        yield info
    except BaseException as exc:
        status, msg = _failure(exc)
        _emit("provider", operation, status, level="WARNING" if status == "cancelled" else "ERROR", provider=provider,
              model=str(info.pop("model", model) or ""), duration_ms=_ms(t0), message=msg, detail=info)
        raise
    _emit("provider", operation, str(info.pop("status", "success")), provider=provider,
          model=str(info.pop("model", model) or ""), duration_ms=_ms(t0), message=str(info.pop("message", "")), detail=info)


@asynccontextmanager
async def task(name: str, /, *, project_id: Any = None, **detail: Any) -> AsyncIterator[dict]:
    """A unit of work (a job, a generation, a request that calls providers): started, then success / failed with
    its duration. Provider calls inside it carry its task name and id. Set info["status"]="failed" (and
    info["message"]) for a task that reports failure without raising."""
    token = _task.set({"task": name, "id": str(project_id)[:12] if project_id else None})
    info: dict = {}
    info_token = _task_info.set(info)
    t0 = time.perf_counter()
    _emit("task", name, "started", detail=detail)
    try:
        yield info
    except BaseException as exc:
        status, msg = _failure(exc)
        _emit("task", name, status, level="WARNING" if status == "cancelled" else "ERROR", duration_ms=_ms(t0), message=msg)
        raise
    else:
        status = str(info.pop("status", "success"))
        _emit("task", name, status, level={"failed": "ERROR", "cancelled": "WARNING"}.get(status, "INFO"),
              duration_ms=_ms(t0), message=str(info.pop("message", "")), detail=info)
    finally:
        _task.reset(token)
        try:
            _task_info.reset(info_token)
        except Exception:
            pass


async def run_task(name: str, coro: Any, /, *, project_id: Any = None, **detail: Any) -> Any:
    """Await `coro` inside `task(name)` and return its result unchanged (exceptions propagate unchanged).
    Use as ``asyncio.create_task(run_task("job", _bg(), project_id=pid))``."""
    async with task(name, project_id=project_id, **detail):
        return await coro


def mark_failed(message: Any = "") -> None:
    """Mark the current task as failed (for jobs that catch their own error and don't re-raise)."""
    try:
        info = _task_info.get()
        if info is not None:
            info["status"] = "failed"
            info["message"] = str(message)
    except Exception:
        pass


def size(value: Any) -> int | None:
    """len(value), or None when it has no length (safe to use when filling log details)."""
    try:
        return len(value)
    except Exception:
        return None


def llm_usage(response: Any) -> dict:
    """Token counts from an OpenAI / Anthropic / Gemini HTTP response (or its parsed JSON); {} if unknown.
    Never raises."""
    try:
        data = response.json() if hasattr(response, "json") and callable(response.json) else response
        if not isinstance(data, dict):
            return {}
        u = data.get("usage")
        if isinstance(u, dict) and u:
            pairs = (("tokens_in", u.get("prompt_tokens", u.get("input_tokens"))),
                     ("tokens_out", u.get("completion_tokens", u.get("output_tokens"))))
        else:
            m = data.get("usageMetadata")
            if not isinstance(m, dict):
                return {}
            pairs = (("tokens_in", m.get("promptTokenCount")), ("tokens_out", m.get("candidatesTokenCount")))
        return {k: v for k, v in pairs if isinstance(v, int)}
    except Exception:
        return {}


def event(name: str, message: str = "", /, *, level: str = "INFO", status: str = "", **detail: Any) -> None:
    """A one-off record (an upload, a fallback taken, a warning shown to the user)."""
    _emit("event", name, status, level=level, message=message, detail=detail)
