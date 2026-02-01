from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import settings
from ..llm import client as llm
from ..llm.prompts import CRITIC_SYSTEM


def _extract_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end+1])
    raise ValueError("invalid JSON")


def review(
    *,
    goal: str,
    plan: Dict[str, Any],
    artifacts: List[Dict[str, Any]],
    mode: str,
    skill_system_prompt: str = "",
) -> Dict[str, Any]:
    model_fast = os.getenv("OPENAI_MODEL_FAST") or settings.model_fast
    model_pro = os.getenv("OPENAI_MODEL_PRO") or settings.model_pro
    model = model_fast if mode == "fast" else model_pro
    payload = {"goal": goal, "plan": plan, "artifacts": artifacts}
    resp = llm.chat(
        model=model,
        messages=[
            {"role": "system", "content": (CRITIC_SYSTEM + ("\n\nSKILL_CONTEXT:\n" + skill_system_prompt.strip() if skill_system_prompt else ""))},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return _extract_json(resp.content)
