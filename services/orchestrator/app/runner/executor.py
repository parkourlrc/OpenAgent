from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from ..config import settings
from ..llm import client as llm
from ..llm.prompts import EXECUTOR_SYSTEM
from ..tools.base import list_tools


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end+1])
    raise ValueError("invalid JSON")


def propose_patch(
    *,
    goal: str,
    plan: Dict[str, Any],
    current_step_idx: int,
    recent_results: List[Dict[str, Any]],
    allowed_tools: List[str],
    mode: str,
    skill_system_prompt: str = "",
) -> Optional[Dict[str, Any]]:
    model_fast = os.getenv("OPENAI_MODEL_FAST") or settings.model_fast
    model_pro = os.getenv("OPENAI_MODEL_PRO") or settings.model_pro
    model = model_fast if mode == "fast" else model_pro

    tool_specs = list_tools(allowed_tools if allowed_tools else None)
    tools_summary = "\n".join([f"- {t.name}: {t.description}" for t in tool_specs])

    sys = EXECUTOR_SYSTEM
    if skill_system_prompt:
        sys = sys + "\n\nSKILL_CONTEXT:\n" + skill_system_prompt.strip()
    sys = sys + "\n\nALLOWED_TOOLS:\n" + tools_summary

    user = {
        "goal": goal,
        "current_step_idx": current_step_idx,
        "plan": plan,
        "recent_results": recent_results[-3:],
    }

    resp = llm.chat(
        model=model,
        messages=[
            {"role": "system", "content": sys},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    data = _extract_json(resp.content)
    patch = data.get("patch")
    if patch is None:
        return None
    # basic shape check
    if not isinstance(patch, dict):
        return None
    return patch
