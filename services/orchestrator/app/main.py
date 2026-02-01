from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import shutil
import subprocess
import zipfile
import time
import uuid
from pathlib import Path
import mimetypes
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import settings
from .events import subscribe, unsubscribe, format_sse
from .db import init_db, exec_sql, from_json, q_all, q_one, to_json
from .schemas import (
    ApprovalDecision,
    KBIngestRequest,
    KBQueryRequest,
    ScheduleCreate,
    SkillCreate,
    SkillImport,
    TaskCreate,
    TaskContinue,
    WorkspaceCreate,
)
from pydantic import BaseModel
from .tools import register_all_tools
from .runner.engine import (
    approve_step,
    cancel_task,
    continue_task_background,
    create_skill,
    create_task,
    create_workspace,
    start_task_background,
)
from .scheduler.scheduler import start_scheduler
from .tools.base import list_tools
from .tools.rag import kb_ingest, kb_query
from .i18n import detect_lang, normalize_lang, t as tr, with_lang, SUPPORTED_LANGS
from .runtime_env import apply_runtime_env, update_runtime_env
from .skill_router import choose_skill_id
from .permissions import POLICIES, SCOPES, get_workspace_policies, set_workspace_policy


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "webui" / "templates"
STATIC_DIR = BASE_DIR / "webui" / "static"
STATIC_VERSION = str(int(time.time()))

try:
    from .build_info import BUILD_ID as APP_BUILD_ID, BUILD_TIME as APP_BUILD_TIME
except Exception:
    APP_BUILD_ID = "dev"
    APP_BUILD_TIME = ""


_LOGGING_CONFIGURED = False
_SUBPROCESS_NO_WINDOW_PATCHED = False


def _patch_subprocess_no_window_once() -> None:
    """
    Desktop UX: prevent child console windows (e.g., node/playwright) from popping up on Windows.

    In PyInstaller `--windowed` mode the parent process has no console. Spawning console-subsystem
    programs may create a visible window unless CREATE_NO_WINDOW is set.
    """
    global _SUBPROCESS_NO_WINDOW_PATCHED
    if _SUBPROCESS_NO_WINDOW_PATCHED:
        return
    if os.name != "nt":
        return
    if str(os.getenv("OWB_DESKTOP") or "").strip() != "1":
        return
    # Allow opting out for local debugging.
    if str(os.getenv("OWB_ALLOW_CONSOLE_WINDOWS") or "").strip() == "1":
        return

    try:
        orig_popen = subprocess.Popen
    except Exception:
        return

    # If already patched (e.g. dev reload), do nothing.
    try:
        if getattr(subprocess.Popen, "__name__", "") == "_OwbPopenNoWindow":
            _SUBPROCESS_NO_WINDOW_PATCHED = True
            return
    except Exception:
        pass

    CREATE_NO_WINDOW = 0x08000000

    class _OwbPopenNoWindow(orig_popen):  # type: ignore[misc]
        """
        Popen subclass that defaults to CREATE_NO_WINDOW on Windows.

        Must remain a class (not a function wrapper) because some deps (e.g. MCP)
        subscript `subprocess.Popen[...]` at runtime on Python 3.11+.
        """

        def __init__(self, *args: Any, **kwargs: Any):  # noqa: D401
            try:
                cf = kwargs.get("creationflags", 0)
                if cf is None:
                    cf = 0
                kwargs["creationflags"] = int(cf) | int(CREATE_NO_WINDOW)
            except Exception:
                kwargs["creationflags"] = int(CREATE_NO_WINDOW)
            super().__init__(*args, **kwargs)

    subprocess.Popen = _OwbPopenNoWindow  # type: ignore[assignment]
    _SUBPROCESS_NO_WINDOW_PATCHED = True


def _configure_logging_once() -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    _LOGGING_CONFIGURED = True
    try:
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    log_path = settings.logs_dir / "workbench.log"
    try:
        handler = RotatingFileHandler(str(log_path), maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        # Ensure Uvicorn logs also land in the file (in addition to the desktop_shell stdout/stderr logs).
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            try:
                lg = logging.getLogger(name)
                lg.setLevel(logging.INFO)
                lg.addHandler(handler)
            except Exception:
                continue
        logging.getLogger("owb").info("logging_initialized build_id=%s build_time=%s", APP_BUILD_ID, APP_BUILD_TIME)
    except Exception:
        # Never crash the app due to logging setup.
        pass

    # Ensure the client log exists so users always have a readable file to attach when reporting UI issues.
    try:
        (settings.logs_dir / "desktop-client.log").touch(exist_ok=True)
    except Exception:
        pass

jinja = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

# Jinja helper: JSON pretty-print
jinja.filters["tojson"] = lambda v, indent=2: json.dumps(v, ensure_ascii=False, indent=indent)

app = FastAPI(title=settings.app_name)
_patch_subprocess_no_window_once()
_configure_logging_once()


def _uak_db_path() -> Path:
    return (settings.data_dir / "uak.db").resolve()


def _b64url_encode(raw: str) -> str:
    b = str(raw or "").encode("utf-8")
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    pad = "=" * ((4 - (len(s) % 4)) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii")).decode("utf-8", errors="strict")


def _encode_task_file_id(*, root: str, rel: str) -> str:
    return _b64url_encode(f"{root}:{rel}")


def _decode_task_file_id(file_id: str) -> tuple[str, str]:
    raw = _b64url_decode(file_id)
    if ":" not in raw:
        raise ValueError("invalid file id")
    root, rel = raw.split(":", 1)
    root = root.strip().lower()
    rel = rel.strip().replace("\\", "/")
    return root, rel


def _safe_rel(rel: str) -> str:
    s = str(rel or "").replace("\\", "/").strip().lstrip("/")
    if not s:
        raise ValueError("empty path")
    parts = [p for p in s.split("/") if p and p not in (".",)]
    if any(p == ".." for p in parts):
        raise ValueError("path traversal")
    if any(":" in p for p in parts):
        raise ValueError("invalid path")
    return "/".join(parts)


def _task_outputs_root(*, task: dict[str, Any]) -> Path:
    ws_id = str(task.get("workspace_id") or "").strip()
    if not ws_id:
        return (settings.workspaces_dir / "default" / "outputs" / str(task.get("id") or "")).resolve()
    ws = q_one("SELECT * FROM workspaces WHERE id=?", (ws_id,))
    ws_root = Path(str((ws or {}).get("path") or settings.workspaces_dir)).resolve()
    return (ws_root / "outputs" / str(task.get("id") or "")).resolve()


def _resolve_task_file_path(*, task: dict[str, Any], file_id: str) -> Path:
    root, rel = _decode_task_file_id(file_id)
    rel = _safe_rel(rel)
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        raise ValueError("missing task id")

    if root == "a":
        base = (settings.artifacts_dir / task_id).resolve()
    elif root == "o":
        base = _task_outputs_root(task=task)
    else:
        raise ValueError("unknown root")

    p = (base / rel).resolve()
    try:
        p.relative_to(base)
    except Exception as e:
        raise ValueError("path outside root") from e
    return p


def _guess_kind(path: Path) -> str:
    ext = str(path.suffix or "").lower().lstrip(".")
    if ext:
        return ext
    return "file"


def _pick_default_file_id(files: list[dict[str, Any]]) -> str:
    if not files:
        return ""

    def _prio(f: dict[str, Any]) -> int:
        kind = str(f.get("kind") or "").lower()
        if kind == "pptx":
            return 0
        if kind == "pdf":
            return 1
        if kind in ("html", "htm"):
            return 2
        if kind in ("md", "markdown"):
            return 3
        if kind in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"):
            return 4
        if kind == "docx":
            return 5
        return 50

    sorted_files = sorted(
        files,
        key=lambda f: (
            _prio(f),
            -float(f.get("mtime") or 0.0),
            str(f.get("name") or ""),
        ),
    )
    return str(sorted_files[0].get("id") or "")


def _load_uak_citation_index(*, run_id: str, max_chunks: int = 800) -> Dict[str, Any]:
    """
    Build a lightweight chunk_id -> (snippet/url/title/...) index from UAK tool recordings.
    Used by the UI to render hoverable citations.
    """
    rid = str(run_id or "").strip()
    if not rid:
        return {"chunks": {}, "warnings": []}

    chunks: Dict[str, Any] = {}
    warnings: List[Dict[str, Any]] = []
    db_path = _uak_db_path()
    if not db_path.exists():
        return {"chunks": {}, "warnings": []}

    import sqlite3

    try:
        con = sqlite3.connect(str(db_path))
    except Exception:
        return {"chunks": {}, "warnings": []}

    def _safe_json(s: Any) -> Any:
        try:
            if isinstance(s, str) and s:
                return json.loads(s)
        except Exception:
            return None
        return None

    def _infer_url(meta: Dict[str, Any], text: str) -> str:
        for k in ("url", "uri", "path"):
            v = meta.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        m = re.search(r"\\bhttps?://\\S+", text or "")
        if m:
            return str(m.group(0)).rstrip(").,;:!?]}\"'")
        return ""

    try:
        cur = con.cursor()
        cur.execute(
            "SELECT tool_name, response_json FROM tool_recordings WHERE run_id=? AND status='DONE' ORDER BY id ASC",
            (rid,),
        )
        for tool_name, response_json in cur.fetchall() or []:
            resp = _safe_json(response_json) or {}
            if not isinstance(resp, dict):
                continue
            ev = resp.get("evidence") if isinstance(resp.get("evidence"), dict) else None
            if not isinstance(ev, dict):
                continue
            ev_chunks = ev.get("chunks")
            if not isinstance(ev_chunks, list):
                continue
            for c in ev_chunks:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("chunk_id") or "").strip()
                if not cid or cid in chunks:
                    continue
                meta = c.get("metadata") if isinstance(c.get("metadata"), dict) else {}
                text = str(c.get("text") or "")
                url = _infer_url(meta, text)
                title = str(meta.get("title") or "")
                snippet = (text or "").strip().replace("\r\n", "\n").replace("\r", "\n")
                if len(snippet) > 420:
                    snippet = snippet[:420] + "…"
                chunks[cid] = {
                    "chunk_id": cid,
                    "tool_name": str(tool_name or ""),
                    "url": url,
                    "title": title,
                    "kind": str(meta.get("kind") or ""),
                    "snippet": snippet,
                }
                if len(chunks) >= int(max_chunks):
                    break
            if len(chunks) >= int(max_chunks):
                break
    except Exception:
        chunks = {}

    try:
        cur = con.cursor()
        cur.execute(
            "SELECT id, payload_json FROM events WHERE run_id=? AND type='guardrail.warned' ORDER BY id DESC LIMIT 20",
            (rid,),
        )
        for _id, payload_json in cur.fetchall() or []:
            pj = _safe_json(payload_json) or {}
            if not isinstance(pj, dict):
                continue
            # Only surface citation-related warnings.
            guardrail = str(pj.get("guardrail") or pj.get("name") or pj.get("guardrail_name") or "")
            warning_kind = str(pj.get("warning") or "")
            if guardrail and "citation" not in guardrail and warning_kind != "unverified_citations":
                continue
            warnings.append(pj)
    except Exception:
        warnings = []

    try:
        con.close()
    except Exception:
        pass

    return {"chunks": chunks, "warnings": warnings}

@app.middleware("http")
async def lang_middleware(request: Request, call_next):
    lang = detect_lang(request)
    request.state.lang = lang
    response = await call_next(request)
    qlang = request.query_params.get("lang")
    if qlang:
        response.set_cookie("lang", normalize_lang(qlang), max_age=3600 * 24 * 365, samesite="lax")
    elif not request.cookies.get("lang"):
        # Persist initial language choice for embedded WebViews that may not send a stable Accept-Language.
        response.set_cookie("lang", normalize_lang(lang), max_age=3600 * 24 * 365, samesite="lax")
    return response


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9\-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "workspace"


def _ensure_admin(request: Request) -> None:
    token = request.headers.get("x-admin-token") or request.query_params.get("token") or ""
    if settings.ui_admin_token and token != settings.ui_admin_token:
        raise HTTPException(status_code=401, detail="Missing/invalid admin token. Provide ?token=... or header x-admin-token")


@app.on_event("startup")
def on_startup() -> None:
    # Apply persisted runtime env (e.g., provider settings) before starting services.
    apply_runtime_env()

    # Force UAK web-search exposure policy to "auto" (step-wise, on-demand gating inside UAK).
    # This guarantees consistent behavior across all OWB runs regardless of host environment.
    os.environ["UAK_WEB_SEARCH_POLICY"] = "auto"

    init_db()
    register_all_tools()

    # auto-create a default workspace if none exists
    ws = q_one("SELECT * FROM workspaces LIMIT 1", ())
    if not ws:
        default_path = settings.workspaces_dir / "default"
        default_path.mkdir(parents=True, exist_ok=True)
        create_workspace(name="Default Workspace", path=default_path)

    # auto-import bundled skills from /app/skills (if mounted)
    if os.getenv("SKILLS_DIR"):
        skills_dir = Path(os.getenv("SKILLS_DIR", "/app/skills"))
    else:
        # Local/dev default: repo-root ./skills (works without setting SKILLS_DIR).
        guess = (BASE_DIR.parents[2] / "skills") if len(BASE_DIR.parents) >= 3 else (Path.cwd() / "skills")
        skills_dir = guess if guess.exists() else Path("/app/skills")
    if skills_dir.exists():
        for yml in sorted(skills_dir.glob("*.yaml")):
            _import_skill_from_yaml(str(yml), ignore_if_exists=True)

    # Backfill skill_meta rows for existing skills.
    try:
        now = _now()
        for s in q_all("SELECT id, yaml_path FROM skills", ()):
            exec_sql(
                "INSERT OR IGNORE INTO skill_meta (skill_id, enabled, source, updated_at) VALUES (?,?,?,?)",
                (s["id"], 1, s.get("yaml_path") or "", now),
            )
    except Exception:
        pass

    start_scheduler()


class AutoTaskCreate(BaseModel):
    goal: str
    mode: Optional[str] = "fast"
    workspace_id: Optional[str] = None
    hint: Optional[str] = None


class SettingsUpdate(BaseModel):
    provider: Dict[str, Optional[str]] = {}
    desktop: Dict[str, Optional[str]] = {}


class ClientLogEvent(BaseModel):
    level: str = "error"
    message: str
    url: Optional[str] = ""
    stack: Optional[str] = ""
    user_agent: Optional[str] = ""
    ts_ms: Optional[int] = None
    extra: Optional[Dict[str, Any]] = None


class WorkspacePoliciesUpdate(BaseModel):
    workspace_id: str
    policies: Dict[str, str]


class RecipeUpsert(BaseModel):
    name: str
    description: Optional[str] = ""
    goal_template: str
    form: Optional[Dict[str, Any]] = None
    default_mode: Optional[str] = "fast"
    enabled: bool = True


class SkillEnableUpdate(BaseModel):
    enabled: bool


class SkillInstallUrl(BaseModel):
    url: str


class MCPServerUpsert(BaseModel):
    name: str
    command: str
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    healthcheck_args: Optional[List[str]] = None
    enabled: bool = True


def _import_skill_from_yaml(yaml_path: str, ignore_if_exists: bool = False) -> Dict[str, Any]:
    import yaml

    p = Path(yaml_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Skill YAML not found: {yaml_path}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    name = data.get("name") or p.stem
    description = data.get("description") or ""
    system_prompt = data.get("system_prompt") or ""
    allowed_tools = data.get("allowed_tools") or []
    default_mode = data.get("default_mode") or "fast"

    # de-dup by name
    existing = q_one("SELECT * FROM skills WHERE name=?", (name,))
    if existing:
        if ignore_if_exists:
            return existing
        raise HTTPException(status_code=409, detail=f"Skill already exists: {name}")
    created = create_skill(
        name=name,
        description=description,
        yaml_path=str(p),
        system_prompt=system_prompt,
        allowed_tools=list(allowed_tools),
        default_mode=default_mode,
    )
    return {"ok": True, "id": created.get("id"), "name": name, "yaml_path": str(p)}


# ----------------------- Static & Templates -----------------------

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/api/icon.png")
def api_icon_png(size: int = 128) -> Response:
    """
    Generated product icon (original pixel-art), used for favicon and in-app branding.
    """
    try:
        from .desktop.icon_assets import icon_image

        img = icon_image(size=max(16, min(int(size), 512)))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return Response(
            content=buf.getvalue(),
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except Exception:
        # Fallback: small inline SVG if Pillow/desktop module isn't available.
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='128' height='128' viewBox='0 0 128 128'>"
            "<rect width='128' height='128' rx='22' fill='#0b0e14'/>"
            "<rect x='16' y='16' width='96' height='96' rx='14' fill='#0f172a' stroke='#334155' stroke-width='4'/>"
            "<path d='M64 36 L82 92 H74 L70 80 H58 L54 92 H46 L64 36 Z M60 72 H68 L64 58 Z' fill='#e6e6e6'/>"
            "<path d='M20 28 L36 28' stroke='#0ea5e9' stroke-width='6' stroke-linecap='round'/>"
            "</svg>"
        )
        return Response(content=svg, media_type="image/svg+xml")


def _normalize_goal_text(goal: str) -> str:
    s = str(goal or "").strip()
    if not s:
        return ""
    return re.sub(r"\s+", " ", s).strip()


def _summarize_goal_legacy(goal: str, lang: str) -> tuple[str, str]:
    """
    Deterministic goal -> (title, subtitle) summarization.

    Keep it fast and offline-friendly (no LLM calls).
    """
    g = _normalize_goal_text(goal)
    if not g:
        return ("", "")
    gl = g.lower()
    zh = str(lang or "").strip().lower().startswith("zh")

    def _has(terms: list[str]) -> bool:
        for t in terms:
            if not t:
                continue
            if t.lower() in gl:
                return True
            if t in g:
                return True
        return False

    deliverables: list[str] = []
    if _has(["ppt", "powerpoint", "slides", "幻灯片", "演示稿", "讲解ppt"]):
        deliverables.append("PPT")
    if _has(["视频稿", "视频脚本", "讲解稿", "video script", "script"]):
        deliverables.append("视频稿" if zh else "Video script")
    if _has(["报告", "report", "analysis", "总结", "调研"]):
        deliverables.append("报告" if zh else "Report")
    if _has(["pdf"]):
        deliverables.append("PDF")
    if _has(["docx", ".docx", "word", ".doc"]):
        deliverables.append("DOCX")
    if _has(["markdown", ".md"]):
        deliverables.append("Markdown")
    if _has(["png", "jpg", "jpeg", "图片", "image"]):
        deliverables.append("图片" if zh else "Images")
    deliverables = list(dict.fromkeys(deliverables))

    domain = g
    if zh:
        domain = re.sub(r"^(请帮我|请你|请|帮我|麻烦|帮忙)\\s*", "", domain)
        domain = re.split(r"[。！？\\n]", domain)[0]
        domain = re.split(r"(最终|并且|然后|最后|要求)", domain, maxsplit=1)[0]
    else:
        domain = re.sub(r"^please\\s+", "", domain, flags=re.I)
        domain = re.split(r"[.!?\\n]", domain)[0]
        domain = re.split(r"(in the end|finally|and|then|requirements:|requirement:)", domain, maxsplit=1, flags=re.I)[0]
    domain = domain.strip(" ,，;；:：-—")
    # UI requirement: keep titles extremely short (<=10 Chinese chars).
    max_domain = 10 if zh else 28
    if len(domain) > max_domain:
        domain = domain[:max_domain].rstrip() + "…"

    if deliverables:
        # Keep the title short; show deliverables in the UI elsewhere (not in the title).
        title = domain or deliverables[0]
    else:
        title = domain or (g[:48].rstrip() + ("…" if len(g) > 48 else ""))

    constraints: list[str] = []
    if _has(["引用", "文献", "论文", "cite", "citation", "sources", "出处"]):
        constraints.append("含论文/来源引用" if zh else "With citations")
    if _has(["严谨", "rigorous", "rigor"]):
        constraints.append("尽量严谨" if zh else "Rigorous")
    if _has(["全面", "comprehensive"]):
        constraints.append("尽量全面" if zh else "Comprehensive")
    if _has(["前沿", "sota", "state-of-the-art", "最先进"]):
        constraints.append("偏前沿" if zh else "State-of-the-art")
    constraints = list(dict.fromkeys(constraints))
    subtitle = " · ".join(constraints)
    return (title.strip(), subtitle.strip())


def _summarize_goal(goal: str, lang: str) -> tuple[str, str]:
    """
    Deterministic goal -> (title, subtitle) summarization.

    Keep it fast and offline-friendly (no LLM calls).
    """
    g = _normalize_goal_text(goal)
    if not g:
        return ("", "")
    gl = g.lower()
    zh = str(lang or "").strip().lower().startswith("zh")

    # UI requirement: keep titles extremely short (<=10 Chinese chars).
    max_title = 10 if zh else 28

    def _clip(s: str, n: int) -> str:
        ss = str(s or "")
        if n <= 0:
            return ""
        if len(ss) <= n:
            return ss
        if n <= 1:
            return ss[:n]
        return ss[: max(0, n - 1)].rstrip() + "…"

    def _has(terms: list[str]) -> bool:
        for t in terms:
            if not t:
                continue
            tl = t.lower()
            if tl and tl in gl:
                return True
            if t in g:
                return True
        return False

    deliverables: list[str] = []
    if _has(["ppt", "powerpoint", "slides", "幻灯片", "演示稿", "讲解PPT", "讲解ppt"]):
        deliverables.append("PPT")
    if _has(["视频稿", "视频脚本", "讲解稿", "video script", "script"]):
        deliverables.append("视频稿" if zh else "Video script")
    if _has(["报告", "report", "analysis", "总结", "调研"]):
        deliverables.append("报告" if zh else "Report")
    if _has(["pdf"]):
        deliverables.append("PDF")
    if _has(["docx", ".docx", "word", ".doc"]):
        deliverables.append("DOCX")
    if _has(["markdown", ".md"]):
        deliverables.append("Markdown")
    if _has(["png", "jpg", "jpeg", "图片", "image"]):
        deliverables.append("图片" if zh else "Images")
    deliverables = list(dict.fromkeys(deliverables))

    domain = g
    if zh:
        domain = re.sub(r"^(请你|请|帮我|麻烦|帮忙)\\s*", "", domain)
        domain = re.split(r"[。！？\\n]", domain)[0]
        domain = re.split(r"(最终|最后|然后|并且|同时|要求|需要)", domain, maxsplit=1)[0]
    else:
        domain = re.sub(r"^please\\s+", "", domain, flags=re.I)
        domain = re.split(r"[.!?\\n]", domain)[0]
        domain = re.split(
            r"(in the end|finally|then|requirements?:|requirement:|and)",
            domain,
            maxsplit=1,
            flags=re.I,
        )[0]
    domain = domain.strip(" ,，;；:：-—")

    title = _clip(domain or (deliverables[0] if deliverables else g), max_title)

    constraints: list[str] = []
    if _has(["引用", "文献", "论文", "cite", "citation", "sources", "出处", "来源"]):
        constraints.append("含引用" if zh else "With citations")
    if _has(["严谨", "rigorous", "rigor"]):
        constraints.append("尽量严谨" if zh else "Rigorous")
    if _has(["全面", "comprehensive"]):
        constraints.append("尽量全面" if zh else "Comprehensive")
    if _has(["前沿", "sota", "state-of-the-art", "最先进"]):
        constraints.append("偏前沿" if zh else "State-of-the-art")
    constraints = list(dict.fromkeys(constraints))
    subtitle = " · ".join(constraints)
    return (title.strip(), subtitle.strip())


def render(template: str, **ctx: Any) -> HTMLResponse:
    request: Optional[Request] = ctx.get("request")
    if request is not None:
        lang = getattr(request.state, "lang", "en")
        ctx.setdefault("lang", lang)
        ctx.setdefault("supported_langs", SUPPORTED_LANGS)
        ctx.setdefault("_", lambda key, **kwargs: tr(lang, key, **kwargs))
        ctx.setdefault("lang_url", lambda target_lang: with_lang(request, target_lang))
        # Sidebar context
        q = request.query_params.get("q") or ""
        ctx.setdefault("q", q)
        if "sidebar_tasks" not in ctx:
            sidebar_tasks = q_all("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT 50", ())
            if q:
                q_lower = q.lower()
                sidebar_tasks = [t for t in sidebar_tasks if q_lower in (t.get("goal") or "").lower()]
            for t in sidebar_tasks:
                try:
                    title, subtitle = _summarize_goal(t.get("goal") or "", lang)
                    t["title"] = title
                    t["subtitle"] = subtitle
                except Exception:
                    continue
            ctx["sidebar_tasks"] = sidebar_tasks
        # Also annotate the current task (if provided by route).
        try:
            task = ctx.get("task")
            if isinstance(task, dict) and task.get("goal"):
                title, subtitle = _summarize_goal(task.get("goal") or "", lang)
                task["title"] = title
                task["subtitle"] = subtitle
        except Exception:
            pass
    ctx.setdefault("build_id", APP_BUILD_ID)
    ctx.setdefault("build_time", APP_BUILD_TIME)
    ctx.setdefault("static_version", STATIC_VERSION)
    t = jinja.get_template(template)
    return HTMLResponse(t.render(**ctx))


@app.get("/", response_class=HTMLResponse)
def ui_index(request: Request) -> HTMLResponse:
    q = request.query_params.get("q") or ""
    tasks = q_all("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT 20", ())
    if q:
        q_lower = q.lower()
        tasks = [t for t in tasks if q_lower in (t.get("goal") or "").lower()]
    for t in tasks:
        t["plan"] = from_json(t.get("plan_json"))
    workspaces = q_all("SELECT * FROM workspaces ORDER BY created_at ASC", ())
    default_workspace_id = request.cookies.get("default_workspace_id") or (workspaces[0]["id"] if workspaces else "")
    default_workspace_name = ""
    for w in workspaces:
        if w.get("id") == default_workspace_id:
            default_workspace_name = w.get("name") or ""
            break

    lang = getattr(request.state, "lang", "en")

    # Quick action chips (do not force a skill; they only help users phrase goals)
    if lang == "zh":
        quick_skills = [
            {"name": "file", "emoji": "🗂", "label": "文件整理"},
            {"name": "research", "emoji": "🔎", "label": "深度调研"},
            {"name": "batch", "emoji": "🧩", "label": "批量处理"},
            {"name": "life", "emoji": "🗓", "label": "计划/复盘"},
        ]
        suggestion_presets = {
            "file": "帮我整理这个文件夹：\n- 按类型/日期归档\n- 生成清单\n",
            "research": "围绕这个主题做一次深度调研，并输出报告：\n",
            "batch": "批量处理这些内容（请先说明规则）：\n",
            "life": "帮我制定一个可执行的计划：\n",
        }
        hour = time.localtime().tm_hour
        if hour < 11:
            greeting = "早上好，和我一起工作吧！"
        elif hour < 14:
            greeting = "中午好，和我一起工作吧！"
        elif hour < 18:
            greeting = "下午好，和我一起工作吧！"
        else:
            greeting = "晚上好，和我一起工作吧！"
    else:
        quick_skills = [
            {"name": "file", "emoji": "🗂", "label": "File organize"},
            {"name": "research", "emoji": "🔎", "label": "Deep research"},
            {"name": "batch", "emoji": "🧩", "label": "Batch process"},
            {"name": "life", "emoji": "🗓", "label": "Planning"},
        ]
        suggestion_presets = {
            "file": "Help me organize this folder:\n- Archive by type/date\n- Generate an index\n",
            "research": "Do deep research on this topic and output a report:\n",
            "batch": "Batch-process these items (specify rules first):\n",
            "life": "Help me create an actionable plan:\n",
        }
        hour = time.localtime().tm_hour
        if hour < 11:
            greeting = "Good morning — let's work together!"
        elif hour < 18:
            greeting = "Good afternoon — let's work together!"
        else:
            greeting = "Good evening — let's work together!"

    recipes = q_all("SELECT * FROM recipes WHERE enabled=1 ORDER BY updated_at DESC LIMIT 8", ())
    for r in recipes:
        r["form"] = from_json(r.get("form_json")) or {}

    return render(
        "index.html",
        request=request,
        tasks=tasks,
        default_workspace_id=default_workspace_id,
        default_workspace_name=default_workspace_name,
        quick_skills=quick_skills,
        suggestion_presets=suggestion_presets,
        greeting=greeting,
        recipes=recipes,
        admin_token=settings.ui_admin_token,
    )


@app.get("/workspaces", response_class=HTMLResponse)
def ui_workspaces(request: Request) -> HTMLResponse:
    workspaces = q_all("SELECT * FROM workspaces ORDER BY created_at DESC", ())
    return render("workspaces.html", request=request, workspaces=workspaces, admin_token=settings.ui_admin_token)


@app.get("/skills", response_class=HTMLResponse)
def ui_skills(request: Request) -> HTMLResponse:
    skills = q_all(
        "SELECT s.*, COALESCE(m.enabled, 1) AS enabled, COALESCE(m.source, '') AS source "
        "FROM skills s LEFT JOIN skill_meta m ON m.skill_id=s.id ORDER BY s.created_at DESC",
        (),
    )
    for s in skills:
        s["allowed_tools"] = from_json(s.get("allowed_tools_json")) or []
    tools = list_tools()
    return render("skills.html", request=request, skills=skills, tools=tools, admin_token=settings.ui_admin_token)


@app.get("/schedules", response_class=HTMLResponse)
def ui_schedules(request: Request) -> HTMLResponse:
    schedules = q_all("SELECT * FROM schedules ORDER BY created_at DESC", ())
    skills = q_all("SELECT * FROM skills ORDER BY name ASC", ())
    workspaces = q_all("SELECT * FROM workspaces ORDER BY name ASC", ())
    return render("schedules.html", request=request, schedules=schedules, skills=skills, workspaces=workspaces, admin_token=settings.ui_admin_token)


@app.get("/tasks/{task_id}", response_class=HTMLResponse)
def ui_task_detail(request: Request, task_id: str) -> HTMLResponse:
    task = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    task["plan"] = from_json(task.get("plan_json"))
    steps = q_all("SELECT * FROM steps WHERE task_id=? ORDER BY idx ASC", (task_id,))
    for s in steps:
        s["args"] = from_json(s.get("args_json")) or {}
        s["result"] = from_json(s.get("result_json"))
    approvals = q_all("SELECT * FROM approvals WHERE task_id=? ORDER BY requested_at DESC", (task_id,))

    report_preview = ""
    report_path = str(task.get("output_path") or "").strip()
    if report_path:
        try:
            p = Path(report_path)
            if p.exists() and p.is_file():
                b = p.read_bytes()
                if len(b) > 240_000:
                    b = b[:240_000] + b"\n\n--- TRUNCATED ---\n"
                report_preview = b.decode("utf-8", errors="ignore")
        except Exception:
            report_preview = ""

    artifacts: List[Dict[str, Any]] = []
    try:
        base = settings.artifacts_dir / task_id
        if base.exists():
            for p in base.rglob("*"):
                if p.is_file():
                    artifacts.append({"path": str(p), "size": p.stat().st_size})
        artifacts.sort(key=lambda a: a.get("path") or "")
    except Exception:
        artifacts = []
    return render(
        "task_detail.html",
        request=request,
        task=task,
        steps=steps,
        approvals=approvals,
        report_preview=report_preview,
        artifacts=artifacts,
        current_task_id=task_id,
        admin_token=settings.ui_admin_token,
    )


@app.get("/settings", response_class=HTMLResponse)
def ui_settings(request: Request) -> HTMLResponse:
    workspaces = q_all("SELECT * FROM workspaces ORDER BY created_at ASC", ())
    skills = q_all(
        "SELECT s.*, COALESCE(m.enabled, 1) AS enabled, COALESCE(m.source, '') AS source "
        "FROM skills s LEFT JOIN skill_meta m ON m.skill_id=s.id ORDER BY s.created_at DESC",
        (),
    )
    for s in skills:
        s["enabled"] = bool(s.get("enabled"))
        s["allowed_tools"] = from_json(s.get("allowed_tools_json")) or []
        src = str(s.get("source") or "").strip()
        s["source_display"] = src
        if src.startswith("{") and src.endswith("}"):
            try:
                j = json.loads(src)
                if isinstance(j, dict) and str(j.get("type") or "").strip().lower() == "url":
                    u = str(j.get("url") or "").strip()
                    if u:
                        s["source_display"] = u
            except Exception:
                s["source_display"] = src
    default_workspace_id = request.cookies.get("default_workspace_id") or (workspaces[0]["id"] if workspaces else "")
    provider = {
        "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", settings.llm_base_url),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", settings.llm_api_key),
        "OPENAI_MODEL_FAST": os.getenv("OPENAI_MODEL_FAST", settings.model_fast),
        "OPENAI_MODEL_PRO": os.getenv("OPENAI_MODEL_PRO", settings.model_pro),
        "OPENAI_MODEL_EMBEDDINGS": os.getenv("OPENAI_MODEL_EMBEDDINGS", settings.model_embeddings),
    }
    desktop = {
        "OWB_HOST_MODE": os.getenv("OWB_HOST_MODE", "local"),
        "OWB_REMOTE_URL": os.getenv("OWB_REMOTE_URL", ""),
        "OWB_REMOTE_TOKEN": os.getenv("OWB_REMOTE_TOKEN", ""),
    }
    uak = {
        "UAK_CITATIONS_MODE": (os.getenv("UAK_CITATIONS_MODE") or "auto").strip().lower() or "auto",
    }
    policies = get_workspace_policies(default_workspace_id) if default_workspace_id else {}
    recipe_count = q_one("SELECT COUNT(*) AS c FROM recipes", ()) or {"c": 0}
    mcp_servers = q_all("SELECT * FROM mcp_servers ORDER BY updated_at DESC", ())
    for r in mcp_servers:
        r["args"] = from_json(r.get("args_json")) or []
        r["env"] = from_json(r.get("env_json")) or {}
        r["healthcheck_args"] = from_json(r.get("healthcheck_args_json")) or []
        r["enabled"] = bool(r.get("enabled"))
    return render(
        "settings.html",
        request=request,
        workspaces=workspaces,
        skills=skills,
        provider=provider,
        desktop=desktop,
        uak=uak,
        policies=policies,
        recipe_count=int(recipe_count.get("c") or 0),
        mcp_servers=mcp_servers,
        data_dir=str(settings.data_dir),
        logs_dir=str(settings.logs_dir),
        default_workspace_id=default_workspace_id,
        admin_token=settings.ui_admin_token,
    )


@app.get("/recipes", response_class=HTMLResponse)
def ui_recipes(request: Request) -> HTMLResponse:
    recipes = q_all("SELECT * FROM recipes ORDER BY updated_at DESC", ())
    for r in recipes:
        r["form"] = from_json(r.get("form_json")) or {}
    return render("recipes.html", request=request, recipes=recipes, admin_token=settings.ui_admin_token)


# ----------------------- API -----------------------

@app.get("/api/health")
def api_health() -> Dict[str, Any]:
    return {"ok": True, "app": settings.app_name, "time": _now()}


@app.get("/api/events")
async def api_events() -> StreamingResponse:
    """
    Server-Sent Events stream. Emits task_update and step_update events.
    """
    q = subscribe()

    async def event_gen():
        try:
            # initial comment for some proxies
            yield ": connected\n\n"
            loop = asyncio.get_running_loop()
            while True:
                ev = await loop.run_in_executor(None, q.get)
                yield format_sse(ev)
        finally:
            unsubscribe(q)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/api/workspaces")
def api_list_workspaces() -> List[Dict[str, Any]]:
    return q_all("SELECT * FROM workspaces ORDER BY created_at DESC", ())


@app.post("/api/workspaces")
def api_create_workspace(payload: WorkspaceCreate, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    name = payload.name.strip()
    if payload.path:
        path = Path(payload.path).expanduser().resolve()
    else:
        slug = _slug(name)
        path = settings.workspaces_dir / slug
    path.mkdir(parents=True, exist_ok=True)
    ws = create_workspace(name=name, path=path)
    return {"ok": True, "workspace": ws}


@app.get("/api/skills")
def api_list_skills() -> List[Dict[str, Any]]:
    rows = q_all(
        "SELECT s.*, COALESCE(m.enabled, 1) AS enabled, COALESCE(m.source, '') AS source "
        "FROM skills s LEFT JOIN skill_meta m ON m.skill_id=s.id ORDER BY s.created_at DESC",
        (),
    )
    for r in rows:
        r["allowed_tools"] = from_json(r.get("allowed_tools_json")) or []
    return rows


@app.post("/api/skills/import")
def api_import_skill(payload: SkillImport, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    return _import_skill_from_yaml(payload.yaml_path)


@app.post("/api/skills")
def api_create_skill(payload: SkillCreate, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    create_skill(
        name=payload.name,
        description=payload.description or "",
        yaml_path=None,
        system_prompt=payload.system_prompt,
        allowed_tools=list(payload.allowed_tools),
        default_mode=payload.default_mode,
    )
    return {"ok": True}


@app.post("/api/skills/{skill_id}/enabled")
def api_set_skill_enabled(skill_id: str, payload: SkillEnableUpdate, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    now = _now()
    row = q_one("SELECT id, yaml_path FROM skills WHERE id=?", (skill_id,))
    if not row:
        raise HTTPException(status_code=404, detail="skill not found")
    exec_sql(
        "INSERT INTO skill_meta (skill_id, enabled, source, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(skill_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at",
        (skill_id, 1 if payload.enabled else 0, row.get("yaml_path") or "", now),
    )
    return {"ok": True, "skill_id": skill_id, "enabled": bool(payload.enabled)}


@app.post("/api/skills/{skill_id}/reload")
def api_reload_skill(skill_id: str, request: Request) -> Dict[str, Any]:
    """
    Reload a skill's definition from its source (YAML path or installed URL) and update the DB record in-place.
    """
    _ensure_admin(request)
    row = q_one(
        "SELECT s.*, COALESCE(m.enabled, 1) AS enabled, COALESCE(m.source, '') AS source "
        "FROM skills s LEFT JOIN skill_meta m ON m.skill_id=s.id WHERE s.id=?",
        (skill_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="skill not found")

    yaml_path = str(row.get("yaml_path") or "").strip()
    source = str(row.get("source") or "").strip()
    enabled = bool(row.get("enabled"))

    url: Optional[str] = None
    path_override: Optional[str] = None
    if source.startswith("{") and source.endswith("}"):
        try:
            src_obj = json.loads(source)
            if isinstance(src_obj, dict) and str(src_obj.get("type") or "").strip().lower() == "url":
                url = str(src_obj.get("url") or "").strip() or None
                path_override = str(src_obj.get("path") or "").strip() or None
        except Exception:
            url = None
            path_override = None

    if url:
        import requests

        try:
            r = requests.get(url, timeout=25)
            r.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"download failed: {e}")

        text = r.text
        if len(text) > 2_000_000:
            raise HTTPException(status_code=400, detail="file too large")

        downloads = settings.data_dir / "skills_downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        filename = _slug(url.split("/")[-1] or "skill") + ".yaml"
        dst = Path(path_override or yaml_path or str(downloads / filename))
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        yaml_path = str(dst)

        # Keep source JSON updated with the resolved path.
        source = to_json({"type": "url", "url": url, "path": yaml_path})

    if not yaml_path:
        raise HTTPException(status_code=400, detail="skill has no yaml_path/source to reload")

    p = Path(yaml_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Skill YAML not found: {yaml_path}")

    import yaml

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="invalid YAML format (expected mapping)")

    name = data.get("name") or p.stem
    description = data.get("description") or ""
    system_prompt = data.get("system_prompt") or ""
    allowed_tools = data.get("allowed_tools") or []
    default_mode = data.get("default_mode") or "fast"

    exec_sql(
        "UPDATE skills SET name=?, description=?, yaml_path=?, system_prompt=?, allowed_tools_json=?, default_mode=? WHERE id=?",
        (
            str(name),
            str(description),
            str(p),
            str(system_prompt),
            to_json(list(allowed_tools) if isinstance(allowed_tools, list) else []),
            str(default_mode or "fast"),
            skill_id,
        ),
    )
    now = _now()
    exec_sql(
        "INSERT INTO skill_meta (skill_id, enabled, source, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(skill_id) DO UPDATE SET source=excluded.source, updated_at=excluded.updated_at",
        (skill_id, 1 if enabled else 0, source or str(p), now),
    )
    return {"ok": True, "id": skill_id, "name": str(name), "yaml_path": str(p)}


@app.post("/api/skills/install_url")
def api_install_skill_from_url(payload: SkillInstallUrl, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    url = (payload.url or "").strip()
    if not (url.startswith("https://") or url.startswith("http://")):
        raise HTTPException(status_code=400, detail="url must be http(s)")
    import requests

    try:
        r = requests.get(url, timeout=25)
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"download failed: {e}")

    text = r.text
    if len(text) > 2_000_000:
        raise HTTPException(status_code=400, detail="file too large")

    # Save under DATA_DIR/skills_downloads for provenance.
    downloads = settings.data_dir / "skills_downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    filename = _slug(url.split("/")[-1] or "skill") + ".yaml"
    path = downloads / filename
    path.write_text(text, encoding="utf-8")

    res = _import_skill_from_yaml(str(path), ignore_if_exists=False)
    try:
        skill_id = str(res.get("id") or "").strip()
        if skill_id:
            now = _now()
            src = to_json({"type": "url", "url": url, "path": str(path)})
            exec_sql(
                "INSERT INTO skill_meta (skill_id, enabled, source, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(skill_id) DO UPDATE SET source=excluded.source, updated_at=excluded.updated_at",
                (skill_id, 1, src, now),
            )
    except Exception:
        pass
    return {"ok": True, "saved_to": str(path), **res}


@app.get("/api/tasks")
def api_list_tasks() -> List[Dict[str, Any]]:
    rows = q_all("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT 200", ())
    for r in rows:
        r["plan"] = from_json(r.get("plan_json"))
    return rows


@app.get("/api/recipes")
def api_list_recipes(enabled_only: bool = False) -> List[Dict[str, Any]]:
    if enabled_only:
        rows = q_all("SELECT * FROM recipes WHERE enabled=1 ORDER BY updated_at DESC", ())
    else:
        rows = q_all("SELECT * FROM recipes ORDER BY updated_at DESC", ())
    for r in rows:
        r["form"] = from_json(r.get("form_json")) or {}
    return rows


@app.post("/api/recipes")
def api_create_recipe(payload: RecipeUpsert, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    rid = uuid.uuid4().hex
    now = _now()
    exec_sql(
        "INSERT INTO recipes (id, name, description, goal_template, form_json, default_mode, enabled, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            rid,
            payload.name.strip(),
            payload.description or "",
            payload.goal_template,
            to_json(payload.form or {}) if payload.form is not None else to_json({}),
            payload.default_mode or "fast",
            1 if payload.enabled else 0,
            now,
            now,
        ),
    )
    return {"ok": True, "id": rid}


@app.post("/api/recipes/{recipe_id}")
def api_update_recipe(recipe_id: str, payload: RecipeUpsert, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    now = _now()
    row = q_one("SELECT id FROM recipes WHERE id=?", (recipe_id,))
    if not row:
        raise HTTPException(status_code=404, detail="recipe not found")
    exec_sql(
        "UPDATE recipes SET name=?, description=?, goal_template=?, form_json=?, default_mode=?, enabled=?, updated_at=? WHERE id=?",
        (
            payload.name.strip(),
            payload.description or "",
            payload.goal_template,
            to_json(payload.form or {}) if payload.form is not None else to_json({}),
            payload.default_mode or "fast",
            1 if payload.enabled else 0,
            now,
            recipe_id,
        ),
    )
    return {"ok": True, "id": recipe_id}


@app.delete("/api/recipes/{recipe_id}")
def api_delete_recipe(recipe_id: str, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    exec_sql("DELETE FROM recipes WHERE id=?", (recipe_id,))
    return {"ok": True}


@app.get("/api/mcp_servers")
def api_list_mcp_servers() -> List[Dict[str, Any]]:
    rows = q_all("SELECT * FROM mcp_servers ORDER BY updated_at DESC", ())
    for r in rows:
        r["args"] = from_json(r.get("args_json")) or []
        r["env"] = from_json(r.get("env_json")) or {}
        r["healthcheck_args"] = from_json(r.get("healthcheck_args_json")) or []
        r["enabled"] = bool(r.get("enabled"))
    return rows


@app.post("/api/mcp_servers")
def api_create_mcp_server(payload: MCPServerUpsert, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    mid = uuid.uuid4().hex
    now = _now()
    exec_sql(
        "INSERT INTO mcp_servers (id, name, command, args_json, env_json, healthcheck_args_json, enabled, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            mid,
            payload.name.strip(),
            payload.command.strip(),
            to_json(payload.args or []),
            to_json(payload.env or {}),
            to_json(payload.healthcheck_args or []),
            1 if payload.enabled else 0,
            now,
            now,
        ),
    )
    return {"ok": True, "id": mid}


@app.post("/api/mcp_servers/{mcp_id}")
def api_update_mcp_server(mcp_id: str, payload: MCPServerUpsert, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    now = _now()
    row = q_one("SELECT id FROM mcp_servers WHERE id=?", (mcp_id,))
    if not row:
        raise HTTPException(status_code=404, detail="mcp server not found")
    exec_sql(
        "UPDATE mcp_servers SET name=?, command=?, args_json=?, env_json=?, healthcheck_args_json=?, enabled=?, updated_at=? WHERE id=?",
        (
            payload.name.strip(),
            payload.command.strip(),
            to_json(payload.args or []),
            to_json(payload.env or {}),
            to_json(payload.healthcheck_args or []),
            1 if payload.enabled else 0,
            now,
            mcp_id,
        ),
    )
    return {"ok": True, "id": mcp_id}


@app.delete("/api/mcp_servers/{mcp_id}")
def api_delete_mcp_server(mcp_id: str, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    exec_sql("DELETE FROM mcp_servers WHERE id=?", (mcp_id,))
    return {"ok": True}


@app.post("/api/mcp_servers/{mcp_id}/health")
def api_health_mcp_server(mcp_id: str, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    row = q_one("SELECT * FROM mcp_servers WHERE id=?", (mcp_id,))
    if not row:
        raise HTTPException(status_code=404, detail="mcp server not found")
    cmd = str(row.get("command") or "").strip()
    args = from_json(row.get("healthcheck_args_json")) or []
    if not args:
        args = ["--version"]
    env = os.environ.copy()
    env.update(from_json(row.get("env_json")) or {})
    import subprocess

    try:
        p = subprocess.run([cmd, *map(str, args)], capture_output=True, text=True, env=env, timeout=5, check=False)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        return {
            "ok": True,
            "exit_code": p.returncode,
            "stdout": out[:4000],
            "stderr": err[:4000],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/tasks")
def api_create_task(payload: TaskCreate, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    # load skill for default mode
    skill = q_one("SELECT * FROM skills WHERE id=?", (payload.skill_id,))
    if not skill:
        raise HTTPException(status_code=404, detail="skill not found")
    default_mode = skill.get("default_mode") or "fast"
    mode = payload.mode or default_mode
    task_id = create_task(workspace_id=payload.workspace_id, skill_id=payload.skill_id, goal=payload.goal, mode=mode)
    start_task_background(task_id)
    return {"ok": True, "task_id": task_id}


@app.post("/api/tasks/{task_id}/start")
def api_start_task(task_id: str, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    start_task_background(task_id)
    return {"ok": True}


@app.post("/api/tasks/{task_id}/cancel")
def api_cancel_task(task_id: str, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    ok = cancel_task(task_id)
    return {"ok": bool(ok)}


@app.get("/api/tasks/{task_id}/events")
def api_task_events(task_id: str, after: int = 0, limit: int = 200, tail: bool = False) -> List[Dict[str, Any]]:
    """
    Event timeline for a task.

    - `after`: a cursor (SQLite rowid) returned as `seq` in previous responses.
    - returns events in chronological order (ascending seq).
    """
    n = max(1, min(int(limit), 2000))
    cursor = 0
    try:
        cursor = int(after or 0)
    except Exception:
        cursor = 0
    if cursor < 0:
        cursor = 0

    if tail:
        # Latest N events, still returned in chronological order.
        rows = q_all(
            "SELECT rowid AS seq, * FROM event_log WHERE task_id=? ORDER BY rowid DESC LIMIT ?",
            (task_id, n),
        )
        rows.reverse()
    else:
        rows = q_all(
            "SELECT rowid AS seq, * FROM event_log WHERE task_id=? AND rowid>? ORDER BY rowid ASC LIMIT ?",
            (task_id, cursor, n),
        )
    for r in rows:
        r["payload"] = from_json(r.get("payload_json")) or {}
    return rows


@app.get("/api/tasks/{task_id}/sidebar")
def api_task_sidebar(task_id: str, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    task = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    report_preview = ""
    report_path = str(task.get("output_path") or "").strip()
    if report_path:
        try:
            p = Path(report_path)
            if p.exists() and p.is_file():
                b = p.read_bytes()
                if len(b) > 240_000:
                    b = b[:240_000] + b"\n\n--- TRUNCATED ---\n"
                report_preview = b.decode("utf-8", errors="ignore")
        except Exception:
            report_preview = ""

    artifacts: List[Dict[str, Any]] = []
    try:
        base = settings.artifacts_dir / task_id
        if base.exists():
            for p in base.rglob("*"):
                if p.is_file():
                    artifacts.append({"path": str(p), "size": p.stat().st_size})
        artifacts.sort(key=lambda a: a.get("path") or "")
    except Exception:
        artifacts = []

    return {"ok": True, "output_path": report_path, "report_preview": report_preview, "artifacts": artifacts}


@app.get("/api/tasks/{task_id}/citations")
def api_task_citations(task_id: str, request: Request, max_chunks: int = 800) -> Dict[str, Any]:
    _ensure_admin(request)
    task = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    backend = str(task.get("backend") or "").strip().lower()
    if backend != "uak":
        raise HTTPException(status_code=400, detail="citations are supported only for UAK backend tasks")
    run_id = str(task.get("backend_run_id") or "").strip()
    out = _load_uak_citation_index(run_id=run_id, max_chunks=max_chunks)
    return {"ok": True, "run_id": run_id, "chunks": out.get("chunks") or {}, "warnings": out.get("warnings") or []}


@app.get("/api/tasks/{task_id}/files")
def api_task_files(task_id: str, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    task = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    out: list[dict[str, Any]] = []

    def _add_from_root(root: Path, *, root_kind: str) -> None:
        if not root.exists():
            return
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.name.endswith(".owb.json"):
                continue
            try:
                rel = p.relative_to(root).as_posix()
            except Exception:
                continue
            try:
                st = p.stat()
            except Exception:
                continue
            out.append(
                {
                    "id": _encode_task_file_id(root=root_kind, rel=rel),
                    "name": p.name,
                    "rel": rel,
                    "kind": _guess_kind(p),
                    "size": int(st.st_size),
                    "mtime": float(st.st_mtime),
                    "group": "artifacts" if root_kind == "a" else "outputs",
                }
            )

    try:
        _add_from_root((settings.artifacts_dir / task_id).resolve(), root_kind="a")
    except Exception:
        pass
    try:
        _add_from_root(_task_outputs_root(task=task), root_kind="o")
    except Exception:
        pass

    out.sort(key=lambda f: (str(f.get("group") or ""), str(f.get("rel") or "")))
    default_id = _pick_default_file_id(out)
    return {"ok": True, "files": out, "default_id": default_id}


@app.get("/api/tasks/{task_id}/files/raw/{file_id}")
def api_task_file_raw(task_id: str, file_id: str, request: Request, download: int = 0) -> Response:
    _ensure_admin(request)
    task = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        path = _resolve_task_file_path(task=task, file_id=file_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    disp = "attachment" if int(download or 0) == 1 else "inline"
    headers = {"Content-Disposition": f'{disp}; filename="{path.name}"'}
    return FileResponse(path=path, media_type=media_type, headers=headers)


class _FileOpenRequest(BaseModel):
    file_id: str
    reveal: bool = False


@app.post("/api/tasks/{task_id}/files/open")
def api_task_file_open(task_id: str, payload: _FileOpenRequest, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    task = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        path = _resolve_task_file_path(task=task, file_id=payload.file_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")

    try:
        if payload.reveal:
            # Best-effort: reveal in Explorer on Windows.
            if os.name == "nt":
                import subprocess

                subprocess.Popen(["explorer.exe", "/select,", str(path)], shell=False)
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/tasks/{task_id}/ppt/state")
def api_task_ppt_state(task_id: str, request: Request, file_id: str) -> Dict[str, Any]:
    _ensure_admin(request)
    task = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        path = _resolve_task_file_path(task=task, file_id=file_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if str(path.suffix or "").lower() != ".pptx":
        raise HTTPException(status_code=400, detail="not a pptx file")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    sidecar = path.with_suffix(".owb.json")
    if sidecar.exists() and sidecar.is_file():
        try:
            deck = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(deck, dict):
                return {"ok": True, "editable": True, "source": "sidecar", "deck": deck}
        except Exception:
            pass

    # Fallback: attempt to load last ppt.render args from UAK recordings for the run_id derived from artifacts path.
    run_id = ""
    try:
        base = (settings.artifacts_dir / task_id).resolve()
        rel = path.resolve().relative_to(base).parts
        if len(rel) >= 2:
            run_id = str(rel[0])
    except Exception:
        run_id = ""
    if not run_id:
        run_id = str(task.get("backend_run_id") or "").strip()
    if not run_id:
        return {"ok": True, "editable": False, "source": "none", "deck": {}}

    db_path = _uak_db_path()
    if not db_path.exists() or db_path.stat().st_size <= 0:
        return {"ok": True, "editable": False, "source": "none", "deck": {}}

    import sqlite3

    def _safe_json(s: Any) -> Any:
        try:
            if isinstance(s, str) and s:
                return json.loads(s)
        except Exception:
            return None
        return None

    try:
        con = sqlite3.connect(str(db_path))
    except Exception:
        return {"ok": True, "editable": False, "source": "none", "deck": {}}

    try:
        cur = con.cursor()
        cur.execute(
            "SELECT args_json FROM tool_recordings WHERE run_id=? AND tool_name='ppt.render' AND status='DONE' ORDER BY id DESC LIMIT 1",
            (run_id,),
        )
        row = cur.fetchone()
        args_json = row[0] if row else None
        deck = _safe_json(args_json)
        if isinstance(deck, dict) and deck.get("slides"):
            return {"ok": True, "editable": True, "source": "uak", "deck": deck}
    except Exception:
        pass
    finally:
        try:
            con.close()
        except Exception:
            pass

    return {"ok": True, "editable": False, "source": "none", "deck": {}}


class _PptSaveRequest(BaseModel):
    file_id: str
    deck: Dict[str, Any]


@app.post("/api/tasks/{task_id}/ppt/save")
def api_task_ppt_save(task_id: str, payload: _PptSaveRequest, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    task = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        path = _resolve_task_file_path(task=task, file_id=payload.file_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if str(path.suffix or "").lower() != ".pptx":
        raise HTTPException(status_code=400, detail="not a pptx file")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    deck = payload.deck or {}
    if not isinstance(deck, dict):
        raise HTTPException(status_code=400, detail="invalid deck")

    # Ensure the tool writes to the selected file path, regardless of client payload.
    args = dict(deck)
    args["output_path"] = str(path)

    # Try to keep the run_id stable for media placement when the file is under artifacts/<task_id>/<run_id>/...
    run_id = ""
    try:
        base = (settings.artifacts_dir / task_id).resolve()
        rel = path.resolve().relative_to(base).parts
        if len(rel) >= 2:
            run_id = str(rel[0])
    except Exception:
        run_id = ""
    if not run_id:
        run_id = str(task.get("backend_run_id") or "").strip() or uuid.uuid4().hex

    try:
        from uak.tools.ppt_native import _ppt_render  # type: ignore
        from uak.tools.spec import ToolCallContext  # type: ignore

        ctx = ToolCallContext(run_id=run_id, thread_id="owb", step_id="ppt_edit", tool_call_id=uuid.uuid4().hex, mode="record")
        res = _ppt_render(ctx, args)
        out = res.output if hasattr(res, "output") else {}
        ok = bool(isinstance(out, dict) and out.get("ok") is True)
        if not ok:
            raise RuntimeError(str(out.get("error") or "ppt.render failed"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Persist editable source alongside the deck.
    try:
        sidecar = path.with_suffix(".owb.json")
        sidecar.write_text(json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    try:
        st = path.stat()
        return {"ok": True, "path": str(path), "size": int(st.st_size), "mtime": float(st.st_mtime)}
    except Exception:
        return {"ok": True, "path": str(path)}


@app.post("/api/tasks/{task_id}/continue")
def api_task_continue(task_id: str, payload: TaskContinue, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    msg = (payload.message or "").strip()
    if not msg:
        raise HTTPException(status_code=400, detail="message required")
    task = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    status = str(task.get("status") or "").strip().lower()
    if status == "waiting_approval":
        def _parse_approval_decision(s: str) -> Optional[str]:
            raw = (s or "").strip()
            if not raw:
                return None
            # Reject first: "不同意" contains "同意".
            if any(k in raw for k in ("拒绝", "不同意", "不允许")):
                return "reject"
            low = raw.lower()
            if low in ("no", "n", "reject", "deny", "refuse"):
                return "reject"
            if any(k in raw for k in ("同意", "允许")):
                return "approve"
            if low in ("yes", "y", "ok", "approve", "allow"):
                return "approve"
            return None

        decision = _parse_approval_decision(msg)
        if not decision:
            raise HTTPException(
                status_code=409,
                detail="task is waiting approval; reply approve/reject (同意/拒绝) or use the approval UI",
            )

        row = q_one(
            "SELECT step_id FROM approvals WHERE task_id=? AND status='pending' ORDER BY requested_at DESC LIMIT 1",
            (task_id,),
        )
        step_id = str((row or {}).get("step_id") or "").strip()
        if not step_id:
            raise HTTPException(status_code=409, detail="task is waiting approval but no pending approval found")
        approve_step(task_id, step_id, decision, msg)
        return {"ok": True, "approved": True, "decision": decision, "step_id": step_id}

    if status in ("queued", "planning", "running"):
        raise HTTPException(status_code=409, detail=f"task is busy (status={status})")

    backend = str(task.get("backend") or "").strip().lower()
    if backend != "uak":
        raise HTTPException(status_code=400, detail="continue is supported only for UAK backend tasks")

    try:
        continue_task_background(task_id=task_id, message=msg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


@app.delete("/api/tasks/{task_id}")
def api_delete_task(task_id: str, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    task = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        return {"ok": True}

    # Best-effort cleanup of generated outputs/artifacts. (Does not delete user workspace files.)
    try:
        ws = q_one("SELECT * FROM workspaces WHERE id=?", (task.get("workspace_id"),))
        if ws:
            out_dir = (Path(str(ws.get("path") or "")).resolve() / "outputs" / task_id).resolve()
            if out_dir.exists() and out_dir.is_dir():
                shutil.rmtree(out_dir, ignore_errors=True)
    except Exception:
        pass
    try:
        art_dir = (settings.artifacts_dir / task_id).resolve()
        if art_dir.exists() and art_dir.is_dir():
            shutil.rmtree(art_dir, ignore_errors=True)
    except Exception:
        pass

    # SQLite may be briefly write-locked while a different task is actively streaming events.
    import sqlite3

    last_err: Optional[BaseException] = None
    for i in range(6):
        try:
            exec_sql("DELETE FROM tasks WHERE id=?", (task_id,))
            last_err = None
            break
        except sqlite3.OperationalError as e:
            last_err = e
            msg = str(e).lower()
            if "locked" in msg or "busy" in msg:
                time.sleep(0.15 * (i + 1))
                continue
            raise
    if last_err is not None:
        raise HTTPException(status_code=503, detail=f"db is busy, please retry: {last_err}")
    return {"ok": True}


def _create_task_auto_impl(payload: AutoTaskCreate, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    goal = (payload.goal or "").strip()
    if not goal:
        raise HTTPException(status_code=400, detail="goal required")

    # Choose workspace: explicit > cookie default > first.
    workspaces = q_all("SELECT * FROM workspaces ORDER BY created_at ASC", ())
    if not workspaces:
        raise HTTPException(status_code=400, detail="no workspaces available")
    default_ws = request.cookies.get("default_workspace_id") or workspaces[0]["id"]
    workspace_id = payload.workspace_id or default_ws
    if not any(w["id"] == workspace_id for w in workspaces):
        workspace_id = workspaces[0]["id"]

    # Let the router pick a skill (enabled only).
    skills = q_all(
        "SELECT s.*, COALESCE(m.enabled, 1) AS enabled FROM skills s "
        "LEFT JOIN skill_meta m ON m.skill_id=s.id WHERE COALESCE(m.enabled, 1)=1 ORDER BY s.created_at ASC",
        (),
    )
    if not skills:
        raise HTTPException(status_code=400, detail="no skills available")
    skill_id = choose_skill_id(goal=goal, skills=skills, hint=payload.hint, mode=(payload.mode or "fast"))

    # Mode: request > skill default > fast
    sk = q_one("SELECT * FROM skills WHERE id=?", (skill_id,))
    mode = (payload.mode or (sk.get("default_mode") if sk else None) or "fast").strip()
    if mode not in ("fast", "pro"):
        mode = "fast"

    task_id = create_task(workspace_id=workspace_id, skill_id=skill_id, goal=goal, mode=mode)
    start_task_background(task_id)
    return {"ok": True, "task_id": task_id, "workspace_id": workspace_id, "skill_id": skill_id, "mode": mode}


@app.post("/api/tasks/auto")
def api_create_task_auto(payload: AutoTaskCreate, request: Request) -> Response:
    data = _create_task_auto_impl(payload, request)
    resp = JSONResponse(data)
    resp.set_cookie("default_workspace_id", data["workspace_id"], max_age=3600 * 24 * 365, samesite="lax")
    return resp


@app.post("/runs/auto", response_class=HTMLResponse)
def ui_create_task_auto(
    request: Request,
    goal: str = Form(""),
    mode: str = Form("fast"),
    workspace_id: str = Form(""),
    hint: str = Form(""),
) -> Response:
    """
    HTML form fallback for starting a run (works even if WebView JS is limited).
    """
    payload = AutoTaskCreate(
        goal=goal,
        mode=(mode or "fast"),
        workspace_id=(workspace_id or None),
        hint=(hint or None),
    )
    data = _create_task_auto_impl(payload, request)
    resp = RedirectResponse(url=f"/tasks/{data['task_id']}", status_code=303)
    resp.set_cookie("default_workspace_id", data["workspace_id"], max_age=3600 * 24 * 365, samesite="lax")
    return resp


@app.get("/api/tasks/{task_id}")
def api_get_task(task_id: str) -> Dict[str, Any]:
    task = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    task["plan"] = from_json(task.get("plan_json"))
    steps = q_all("SELECT * FROM steps WHERE task_id=? ORDER BY idx ASC", (task_id,))
    for s in steps:
        s["args"] = from_json(s.get("args_json")) or {}
        s["result"] = from_json(s.get("result_json"))
    approvals = q_all("SELECT * FROM approvals WHERE task_id=? ORDER BY requested_at DESC", (task_id,))
    return {"task": task, "steps": steps, "approvals": approvals}


@app.post("/api/tasks/{task_id}/approve/{step_id}")
def api_approve(task_id: str, step_id: str, payload: ApprovalDecision, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    approve_step(task_id, step_id, payload.decision, payload.reason)
    return {"ok": True}


@app.get("/api/schedules")
def api_list_schedules() -> List[Dict[str, Any]]:
    return q_all("SELECT * FROM schedules ORDER BY created_at DESC", ())


@app.post("/api/schedules")
def api_create_schedule(payload: ScheduleCreate, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    sch_id = uuid.uuid4().hex
    now = _now()
    exec_sql(
        "INSERT INTO schedules (id, name, cron_expr, workspace_id, skill_id, mode, enabled, payload_json, next_run_at, last_run_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            sch_id,
            payload.name,
            payload.cron_expr,
            payload.workspace_id,
            payload.skill_id,
            payload.mode,
            1 if payload.enabled else 0,
            to_json(payload.payload) if payload.payload else None,
            None,
            None,
            now,
            now,
        ),
    )
    return {"ok": True, "id": sch_id}


@app.post("/api/settings")
def api_update_settings(payload: SettingsUpdate, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    updates: Dict[str, Optional[str]] = {}
    updates.update(payload.provider or {})
    updates.update(payload.desktop or {})
    # Persist + apply runtime env vars (provider + desktop settings).
    update_runtime_env(updates)
    return {"ok": True}


@app.get("/api/workspace_policies")
def api_get_workspace_policies(workspace_id: str) -> Dict[str, Any]:
    policies = get_workspace_policies(workspace_id)
    return {"ok": True, "workspace_id": workspace_id, "policies": policies, "available_scopes": SCOPES, "available_policies": POLICIES}


@app.post("/api/workspace_policies")
def api_set_workspace_policies(payload: WorkspacePoliciesUpdate, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    ws = q_one("SELECT id FROM workspaces WHERE id=?", (payload.workspace_id,))
    if not ws:
        raise HTTPException(status_code=404, detail="workspace not found")
    now = _now()
    for scope, policy in (payload.policies or {}).items():
        if scope not in SCOPES:
            continue
        if policy not in POLICIES:
            continue
        set_workspace_policy(workspace_id=payload.workspace_id, scope=scope, policy=policy, updated_at=now)
    return {"ok": True, "workspace_id": payload.workspace_id, "policies": get_workspace_policies(payload.workspace_id)}


@app.post("/api/open_api_site")
def api_open_api_site(request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    import webbrowser

    url = "https://0-0.pro/"
    try:
        webbrowser.open(url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "url": url}


@app.post("/api/open_logs_dir")
def api_open_logs_dir(request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    try:
        os.startfile(str(settings.logs_dir))  # type: ignore[attr-defined]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "path": str(settings.logs_dir)}


def _tail_text_file(path: Path, *, max_lines: int = 200, max_bytes: int = 500_000) -> List[str]:
    n = max(1, min(int(max_lines), 2000))
    bcap = max(10_000, min(int(max_bytes), 2_000_000))
    try:
        with path.open("rb") as f:
            try:
                f.seek(0, io.SEEK_END)
                size = f.tell()
                start = max(0, size - bcap)
                f.seek(start, io.SEEK_SET)
            except Exception:
                start = 0
                f.seek(0)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if start > 0 and lines:
            # Drop a possibly partial line.
            lines = lines[1:]
        return lines[-n:]
    except Exception as e:
        return [f"[owb] failed to read {path.name}: {e}"]


@app.post("/api/client_log")
def api_client_log(payload: ClientLogEvent, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    record = {
        "time": _now(),
        "level": str(payload.level or "error"),
        "message": str(payload.message or ""),
        "url": str(payload.url or ""),
        "stack": str(payload.stack or ""),
        "user_agent": str(payload.user_agent or ""),
        "ts_ms": int(payload.ts_ms or 0) if payload.ts_ms is not None else None,
        "extra": payload.extra or {},
    }
    try:
        p = (settings.logs_dir / "desktop-client.log").resolve()
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    try:
        logging.getLogger("owb.client").warning("client_log %s", record.get("message") or "")
    except Exception:
        pass
    return {"ok": True}


@app.get("/api/logs/tail")
def api_logs_tail(request: Request, name: str, lines: int = 200) -> Dict[str, Any]:
    _ensure_admin(request)
    raw = str(name or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="name required")
    if "/" in raw or "\\" in raw or ".." in raw:
        raise HTTPException(status_code=400, detail="invalid name")
    p = (settings.logs_dir / raw).resolve()
    try:
        if p.parent != settings.logs_dir.resolve():
            raise HTTPException(status_code=400, detail="invalid name")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid name")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="log file not found")
    out = _tail_text_file(p, max_lines=lines)
    return {"ok": True, "name": raw, "lines": out}


@app.get("/api/diagnostics/export")
def api_export_diagnostics(request: Request) -> Response:
    _ensure_admin(request)
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    filename = f"owb-diagnostics-{ts}.zip"

    def _redact(k: str, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        if k.upper() in ("OPENAI_API_KEY", "OWB_REMOTE_TOKEN", "UI_ADMIN_TOKEN"):
            return "REDACTED"
        return v

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        meta = {
            "time": _now(),
            "platform": os.name,
            "python": os.sys.version,
            "app_name": settings.app_name,
            "data_dir": str(settings.data_dir),
        }
        z.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

        # runtime env snapshot (redacted)
        runtime_path = (settings.data_dir / "runtime_env.json")
        if runtime_path.exists():
            try:
                data = json.loads(runtime_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data = {k: _redact(k, v) for k, v in data.items()}
                z.writestr("runtime_env.json", json.dumps(data, ensure_ascii=False, indent=2))
            except Exception:
                z.writestr("runtime_env.json", runtime_path.read_text(encoding="utf-8", errors="ignore"))

        # recent tasks/steps (no secrets expected)
        tasks = q_all("SELECT * FROM tasks ORDER BY updated_at DESC LIMIT 30", ())
        z.writestr("recent_tasks.json", json.dumps(tasks, ensure_ascii=False, indent=2))
        steps = q_all("SELECT * FROM steps ORDER BY updated_at DESC LIMIT 60", ())
        z.writestr("recent_steps.json", json.dumps(steps, ensure_ascii=False, indent=2))

        # DB schema (sqlite_master)
        try:
            schema = q_all("SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type, name", ())
            z.writestr("db_schema.json", json.dumps(schema, ensure_ascii=False, indent=2))
        except Exception:
            pass

        # logs (best-effort, capped)
        logs_dir = settings.logs_dir
        if logs_dir.exists():
            files = [p for p in logs_dir.glob("**/*") if p.is_file()]
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for p in files[:30]:
                try:
                    b = p.read_bytes()
                    if len(b) > 2_000_000:
                        b = b[:2_000_000] + b"\n\n--- TRUNCATED ---\n"
                    z.writestr(f"logs/{p.name}", b)
                except Exception:
                    continue

    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename=\"{filename}\"'},
    )


@app.post("/api/kb/ingest")
def api_kb_ingest(payload: KBIngestRequest, request: Request) -> Dict[str, Any]:
    _ensure_admin(request)
    # get workspace path
    ws = q_one("SELECT * FROM workspaces WHERE id=?", (payload.workspace_id,))
    if not ws:
        raise HTTPException(status_code=404, detail="workspace not found")
    ctx = type("Ctx", (), {})()
    from .tools.base import ToolContext
    ctx = ToolContext(workspace_root=Path(ws["path"]).resolve(), task_id="kb", step_id=uuid.uuid4().hex)
    return kb_ingest(ctx, payload.model_dump())


@app.post("/api/kb/query")
def api_kb_query(payload: KBQueryRequest) -> Dict[str, Any]:
    ws = q_one("SELECT * FROM workspaces WHERE id=?", (payload.workspace_id,))
    if not ws:
        raise HTTPException(status_code=404, detail="workspace not found")
    from .tools.base import ToolContext
    ctx = ToolContext(workspace_root=Path(ws["path"]).resolve(), task_id="kb", step_id=uuid.uuid4().hex)
    return kb_query(ctx, payload.model_dump())


@app.post("/ui/workspaces/create")
def ui_create_workspace(name: str = Form(...), path: str = Form(""), token: str = Form("")) -> Response:
    # HTML form helper
    req = type("R", (), {"headers": {}, "query_params": {"token": token}})()
    _ensure_admin(req)  # type: ignore
    payload = WorkspaceCreate(name=name, path=path or None)
    api_create_workspace(payload, req)  # type: ignore
    return RedirectResponse("/workspaces", status_code=303)


@app.post("/ui/settings/workspace")
def ui_set_default_workspace(default_workspace_id: str = Form(...), token: str = Form("")) -> Response:
    req = type("R", (), {"headers": {}, "query_params": {"token": token}})()
    _ensure_admin(req)  # type: ignore
    resp = RedirectResponse("/settings", status_code=303)
    resp.set_cookie("default_workspace_id", default_workspace_id, max_age=3600 * 24 * 365, samesite="lax")
    return resp


@app.post("/ui/skills/import")
def ui_import_skill(yaml_path: str = Form(...), token: str = Form("")) -> Response:
    req = type("R", (), {"headers": {}, "query_params": {"token": token}})()
    _ensure_admin(req)  # type: ignore
    _import_skill_from_yaml(yaml_path)
    return RedirectResponse("/skills", status_code=303)


@app.post("/ui/tasks/create")
def ui_create_task(
    workspace_id: str = Form(...),
    skill_id: str = Form(...),
    goal: str = Form(...),
    mode: str = Form(""),
    token: str = Form(""),
) -> Response:
    req = type("R", (), {"headers": {}, "query_params": {"token": token}})()
    _ensure_admin(req)  # type: ignore
    payload = TaskCreate(workspace_id=workspace_id, skill_id=skill_id, goal=goal, mode=mode or None)
    resp = api_create_task(payload, req)  # type: ignore
    return RedirectResponse(f"/tasks/{resp['task_id']}", status_code=303)


@app.post("/ui/tasks/{task_id}/approve/{step_id}")
def ui_approve_task_step(
    task_id: str,
    step_id: str,
    decision: str = Form(...),
    reason: str = Form(""),
    token: str = Form(""),
) -> Response:
    req = type("R", (), {"headers": {}, "query_params": {"token": token}})()
    _ensure_admin(req)  # type: ignore
    approve_step(task_id, step_id, "approve" if decision == "approve" else "reject", reason or None)
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@app.post("/ui/tasks/{task_id}/continue")
def ui_continue_task(task_id: str, message: str = Form(""), token: str = Form("")) -> Response:
    req = type("R", (), {"headers": {}, "query_params": {"token": token}})()
    _ensure_admin(req)  # type: ignore
    msg = (message or "").strip()
    if msg:
        try:
            api_task_continue(task_id, TaskContinue(message=msg), req)  # type: ignore[arg-type]
        except Exception:
            pass
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@app.post("/ui/tasks/{task_id}/cancel")
def ui_cancel_task(task_id: str, token: str = Form(""), reason: str = Form("")) -> Response:
    req = type("R", (), {"headers": {}, "query_params": {"token": token}})()
    _ensure_admin(req)  # type: ignore
    cancel_task(task_id, reason=(reason or "").strip() or None)
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)
