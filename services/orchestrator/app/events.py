from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Event:
    type: str
    data: Dict[str, Any]
    ts: float


# Thread-safe subscriber queues
_subscribers: List["queue.Queue[Event]"] = []
_lock = threading.Lock()


def subscribe() -> "queue.Queue[Event]":
    q: "queue.Queue[Event]" = queue.Queue()
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: "queue.Queue[Event]") -> None:
    with _lock:
        if q in _subscribers:
            _subscribers.remove(q)


def emit(event_type: str, data: Dict[str, Any]) -> None:
    ev = Event(type=event_type, data=data, ts=time.time())
    with _lock:
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(ev)
        except Exception:
            pass


def format_sse(ev: Event) -> str:
    payload = json.dumps({"type": ev.type, "data": ev.data, "ts": ev.ts}, ensure_ascii=False)
    return f"event: {ev.type}\ndata: {payload}\n\n"
