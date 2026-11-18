"""Action handlers and their dispatcher.

Handlers are referenced from the intent table as "module.function" and are
resolved lazily, so a broken or unavailable backend cannot stop ODIN starting.
Every handler takes (ctx, slots) and returns either a string to speak or None
to fall back to the intent's own reply.
"""

from __future__ import annotations

import importlib
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("odin.actions")

_MODULES = ("media", "volume", "apps", "places", "system", "meta")


class ActionError(RuntimeError):
    """A command was understood but could not be carried out."""


@dataclass
class Context:
    cfg: Any
    speaker: Any = None
    standby: bool = False
    # State captured before a reversible action, for "undo that".
    undo: dict | None = None


def run_command(argv: list[str], timeout: float = 5.0) -> str:
    """Run a helper program, raising ActionError with a usable message."""
    if shutil.which(argv[0]) is None:
        raise ActionError(f"{argv[0]} is not installed")
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise ActionError(f"{argv[0]} timed out") from exc
    except OSError as exc:
        raise ActionError(f"could not run {argv[0]}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        message = detail[0] if detail else f"exit code {proc.returncode}"
        raise ActionError(message)
    return (proc.stdout or "").strip()


def resolve(handler: str) -> Callable[[Context, dict], str | None]:
    module_name, _, function_name = handler.rpartition(".")
    if not module_name or module_name not in _MODULES:
        raise ActionError(f"unknown handler module: {handler}")
    module = importlib.import_module(f".{module_name}", __package__)
    function = getattr(module, function_name, None)
    if function is None:
        raise ActionError(f"unknown handler: {handler}")
    return function


def dispatch(handler: str, ctx: Context, slots: dict) -> str | None:
    return resolve(handler)(ctx, slots)
