from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple


class CronError(ValueError):
    pass


def _parse_field(field: str, min_v: int, max_v: int) -> Set[int]:
    field = field.strip()
    if field == "*":
        return set(range(min_v, max_v + 1))

    values: Set[int] = set()
    parts = field.split(",")
    for part in parts:
        part = part.strip()
        if part == "*":
            values.update(range(min_v, max_v + 1))
            continue
        if part.startswith("*/"):
            step = int(part[2:])
            if step <= 0:
                raise CronError("invalid step")
            values.update(range(min_v, max_v + 1, step))
            continue
        # range with step, e.g. 1-10/2
        if "/" in part:
            rng, step_s = part.split("/", 1)
            step = int(step_s)
            if "-" in rng:
                a_s, b_s = rng.split("-", 1)
                a, b = int(a_s), int(b_s)
            else:
                a, b = int(rng), max_v
            if a < min_v or b > max_v or a > b:
                raise CronError("invalid range")
            values.update(range(a, b + 1, step))
            continue
        if "-" in part:
            a_s, b_s = part.split("-", 1)
            a, b = int(a_s), int(b_s)
            if a < min_v or b > max_v or a > b:
                raise CronError("invalid range")
            values.update(range(a, b + 1))
            continue
        v = int(part)
        if v < min_v or v > max_v:
            raise CronError("value out of range")
        values.add(v)
    return values


@dataclass
class Cron:
    minutes: Set[int]
    hours: Set[int]
    dom: Set[int]          # day of month 1-31
    months: Set[int]       # month 1-12
    dow: Set[int]          # day of week 0-7 (cron semantics: 0 or 7 = Sunday, 1=Monday, ..., 6=Saturday). Stored as 0-6 with 0=Sunday.

    @staticmethod
    def parse(expr: str) -> "Cron":
        parts = [p for p in expr.strip().split() if p]
        if len(parts) != 5:
            raise CronError("Cron must have 5 fields: min hour dom month dow")
        minute_s, hour_s, dom_s, month_s, dow_s = parts
        minutes = _parse_field(minute_s, 0, 59)
        hours = _parse_field(hour_s, 0, 23)
        dom = _parse_field(dom_s, 1, 31)
        months = _parse_field(month_s, 1, 12)
        # Support cron DOW: 0-7 where 0 or 7 = Sunday.
        dow_vals = _parse_field(dow_s, 0, 7)
        dow_norm = {0 if v == 7 else v for v in dow_vals}
        return Cron(minutes=minutes, hours=hours, dom=dom, months=months, dow=dow_norm)

    def matches(self, t: dt.datetime) -> bool:
        cron_dow = (t.weekday() + 1) % 7  # Sunday=0
        return (t.minute in self.minutes and t.hour in self.hours and t.day in self.dom and t.month in self.months and cron_dow in self.dow)

    def next_after(self, after: dt.datetime, *, max_lookahead_days: int = 366) -> dt.datetime:
        # Brute-force search minute by minute (good enough for small workloads)
        t = after.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        end = after + dt.timedelta(days=max_lookahead_days)
        while t <= end:
            if self.matches(t):
                return t
            t += dt.timedelta(minutes=1)
        raise CronError("no matching time found within lookahead window")
