from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from .config import settings
from .llm import client as llm


def _normalize(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _looks_like_placeholder_key(key: str) -> bool:
    k = (key or "").strip()
    if not k:
        return True
    if k in ("sk-your-key", "your-key", "changeme"):
        return True
    if k.startswith("sk-your-") or k.startswith("sk-xxx"):
        return True
    return False


def _heuristic_choose(goal: str, skills: List[Dict[str, Any]]) -> str:
    """
    Fast, offline routing fallback: score skills by keyword overlap against name/description/yaml_path.
    """
    g = _normalize(goal)
    if not g:
        return skills[0]["id"]

    keyword_groups = [
        (("research", "report", "paper", "survey", "search", "crawl", "deep research", "调研", "研究", "论文", "报告", "检索"), 3),
        (("file", "folder", "cleanup", "organize", "整理", "归档", "文件", "目录"), 3),
        (("media", "image", "audio", "video", "生成", "配音", "图片", "视频", "音频"), 2),
        (("code", "build", "debug", "repo", "项目", "代码", "修复", "开发"), 2),
    ]

    best = (skills[0]["id"], -1)
    for s in skills:
        text = " ".join(
            [
                str(s.get("name") or ""),
                str(s.get("description") or ""),
                str(s.get("yaml_path") or ""),
            ]
        )
        t = _normalize(text)
        score = 0
        for keys, w in keyword_groups:
            for k in keys:
                if k in g and k in t:
                    score += w
        # generic overlap
        for token in set(re.split(r"[^a-z0-9\u4e00-\u9fff]+", g)):
            if token and token in t and len(token) >= 2:
                score += 1
        if score > best[1]:
            best = (s["id"], score)
    return best[0]


def choose_skill_id(*, goal: str, skills: List[Dict[str, Any]], hint: Optional[str] = None, mode: str = "fast") -> str:
    """
    Choose the best skill for a goal. Falls back to the first skill if routing fails.
    Returns a skill_id.
    """
    if not skills:
        raise ValueError("no skills available")
    if len(skills) == 1:
        return skills[0]["id"]

    # If provider is not configured, do not block UX on LLM routing.
    api_key = os.getenv("OPENAI_API_KEY") or settings.llm_api_key
    base_url = os.getenv("OPENAI_BASE_URL") or settings.llm_base_url
    if _looks_like_placeholder_key(api_key) or not base_url:
        return _heuristic_choose(goal, skills)

    model_fast = os.getenv("OPENAI_MODEL_FAST") or settings.model_fast
    model_pro = os.getenv("OPENAI_MODEL_PRO") or settings.model_pro
    model = model_fast if mode == "fast" else model_pro

    options = [{"id": s["id"], "name": s.get("name", ""), "description": s.get("description") or ""} for s in skills]
    sys = (
        "You are a router that selects the best skill for the user's goal.\n"
        "Pick exactly ONE skill id from the provided list.\n"
        'Return ONLY JSON: {"skill_id": "...", "reason": "..."}\n'
        "Do not include any other keys."
    )
    user: Dict[str, Any] = {"goal": goal, "skills": options}
    if hint:
        user["hint"] = hint

    try:
        resp = llm.chat(
            model=model,
            messages=[{"role": "system", "content": sys}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
            temperature=0.0,
            response_format={"type": "json_object"},
            timeout_s=4,
        )
        data = json.loads(resp.content or "{}")
        skill_id = str(data.get("skill_id") or "").strip()
        if any(s["id"] == skill_id for s in skills):
            return skill_id
    except Exception:
        pass

    return _heuristic_choose(goal, skills)
