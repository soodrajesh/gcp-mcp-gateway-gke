"""One structured JSON log line per tool call: who (the gateway identity), which tool, outcome, duration.
Argument VALUES are never logged (they can carry secrets or personal data); only the argument NAMES."""
from __future__ import annotations

import contextvars
import functools
import json
import sys
import time

caller_var: contextvars.ContextVar[str] = contextvars.ContextVar("caller", default="unknown")


def emit(**fields) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}), file=sys.stdout, flush=True)


def audited(server: str):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0, outcome = time.monotonic(), "ok"
            try:
                return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 - recorded, then re-raised to the MCP layer
                outcome = f"error:{type(e).__name__}"
                raise
            finally:
                emit(event="tool_call", server=server, tool=fn.__name__, caller=caller_var.get(),
                     arg_names=sorted(kwargs), outcome=outcome, ms=round((time.monotonic() - t0) * 1000))
        return wrapper
    return deco
