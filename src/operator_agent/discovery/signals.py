# -*- coding: utf-8 -*-
"""Control-plane for the discovery framework — **synchronous skeleton only**.

V8 §9 scope: the signal/event *types* and a simple publish/subscribe
dispatch interface are complete, but there is **no** real async
interruption / pause / resume.  The :class:`CancellationToken` is an
interface placeholder you can check (``is_cancelled``); it does not (yet)
preempt a running agent.

Control signals deliberately do **not** travel over the blackboard — they
ride this separate bus (V8 §4).  Agents emit :attr:`SignalType.Error` on
failure and :attr:`SignalType.Done` on completion; the supervisor subscribes
to ``Error`` to report to the user + suggest a fix (it never polls agents).
"""
from __future__ import annotations

import dataclasses
import enum
import time
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "SignalType",
    "Signal",
    "SignalBus",
    "CancellationToken",
]


class SignalType(enum.Enum):
    """Control / lifecycle events on the bus."""
    Start = "start"
    Stop = "stop"
    Pause = "pause"
    Resume = "resume"
    RevisePrompt = "revise_prompt"
    Interrupt = "interrupt"
    Error = "error"
    Done = "done"


@dataclasses.dataclass
class Signal:
    """One control/lifecycle event.

    ``type``      — the :class:`SignalType`.
    ``source``    — who emitted it (agent / stage name).
    ``payload``   — arbitrary JSON-ish data (e.g. error contract, hint).
    ``timestamp`` — epoch seconds, auto-stamped.
    """
    type: SignalType
    source: str = ""
    payload: Dict[str, Any] = dataclasses.field(default_factory=dict)
    timestamp: float = dataclasses.field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "source": self.source,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Signal":
        return cls(
            type=SignalType(d["type"]) if not isinstance(d.get("type"),
                                                          SignalType)
            else d["type"],
            source=d.get("source", ""),
            payload=dict(d.get("payload") or {}),
            timestamp=d.get("timestamp", time.time()),
        )


# A handler takes the Signal and returns nothing.
Handler = Callable[[Signal], None]


class SignalBus:
    """Synchronous publish/subscribe dispatch.

    ``subscribe(signal_type, handler)`` registers a handler for one signal
    type; ``subscribe_all(handler)`` listens to every type.  ``publish``
    (a.k.a. ``emit``) invokes matching handlers **synchronously, in
    registration order**, and records the signal in :attr:`history`.

    Handler exceptions are swallowed (a failing observer must not break the
    emitter); they are recorded in :attr:`handler_errors`.
    """

    def __init__(self) -> None:
        self._handlers: Dict[SignalType, List[Handler]] = {}
        self._global: List[Handler] = []
        self.history: List[Signal] = []
        self.handler_errors: List[Dict[str, Any]] = []

    # ---------------------- subscription ----------------------
    def subscribe(self, signal_type: SignalType, handler: Handler) -> None:
        self._handlers.setdefault(signal_type, []).append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        self._global.append(handler)

    def unsubscribe(self, signal_type: SignalType, handler: Handler) -> None:
        lst = self._handlers.get(signal_type)
        if lst and handler in lst:
            lst.remove(handler)

    # ---------------------- publish ----------------------
    def publish(self, signal: Signal) -> Signal:
        """Dispatch ``signal`` to matching handlers (synchronously)."""
        self.history.append(signal)
        for handler in list(self._handlers.get(signal.type, [])) + \
                list(self._global):
            try:
                handler(signal)
            except Exception as exc:  # observer failure must not propagate
                self.handler_errors.append({
                    "signal": signal.to_dict(),
                    "error": repr(exc),
                })
        return signal

    # Convenience emitter.
    def emit(self, signal_type: SignalType, source: str = "",
             **payload: Any) -> Signal:
        return self.publish(Signal(type=signal_type, source=source,
                                   payload=dict(payload)))

    def emit_error(self, source: str, **payload: Any) -> Signal:
        return self.emit(SignalType.Error, source=source, **payload)

    def emit_done(self, source: str, **payload: Any) -> Signal:
        return self.emit(SignalType.Done, source=source, **payload)

    def history_of(self, signal_type: SignalType) -> List[Signal]:
        return [s for s in self.history if s.type == signal_type]


class CancellationToken:
    """Cooperative cancellation **interface placeholder**.

    NOT truly async: setting it does not preempt a running agent.  Agents
    are expected to *poll* :attr:`is_cancelled` at safe checkpoints in a
    later phase.  Here it only stores the flag + an optional reason.
    """

    def __init__(self) -> None:
        self._cancelled = False
        self.reason: Optional[str] = None

    def cancel(self, reason: Optional[str] = None) -> None:
        self._cancelled = True
        self.reason = reason

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def reset(self) -> None:
        self._cancelled = False
        self.reason = None

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise RuntimeError(
                f"operation cancelled: {self.reason or 'no reason given'}")
