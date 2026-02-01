from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pdfplumber
from docx import Document

from .base import ToolContext, ToolSpec, register
from ..config import settings


def _normalize_rel_path(rel_path: str) -> str:
    s = str(rel_path or "").strip()
    if not s:
        return "."
    s2 = s.replace("\\", "/")
    while s2.startswith("./"):
        s2 = s2[2:]
    if s2.startswith("workspace/"):
        s2 = s2[len("workspace/") :]
    return s2 or "."


def _read_pdf(path: Path, max_chars: int) -> str:
    parts = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            if txt:
                parts.append(txt)
            if sum(len(p) for p in parts) >= max_chars:
                break
    text = "\n\n".join(parts)
    return text[:max_chars]


def _read_docx(path: Path, max_chars: int) -> str:
    doc = Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text]
    text = "\n".join(parts)
    return text[:max_chars]


def _read_text(path: Path, max_chars: int) -> str:
    data = path.read_bytes()
    try:
        t = data.decode("utf-8")
    except UnicodeDecodeError:
        t = data.decode("utf-8", errors="replace")
    return t[:max_chars]


def docs_parse(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    rel = _normalize_rel_path(args["path"])
    max_chars = int(args.get("max_chars", 200000))
    p = (ctx.workspace_root / rel).resolve()
    if not str(p).startswith(str(ctx.workspace_root.resolve())):
        raise ValueError("path escapes workspace")
    if not p.exists():
        raise FileNotFoundError(str(p))
    ext = p.suffix.lower()
    if ext == ".pdf":
        text = _read_pdf(p, max_chars=max_chars)
        kind = "pdf"
    elif ext in (".docx",):
        text = _read_docx(p, max_chars=max_chars)
        kind = "docx"
    else:
        text = _read_text(p, max_chars=max_chars)
        kind = "text"
    truncated = len(text) >= max_chars
    return {"ok": True, "path": rel, "type": kind, "truncated": truncated, "text": text}


def register_docs_tools() -> None:
    register(
        ToolSpec(
            name="docs.parse",
            description="Parse a document (PDF/DOCX/TXT) from workspace and return extracted text.",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 200000},
                },
                "required": ["path"],
            },
            func=docs_parse,
            risky=False,
        )
    )
