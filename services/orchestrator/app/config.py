from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    return val


@dataclass(frozen=True)
class Settings:
    # App
    app_name: str = _env("APP_NAME", "OpenAgent Workbench")
    host: str = _env("APP_HOST", "0.0.0.0")
    port: int = int(_env("APP_PORT", "8787"))

    # Storage
    data_dir: Path = Path(_env("DATA_DIR", str(Path.cwd() / "data"))).resolve()
    db_path: Path = Path(_env("DB_PATH", str(Path(_env("DATA_DIR", str(Path.cwd() / "data"))) / "workbench.db"))).resolve()
    workspaces_dir: Path = Path(_env("WORKSPACES_DIR", str(Path(_env("DATA_DIR", str(Path.cwd() / "data"))) / "workspaces"))).resolve()
    artifacts_dir: Path = Path(_env("ARTIFACTS_DIR", str(Path(_env("DATA_DIR", str(Path.cwd() / "data"))) / "artifacts"))).resolve()
    logs_dir: Path = Path(_env("LOGS_DIR", str(Path(_env("DATA_DIR", str(Path.cwd() / "data"))) / "logs"))).resolve()

    # LLM provider (OpenAI-compatible)
    llm_base_url: str = _env("OPENAI_BASE_URL", "https://0-0.pro/v1")
    llm_api_key: str = _env("OPENAI_API_KEY", "CHANGE_ME")
    model_fast: str = _env("OPENAI_MODEL_FAST", "gpt-4o-mini")
    model_pro: str = _env("OPENAI_MODEL_PRO", "gpt-4o")
    model_vision: str = _env("OPENAI_MODEL_VISION", _env("OPENAI_MODEL_PRO", "gpt-4o"))
    model_embeddings: str = _env("OPENAI_MODEL_EMBEDDINGS", "text-embedding-3-small")

    # Optional modality-specific models (still via OpenAI-compatible provider)
    model_image: str = _env("OPENAI_MODEL_IMAGE", _env("OPENAI_MODEL_PRO", "gpt-4o"))
    model_audio_transcribe: str = _env("OPENAI_MODEL_AUDIO_TRANSCRIBE", _env("OPENAI_MODEL_PRO", "gpt-4o"))
    model_audio_speech: str = _env("OPENAI_MODEL_AUDIO_SPEECH", _env("OPENAI_MODEL_PRO", "gpt-4o"))
    model_video: str = _env("OPENAI_MODEL_VIDEO", _env("OPENAI_MODEL_PRO", "gpt-4o"))

    # Execution and safety
    # Root to confine filesystem tool operations; if empty, uses workspace root.
    fs_allow_outside_workspace: bool = _env("FS_ALLOW_OUTSIDE_WORKSPACE", "false").lower() in ("1", "true", "yes")
    shell_allow: bool = _env("SHELL_ALLOW", "true").lower() in ("1", "true", "yes")
    shell_docker_backend: bool = _env("SHELL_DOCKER_BACKEND", "false").lower() in ("1", "true", "yes")
    shell_docker_image: str = _env("SHELL_DOCKER_IMAGE", "python:3.11-slim")

    # Browser automation
    browser_enabled: bool = _env("BROWSER_ENABLED", "true").lower() in ("1", "true", "yes")
    browser_headless: bool = _env("BROWSER_HEADLESS", "true").lower() in ("1", "true", "yes")
    browser_timeout_ms: int = int(_env("BROWSER_TIMEOUT_MS", "45000"))

    # Human-in-the-loop approvals
    require_approval_shell: bool = _env("REQUIRE_APPROVAL_SHELL", "true").lower() in ("1", "true", "yes")
    require_approval_fs_write: bool = _env("REQUIRE_APPROVAL_FS_WRITE", "true").lower() in ("1", "true", "yes")
    require_approval_fs_delete: bool = _env("REQUIRE_APPROVAL_FS_DELETE", "true").lower() in ("1", "true", "yes")
    require_approval_browser_click: bool = _env("REQUIRE_APPROVAL_BROWSER_CLICK", "true").lower() in ("1", "true", "yes")

    # Scheduler
    scheduler_enabled: bool = _env("SCHEDULER_ENABLED", "true").lower() in ("1", "true", "yes")
    scheduler_tick_seconds: int = int(_env("SCHEDULER_TICK_SECONDS", "5"))

    # Optional simple auth for UI
    ui_admin_token: str = _env("UI_ADMIN_TOKEN", "admin")


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
settings.logs_dir.mkdir(parents=True, exist_ok=True)
