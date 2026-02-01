from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from ..config import settings
from ..llm import client as llm
from ..llm.prompts import PLANNER_SYSTEM
from ..tools.base import ToolSpec, list_tools, openai_tool_schema


class PlanError(RuntimeError):
    pass


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    # try to find first {...}
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end+1])
    raise PlanError("Unable to parse JSON from planner output")


def generate_plan(*, goal: str, allowed_tools: List[str], mode: str, skill_system_prompt: str = "") -> Dict[str, Any]:
    model_fast = os.getenv("OPENAI_MODEL_FAST") or settings.model_fast
    model_pro = os.getenv("OPENAI_MODEL_PRO") or settings.model_pro
    model = model_fast if mode == "fast" else model_pro

    # Provide the allowed tools list to planner (names + short descriptions)
    tool_specs = list_tools(allowed_tools if allowed_tools else None)
    tools_summary = "\n".join([f"- {t.name}: {t.description}" for t in tool_specs])
    sys = PLANNER_SYSTEM
    if skill_system_prompt:
        sys = sys + "\n\nSKILL_CONTEXT:\n" + skill_system_prompt.strip()
    sys = sys + "\n\nALLOWED_TOOLS:\n" + tools_summary

    user = f"GOAL:\n{goal}\n\nReturn only strict JSON as specified."

    resp = llm.chat(
        model=model,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    try:
        plan = _extract_json(resp.content)
    except Exception:
        # retry with a stricter repair prompt
        repair_sys = "You output invalid JSON. Output ONLY valid JSON for the plan schema. No markdown."
        resp2 = llm.chat(
            model=model,
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": user},
                {"role": "assistant", "content": resp.content},
                {"role": "system", "content": repair_sys},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        plan = _extract_json(resp2.content)

    # basic validation
    if "steps" not in plan or not isinstance(plan["steps"], list) or len(plan["steps"]) == 0:
        raise PlanError("Plan must include non-empty steps")
    for s in plan["steps"]:
        if "tool" not in s or "args" not in s:
            raise PlanError("Each step must include tool and args")
        if allowed_tools and s["tool"] not in allowed_tools:
            raise PlanError(f"Step tool not allowed: {s['tool']}")
        if "requires_approval" not in s:
            s["requires_approval"] = False

    if "artifacts" not in plan:
        plan["artifacts"] = []
    if "summary" not in plan:
        plan["summary"] = "Run"

    return plan
