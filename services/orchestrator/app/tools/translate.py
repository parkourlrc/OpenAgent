from __future__ import annotations

from typing import Any, Dict, Optional

from ..config import settings
from ..llm import client as llm
from .base import ToolContext, ToolSpec, register


def translate_text(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    text = args["text"]
    target = args.get("target_language", "English")
    source = args.get("source_language")
    style = args.get("style", "natural")
    model = args.get("model", settings.model_fast)
    sys = "You are a professional translator. Translate accurately and preserve meaning, tone, and formatting."
    user = f"Translate the following text to {target}."
    if source:
        user += f" The source language is {source}."
    user += f" Style: {style}.\n\nTEXT:\n{text}"
    resp = llm.chat(model=model, messages=[{"role": "system", "content": sys}, {"role": "user", "content": user}], temperature=0.2)
    return {"ok": True, "translated": resp.content}


def register_translate_tools() -> None:
    register(
        ToolSpec(
            name="translate.text",
            description="Translate text to a target language using the OpenAI-compatible LLM.",
            json_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "target_language": {"type": "string", "default": "English"},
                    "source_language": {"type": "string"},
                    "style": {"type": "string", "default": "natural"},
                    "model": {"type": "string"},
                },
                "required": ["text", "target_language"],
            },
            func=translate_text,
            risky=False,
        )
    )
