from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Any, Dict, Optional

from ..config import settings
from ..db import exec_sql, from_json, q_all, q_one, to_json
from ..runner.engine import create_task, start_task_background
from .cron import Cron, CronError


def _now_dt() -> dt.datetime:
    return dt.datetime.utcnow().replace(second=0, microsecond=0)


def _iso(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: Optional[str]) -> Optional[dt.datetime]:
    if not s:
        return None
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


def _compute_next(expr: str, after: dt.datetime) -> dt.datetime:
    cron = Cron.parse(expr)
    return cron.next_after(after)


def tick_once() -> None:
    now = _now_dt()
    schedules = q_all("SELECT * FROM schedules WHERE enabled=1", ())
    for sch in schedules:
        sch_id = sch["id"]
        expr = sch["cron_expr"]
        next_run = _parse_iso(sch.get("next_run_at"))
        if next_run is None:
            try:
                next_run = _compute_next(expr, now - dt.timedelta(minutes=1))
                exec_sql("UPDATE schedules SET next_run_at=?, updated_at=? WHERE id=?", (_iso(next_run), _iso(now), sch_id))
            except CronError as e:
                exec_sql("UPDATE schedules SET enabled=0, updated_at=? WHERE id=?", (_iso(now), sch_id))
            continue

        if next_run <= now:
            # trigger
            payload = from_json(sch.get("payload_json")) or {}
            goal = payload.get("goal") or f"Scheduled run: {sch['name']}"
            mode = sch.get("mode", "fast")
            task_id = create_task(workspace_id=sch["workspace_id"], skill_id=sch["skill_id"], goal=goal, mode=mode)
            start_task_background(task_id)
            exec_sql("UPDATE schedules SET last_run_at=?, updated_at=? WHERE id=?", (_iso(now), _iso(now), sch_id))
            # compute next
            try:
                nxt = _compute_next(expr, now)
                exec_sql("UPDATE schedules SET next_run_at=?, updated_at=? WHERE id=?", (_iso(nxt), _iso(now), sch_id))
            except CronError:
                exec_sql("UPDATE schedules SET enabled=0, updated_at=? WHERE id=?", (_iso(now), sch_id))


class SchedulerThread(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                tick_once()
            except Exception:
                pass
            self._stop.wait(settings.scheduler_tick_seconds)

    def stop(self) -> None:
        self._stop.set()


_scheduler: Optional[SchedulerThread] = None


def start_scheduler() -> None:
    global _scheduler
    if not settings.scheduler_enabled:
        return
    if _scheduler is None:
        _scheduler = SchedulerThread()
        _scheduler.start()


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.stop()
        _scheduler = None
