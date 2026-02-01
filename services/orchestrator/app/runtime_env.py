from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional


ALLOWED_KEYS = {
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL_FAST",
    "OPENAI_MODEL_PRO",
    "OPENAI_MODEL_VISION",
    "OPENAI_MODEL_EMBEDDINGS",
    "OPENAI_MODEL_IMAGE",
    "OPENAI_MODEL_AUDIO_TRANSCRIBE",
    "OPENAI_MODEL_AUDIO_SPEECH",
    "OPENAI_MODEL_VIDEO",
    # UAK runtime behavior
    "UAK_CITATIONS_MODE",  # auto | off | require
    # Desktop host/client connection settings
    "OWB_HOST_MODE",        # local | remote
    "OWB_REMOTE_URL",       # e.g. https://workbench.company.com
    "OWB_REMOTE_TOKEN",     # optional admin token for remote host
}


def _data_dir_fallback() -> Path:
    # If DATA_DIR is not set, fall back to repo-local ./data.
    return Path(os.getenv("DATA_DIR") or (Path.cwd() / "data")).resolve()


def _path() -> Path:
    return _data_dir_fallback() / "runtime_env.json"


def load_runtime_env() -> Dict[str, str]:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in data.items():
        if k in ALLOWED_KEYS and isinstance(v, str):
            out[k] = v
    return out


def apply_runtime_env() -> Dict[str, str]:
    applied = load_runtime_env()
    for k, v in applied.items():
        if v != "":
            os.environ[k] = v

    # Force UAK web-search policy to step-wise "auto" mode for all runs.
    # (This prevents host environment or other configs from switching it to always/off.)
    os.environ["UAK_WEB_SEARCH_POLICY"] = "auto"

    return applied


def update_runtime_env(updates: Dict[str, Optional[str]]) -> Dict[str, str]:
    cur = load_runtime_env()
    for k, v in updates.items():
        if k not in ALLOWED_KEYS:
            continue
        if v is None:
            continue
        cur[k] = str(v)
        if v != "":
            os.environ[k] = str(v)
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return cur
