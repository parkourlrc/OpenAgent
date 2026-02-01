from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import settings
from ..db import exec_sql, from_json, get_conn, q_all, q_one, to_json
from ..events import emit
from ..permissions import get_workspace_policy, grant_ask_once_scope, is_ask_once_scope_granted, scope_for_tool
from ..tools.base import ToolContext, list_tools as list_app_tools, run_tool

from .engine import _collect_artifacts, _load_skill, _load_task, _load_workspace, _now, _update_task, render_prompt_template


def _uak_db_path() -> Path:
    return (settings.data_dir / "uak.db").resolve()


_UAK_RUNNING_LOCK = threading.Lock()
_UAK_RUNNING: dict[str, dict[str, Any]] = {}


def _uak_register_running(task_id: str, *, loop: asyncio.AbstractEventLoop, task: "asyncio.Task[Any]") -> None:
    try:
        with _UAK_RUNNING_LOCK:
            _UAK_RUNNING[task_id] = {"loop": loop, "task": task}
    except Exception:
        pass


def _uak_unregister_running(task_id: str) -> None:
    try:
        with _UAK_RUNNING_LOCK:
            _UAK_RUNNING.pop(task_id, None)
    except Exception:
        pass


def cancel_uak_task(task_id: str) -> bool:
    """
    Best-effort cancellation for a running UAK task. Returns True if a running task was found and a cancel signal
    was scheduled onto its event loop.
    """
    entry: Optional[dict[str, Any]] = None
    try:
        with _UAK_RUNNING_LOCK:
            entry = _UAK_RUNNING.get(task_id)
    except Exception:
        entry = None

    if not entry:
        return False
    loop = entry.get("loop")
    task = entry.get("task")
    if loop is None or task is None:
        return False
    try:
        loop.call_soon_threadsafe(task.cancel)
        return True
    except Exception:
        return False


def _is_task_canceled(task_id: str) -> bool:
    try:
        row = q_one("SELECT status FROM tasks WHERE id=?", (task_id,))
        return bool(row) and str(row.get("status") or "").strip().lower() == "canceled"
    except Exception:
        return False


def _append_event_log(*, task_id: str, event_type: str, payload: Dict[str, Any], step_id: Optional[str] = None) -> None:
    try:
        seq: Optional[int] = None
        with get_conn() as con:
            cur = con.execute(
                "INSERT INTO event_log (id, task_id, step_id, type, payload_json, ts, created_at) VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, task_id, step_id, event_type, to_json(payload), time.time(), _now()),
            )
            seq = int(cur.lastrowid or 0) or None
        data = {"task_id": task_id, "type": event_type, "payload": payload}
        if seq is not None:
            data["seq"] = int(seq)
        emit("event_log", data)
    except Exception:
        pass


def _load_chat_history(task_id: str, *, limit: int = 200) -> List[Dict[str, str]]:
    rows = []
    try:
        rows = q_all(
            "SELECT payload_json FROM event_log WHERE task_id=? AND type='chat_message' ORDER BY ts ASC LIMIT ?",
            (task_id, int(limit)),
        )
    except Exception:
        rows = []

    out: List[Dict[str, str]] = []
    for r in rows:
        payload = from_json(r.get("payload_json")) or {}
        role = str(payload.get("role") or "").strip().lower()
        content = str(payload.get("content") or "").strip()
        if role not in ("user", "assistant", "system", "tool"):
            continue
        if not content:
            continue
        out.append({"role": role, "content": content})
    return out


def _register_workbench_tools(kernel, *, task_id: str, workspace_root: Path) -> None:
    """
    Register Workbench tools into UAK ToolRegistry.
    """
    from uak.tools.spec import ToolNetworkPermissions, ToolPermissions, ToolSpec as UAKToolSpec

    run_ctx: dict[str, Any] = {"task_id": task_id, "workspace_root": str(workspace_root)}

    def _make_handler(tool_name: str):
        def _handler(ctx, args):
            # ctx: uak.tools.spec.ToolCallContext
            tc = ToolContext(
                workspace_root=Path(run_ctx["workspace_root"]).resolve(),
                task_id=str(run_ctx["task_id"]),
                step_id=str(getattr(ctx, "step_id", "") or uuid.uuid4().hex),
            )
            return run_tool(tc, tool_name, args if isinstance(args, dict) else {})

        # Force ToolBus to pass (ctx, args)
        setattr(_handler, "__uak_accepts_context__", True)
        return _handler

    for t in list_app_tools():
        # Avoid re-registering if a tool name already exists in UAK.
        try:
            kernel.tools.get(t.name)
            continue
        except Exception:
            pass

        network_allowed = bool(str(t.name).startswith("browser."))
        perms = ToolPermissions(
            fs_roots=[],
            network=ToolNetworkPermissions(allowed=network_allowed, domains_allowlist=[]),
            secrets=[],
        )
        spec = UAKToolSpec(
            name=t.name,
            description=t.description,
            input_schema=t.json_schema,
            output_schema=None,
            risk="high" if bool(getattr(t, "risky", False)) else "low",
            side_effects="none",
            timeout_ms=120_000,
            permissions=perms,
        )
        kernel.tools.register(spec, _make_handler(t.name))


def _register_uak_evidence_tools(kernel, *, workspace_root: Path) -> None:
    """
    Register minimal evidence tools required by UAK citations mode:
    - web.search / web.fetch
    - doc.extract (workspace-relative path support)
    """
    # Patch UAK's no-key search provider to be more tolerant of DDG HTML markup changes.
    # This fixes "web.search returns empty results" which cascades into missing citations.
    try:
        import re
        from html import unescape
        from urllib.parse import parse_qs, unquote, urlencode, urlparse

        import httpx

        import uak.tools.web as uak_web

        if not getattr(uak_web.DuckDuckGoHtmlProvider, "_owb_patched", False):

            def _normalize_ddg_url(raw: str) -> str:
                u = str(raw or "").strip()
                if u.startswith("//"):
                    u = "https:" + u
                if "duckduckgo.com/l/?" in u:
                    try:
                        parsed = urlparse(u)
                        qs = parse_qs(parsed.query)
                        uddg = (qs.get("uddg") or [None])[0]
                        if uddg:
                            u = unquote(str(uddg))
                    except Exception:
                        pass
                return u

            def _strip_tags(html_text: str) -> str:
                return re.sub(r"\\s+", " ", re.sub(r"<[^>]+>", " ", str(html_text or ""))).strip()

            async def _search_openalex(*, query: str, top_k: int) -> list[uak_web.WebSearchResult]:
                ua = "OpenAgentWorkbench/1.0 (+https://0-0.pro)"
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    resp = await client.get(
                        "https://api.openalex.org/works",
                        params={"search": query, "per-page": int(max(1, min(top_k, 20)))},
                        headers={"User-Agent": ua, "Accept": "application/json"},
                    )
                    resp.raise_for_status()
                    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                items = data.get("results") if isinstance(data, dict) else None
                if not isinstance(items, list):
                    return []
                out: list[uak_web.WebSearchResult] = []
                for idx, item in enumerate(items[:top_k], start=1):
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title") or "").strip()
                    doi = str(item.get("doi") or "").strip()
                    url = doi or str(item.get("id") or "").strip()
                    if not url:
                        continue
                    year = str(item.get("publication_year") or "").strip() or None
                    snippet = ""
                    try:
                        venue = ""
                        pv = item.get("primary_location") or {}
                        hv = pv.get("source") or {}
                        venue = str(hv.get("display_name") or "").strip()
                        if venue and year:
                            snippet = f"{venue} ({year})"
                        elif venue:
                            snippet = venue
                        elif year:
                            snippet = year
                    except Exception:
                        snippet = year or ""
                    out.append(uak_web.WebSearchResult(title=title, url=url, snippet=snippet, source="openalex", rank=idx, published=year))
                return out

            async def _patched_ddg_search(
                self,
                *,
                query: str,
                top_k: int,
                language,
                time_range,
                safe_search,
            ):
                ua = (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
                )
                url = f"https://duckduckgo.com/html/?{urlencode({'q': query})}"
                headers = {"User-Agent": ua, "Accept": "text/html,application/xhtml+xml"}
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                    html = resp.text or ""

                # Split result containers; tolerate class lists (e.g. class="result results_links ...").
                blocks = re.split(r'<div[^>]+class="[^"]*results_links[^"]*"', html, flags=re.IGNORECASE)
                results: list[uak_web.WebSearchResult] = []
                for block in blocks[1:]:
                    if len(results) >= top_k:
                        break
                    link = re.search(r'class="result__a"[^>]*href="([^"]+)"', block)
                    title = re.search(r'class="result__a"[^>]*>(.*?)</a>', block, flags=re.DOTALL)
                    snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|div)>', block, flags=re.DOTALL)
                    url_val = unescape(link.group(1)) if link else ""
                    title_val = _strip_tags(unescape(title.group(1))) if title else ""
                    snip_val = _strip_tags(unescape(snippet.group(1))) if snippet else ""
                    url_val = _normalize_ddg_url(url_val)
                    if not url_val:
                        continue
                    results.append(
                        uak_web.WebSearchResult(
                            title=title_val,
                            url=url_val,
                            snippet=snip_val,
                            source="duckduckgo",
                            rank=len(results) + 1,
                            published=None,
                        )
                    )

                if results:
                    return results
                # Stable fallback: OpenAlex (no API key, JSON).
                return await _search_openalex(query=query, top_k=top_k)

            uak_web.DuckDuckGoHtmlProvider.search = _patched_ddg_search  # type: ignore[assignment]
            setattr(uak_web.DuckDuckGoHtmlProvider, "_owb_patched", True)
    except Exception:
        pass

    # doc.extract: resolve relative paths against the Workbench workspace root.
    try:
        kernel.tools.get("doc.extract")
        return
    except Exception:
        pass

    try:
        from uak.tools.documents import _doc_extract
        from uak.tools.spec import ToolAudit, ToolIdempotency, ToolPermissions, ToolRetry, ToolSpec

        def _handler(args: Dict[str, Any]) -> Any:
            a: Dict[str, Any] = args if isinstance(args, dict) else {}
            try:
                raw = str(a.get("path") or "").strip()
                raw_norm = raw.replace("\\", "/")
                while raw_norm.startswith("./"):
                    raw_norm = raw_norm[2:]
                if raw_norm.startswith("workspace/"):
                    raw_norm = raw_norm[len("workspace/") :]
                if raw and not Path(raw).is_absolute():
                    a = dict(a)
                    a["path"] = str((workspace_root / raw_norm).resolve())
            except Exception:
                pass
            return _doc_extract(a)

        kernel.tools.register(
            ToolSpec(
                name="doc.extract",
                description=(
                    "Extract readable text from a local document file under the workspace. "
                    "Returns evidence chunks for citations."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to a local file under the run's fs_roots."},
                        "prefer": {
                            "type": "string",
                            "default": "auto",
                            "description": "auto|kreuzberg|pypdf (PDF only). auto prefers kreuzberg when available.",
                        },
                        "encoding": {"type": ["string", "null"], "default": None, "description": "Text encoding hint."},
                        "max_chars": {"type": "integer", "minimum": 100, "maximum": 500000, "default": 12000},
                        "chunk_size_chars": {"type": "integer", "minimum": 200, "maximum": 20000, "default": 2000},
                        "max_chunks": {"type": "integer", "minimum": 1, "maximum": 200, "default": 6},
                    },
                    "required": ["path"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "path": {"type": "string"},
                        "title": {"type": "string"},
                        "method": {"type": "string"},
                        "text": {"type": "string"},
                        "metadata": {"type": "object"},
                        "evidence": {"type": "object"},
                    },
                    "required": ["ok"],
                },
                risk="low",
                side_effects="read",
                idempotency=ToolIdempotency(
                    supported=True,
                    key_fields=["path", "prefer", "encoding", "max_chars", "chunk_size_chars", "max_chunks"],
                ),
                timeout_ms=60_000,
                retry=ToolRetry(max_attempts=1, backoff_ms=0, retry_on=[]),
                permissions=ToolPermissions(fs_roots=[], secrets=[]),
                audit=ToolAudit(log_args=True, log_result=False, redact_fields=[]),
            ),
            _handler,
        )
    except Exception:
        pass


def _citations_required_for_goal(goal: str) -> bool:
    mode = _citations_mode()
    if mode == "off":
        return False
    if mode == "require":
        return True
    try:
        from uak.agent.engine import _goal_requests_citations  # type: ignore[attr-defined]

        return bool(_goal_requests_citations(goal))
    except Exception:
        return False


def _citations_mode() -> str:
    mode = str(os.environ.get("UAK_CITATIONS_MODE") or "auto").strip().lower() or "auto"
    if mode.startswith("off") or mode in {"0", "false", "no"}:
        return "off"
    if mode.startswith("require") or mode in {"1", "true", "yes", "on"}:
        return "require"
    return "auto"


def _goal_requests_ppt(goal: str) -> bool:
    g = str(goal or "")
    gl = g.lower()
    if any(k in gl for k in ("ppt", "pptx", "powerpoint", "slides")):
        return True
    # Chinese keywords
    return any(k in g for k in ("幻灯片", "演示文稿", "课件", "PPT"))


async def _uak_extract_last_llm_output(*, stores, run_id: str) -> tuple[str, str]:
    """
    Best-effort salvage: when UAK fails an output guardrail, extract the last assistant text
    from UAK's llm_recordings table, and return (text, last_guardrail_reason).
    """
    if not run_id:
        return "", ""
    db_path = getattr(stores, "db_path", None)
    if not db_path:
        return "", ""
    try:
        import aiosqlite
        import json as _json

        async with aiosqlite.connect(str(db_path)) as db:
            # Find last guardrail failure reason (if any).
            reason = ""
            try:
                cur = await db.execute(
                    "SELECT payload_json FROM events WHERE run_id=? AND type='guardrail.failed' ORDER BY id DESC LIMIT 1",
                    (run_id,),
                )
                row = await cur.fetchone()
                await cur.close()
                if row and row[0]:
                    payload = _json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
                    if isinstance(payload, dict):
                        reason = str(payload.get("reason") or "") or ""
            except Exception:
                reason = ""

            # Locate the last llm call id for the main agent (exclude context summary calls).
            llm_call_id = ""
            try:
                cur = await db.execute(
                    """
                    SELECT payload_json
                    FROM events
                    WHERE run_id=?
                      AND type='llm.completed'
                      AND source_component='agent'
                      AND source_name NOT LIKE '%:context_summary'
                    ORDER BY id DESC
                    LIMIT 10
                    """,
                    (run_id,),
                )
                rows = await cur.fetchall()
                await cur.close()
                for r in rows or []:
                    pj = r[0] if r else None
                    if not pj:
                        continue
                    payload = _json.loads(pj) if isinstance(pj, str) else (pj or {})
                    if not isinstance(payload, dict):
                        continue
                    cid = str(payload.get("llm_call_id") or "").strip()
                    if cid:
                        llm_call_id = cid
                        break
            except Exception:
                llm_call_id = ""

            # Pull the recorded LLM content (this includes the content that failed guardrails).
            if llm_call_id:
                cur = await db.execute(
                    """
                    SELECT response_json
                    FROM llm_recordings
                    WHERE run_id=? AND llm_call_id=? AND status='DONE'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (run_id, llm_call_id),
                )
                row = await cur.fetchone()
                await cur.close()
                if row and row[0]:
                    resp = _json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
                    if isinstance(resp, dict) and isinstance(resp.get("content"), str):
                        return str(resp.get("content") or ""), reason

            # Fallback: last DONE recording for the run.
            cur = await db.execute(
                "SELECT response_json FROM llm_recordings WHERE run_id=? AND status='DONE' ORDER BY id DESC LIMIT 1",
                (run_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if row and row[0]:
                resp = _json.loads(row[0]) if isinstance(row[0], str) else (row[0] or {})
                if isinstance(resp, dict) and isinstance(resp.get("content"), str):
                    return str(resp.get("content") or ""), reason
    except Exception:
        pass
    return "", ""


def _patch_uak_ppt_render_tool(kernel, *, task_id: str) -> None:
    """
    Ensure UAK's built-in `ppt.render` writes into the Workbench artifacts directory so it shows up in the UI and
    gets bundled into run reports.
    """
    try:
        rt = kernel.tools.get("ppt.render")
    except Exception:
        return
    try:
        from uak.tools.registry import RegisteredTool
    except Exception:
        return

    spec = getattr(rt, "spec", None)
    handler = getattr(rt, "handler", None)
    if spec is None or handler is None:
        return

    def _wrapped(ctx, args):
        a: Dict[str, Any] = args if isinstance(args, dict) else {}
        out_path_raw = str(a.get("output_path") or "").strip()
        if not out_path_raw:
            step_id = str(getattr(ctx, "step_id", "") or uuid.uuid4().hex)
            out_path = (settings.artifacts_dir / task_id / step_id / "deck.pptx").resolve()
            a = dict(a)
            a["output_path"] = str(out_path)
        try:
            return handler(ctx=ctx, args=a)
        except TypeError:
            return handler(ctx, a)

    setattr(_wrapped, "__uak_accepts_context__", True)
    try:
        # ToolRegistry doesn't support overwrite; patch the registry entry for this kernel only.
        kernel.tools._tools["ppt.render"] = RegisteredTool(spec=spec, handler=_wrapped)  # type: ignore[attr-defined]
    except Exception:
        return


async def _uak_get_last_llm_recording(*, stores, run_id: str) -> dict[str, Any]:
    """
    Best-effort diagnostic helper for "empty output" cases. Returns a dict containing the most recent
    llm_recordings row (sanity: only run-local, stored in uak.db).
    """
    if not run_id:
        return {}
    db_path = getattr(stores, "db_path", None)
    if not db_path:
        return {}
    try:
        import aiosqlite
        import json as _json

        async with aiosqlite.connect(str(db_path)) as db:
            cur = await db.execute(
                "SELECT request_json, response_json, error_json, status FROM llm_recordings WHERE run_id=? ORDER BY id DESC LIMIT 1",
                (run_id,),
            )
            row = await cur.fetchone()
            await cur.close()
            if not row:
                return {}
            req = _json.loads(row[0]) if isinstance(row[0], str) and row[0] else (row[0] or {})
            resp = _json.loads(row[1]) if isinstance(row[1], str) and row[1] else (row[1] or {})
            err = _json.loads(row[2]) if isinstance(row[2], str) and row[2] else (row[2] or {})
            status = str(row[3] or "")
            out: dict[str, Any] = {"status": status}
            if isinstance(req, dict):
                out["request"] = req
            if isinstance(resp, dict):
                out["response"] = resp
            if isinstance(err, dict):
                out["error"] = err
            return out
    except Exception:
        return {}


class _WorkbenchPolicyEngine:
    """
    UAK policy engine that respects Workbench workspace policies:
    - ask_once / always_allow / always_deny per scope
    - fs_read defaults to always_allow (within fs_roots)
    - fs_write defaults to always_allow (within fs_roots)
    - network defaults to always_allow unless a policy is set
    """

    def __init__(self, *, workspace_id: str, task_id: str):
        from uak.tools.policy import PolicyEngine as DefaultPolicyEngine

        self._base = DefaultPolicyEngine()
        self._workspace_id = workspace_id
        self._task_id = task_id

    def decide(self, tool, args, ctx, *, already_approved: bool):  # pragma: no cover - exercised via runtime
        from uak.id import ulid
        from uak.models import InterruptInputSpec, InterruptReason, InterruptSpec
        from uak.tools.policy import PolicyDecision

        base = self._base.decide(tool, args, ctx, already_approved=already_approved)
        # Never override hard denies (e.g., outside fs_roots).
        if (not base.allow) or base.mode == "deny":
            return base

        scope = scope_for_tool(getattr(tool, "name", "") or "")
        pol = get_workspace_policy(self._workspace_id, scope)
        default_pol = "always_allow" if scope in {"network", "fs_read", "fs_write"} else "ask_once"
        policy = pol or default_pol

        # Workbench "network" scope: only opt-in if user set a policy.
        if scope == "network" and pol is None:
            return base

        if policy == "always_allow":
            return PolicyDecision(allow=True, mode="auto", reason="workspace_policy_always_allow")
        if policy == "always_deny":
            # Only deny when we'd otherwise have required an approval, or for opted-in network gating.
            if scope in {"network", "mcp"} or base.mode == "require_approval":
                return PolicyDecision(allow=False, mode="deny", reason="workspace_policy_always_deny")
            return base

        # ask_once
        if is_ask_once_scope_granted(self._task_id, scope):
            return PolicyDecision(allow=True, mode="auto", reason="workspace_policy_ask_once_granted")

        # If base already needs approval, keep its interrupt; otherwise add one for opted-in network gating.
        interrupt = base.interrupt
        if interrupt is None:
            interrupt = InterruptSpec(
                reason=InterruptReason.approval_required,
                risk_level=getattr(tool, "risk", "low"),
                requested_inputs=[
                    InterruptInputSpec(name="approve", type="boolean", description=f"Approve '{tool.name}'?"),
                ],
                resume_token=ulid(),
            )
        return PolicyDecision(allow=True, mode="require_approval", reason="workspace_policy_ask_once", interrupt=interrupt)


async def _sync_uak_events_to_event_log(*, task_id: str, stores, run_id: str, stop: asyncio.Event) -> None:
    """
    Best-effort: tail UAK events and mirror them into Workbench event_log for the UI timeline.
    """
    offset = 0
    try:
        row = q_one("SELECT backend_last_offset FROM tasks WHERE id=?", (task_id,))
        if row and row.get("backend_last_offset") is not None:
            offset = int(row["backend_last_offset"] or 0)
    except Exception:
        offset = 0

    def _should_keep(ev_type: str) -> bool:
        prefixes = ("run.", "step.", "llm.", "tool.", "approval.", "interrupt.", "guardrail.", "mcp.", "handoff.", "verifier.")
        return any(ev_type.startswith(p) for p in prefixes)

    def _map_step_status(ev_type: str) -> str:
        t = str(ev_type or "").strip().lower()
        if t == "step.failed":
            return "failed"
        if t == "step.completed":
            return "succeeded"
        if t == "step.started":
            return "running"
        if t == "step.scheduled":
            return "queued"
        return ""

    def _upsert_uak_step(*, uak_step_id: str, ev_type: str, ev: Dict[str, Any]) -> None:
        """
        Mirror UAK step metadata into Workbench `steps` so the UI can always render stable step titles
        (even when the event timeline is tailed/truncated).
        """
        sid = str(uak_step_id or "").strip()
        if not sid:
            return

        name = ""
        try:
            payload = ev.get("payload")
            if isinstance(payload, dict):
                name = str(payload.get("node") or "").strip()
        except Exception:
            name = ""

        status = _map_step_status(ev_type)
        now = _now()

        try:
            row = q_one("SELECT id, idx, name, status FROM steps WHERE id=? AND task_id=?", (sid, task_id))
        except Exception:
            row = None

        if not row:
            # Assign an idx in first-seen order for the task (stable + deterministic).
            try:
                last = q_one("SELECT MAX(idx) AS m FROM steps WHERE task_id=?", (task_id,))
                start_idx = (int(last["m"]) + 1) if last and last.get("m") is not None else 0
            except Exception:
                start_idx = 0

            nm = name or f"Step {start_idx + 1}"
            st = status or "running"
            try:
                exec_sql(
                    "INSERT OR IGNORE INTO steps (id, task_id, idx, name, tool, args_json, status, requires_approval, result_json, error, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sid,
                        task_id,
                        int(start_idx),
                        nm,
                        "uak",
                        to_json({}),
                        st,
                        0,
                        None,
                        None,
                        now,
                        now,
                    ),
                )
            except Exception:
                return
            row = {"id": sid, "idx": int(start_idx), "name": nm, "status": st}

        # Best-effort updates (do not overwrite with empty values).
        fields: Dict[str, Any] = {}
        try:
            if name and str(row.get("name") or "").strip() != name:
                fields["name"] = name
        except Exception:
            pass
        try:
            if status and str(row.get("status") or "").strip().lower() != status:
                fields["status"] = status
        except Exception:
            pass

        if fields:
            try:
                fields["updated_at"] = now
                sets = ", ".join([f"{k}=?" for k in fields.keys()])
                params = tuple(fields.values()) + (sid,)
                exec_sql(f"UPDATE steps SET {sets} WHERE id=?", params)
            except Exception:
                pass

    while not stop.is_set():
        try:
            batch = await stores.list_events(run_id, from_offset=offset, limit=200)
        except Exception:
            await asyncio.sleep(0.3)
            continue
        if not batch:
            await asyncio.sleep(0.2)
            continue
        for ev in batch:
            try:
                offset = int(ev.get("offset") or offset)
                ev_type = str(ev.get("type") or "")
                if not ev_type or not _should_keep(ev_type):
                    continue
                if ev_type.startswith("step."):
                    try:
                        _upsert_uak_step(uak_step_id=str(ev.get("step_id") or ""), ev_type=ev_type, ev=ev)
                    except Exception:
                        pass
                payload = {
                    "uak": True,
                    "run_id": run_id,
                    "event": ev,
                }
                _append_event_log(task_id=task_id, step_id=ev.get("step_id"), event_type="uak_event", payload=payload)
            except Exception:
                continue
        try:
            exec_sql("UPDATE tasks SET backend_last_offset=?, updated_at=? WHERE id=?", (offset, _now(), task_id))
        except Exception:
            pass


def _insert_pending_approval_step(*, task_id: str, tool_name: str, tool_call_id: str) -> str:
    step_id = uuid.uuid4().hex
    now = _now()
    last = q_one("SELECT MAX(idx) AS m FROM steps WHERE task_id=?", (task_id,))
    start_idx = (last["m"] + 1) if last and last["m"] is not None else 0
    exec_sql(
        "INSERT INTO steps (id, task_id, idx, name, tool, args_json, status, requires_approval, result_json, error, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            step_id,
            task_id,
            int(start_idx),
            f"Approval: {tool_name}",
            tool_name,
            to_json({"tool_call_id": tool_call_id}),
            "waiting_approval",
            1,
            None,
            None,
            now,
            now,
        ),
    )
    return step_id


def _write_uak_report(*, ws_root: Path, task_id: str, goal: str, run_id: str, output: Any) -> Dict[str, str]:
    out_dir = ws_root / "outputs" / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "report.md"
    html_path = out_dir / "report.html"

    artifacts = _collect_artifacts(task_id)

    def _as_text(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        try:
            import json as _json

            return _json.dumps(v, ensure_ascii=False, indent=2)
        except Exception:
            return str(v)

    md_lines = []
    md_lines.append(f"# Run Report: {task_id}")
    md_lines.append("")
    md_lines.append(f"## Backend")
    md_lines.append(f"- runtime: uak")
    md_lines.append(f"- run_id: `{run_id}`")
    md_lines.append(f"- uak_db: `{_uak_db_path()}`")
    md_lines.append("")
    md_lines.append("## Goal")
    md_lines.append(goal or "")
    md_lines.append("")
    md_lines.append("## Output")
    md_lines.append(_as_text(output))
    md_lines.append("")
    md_lines.append("## Artifacts")
    if artifacts:
        for a in artifacts:
            md_lines.append(f"- `{a['path']}` ({a['size']} bytes)")
    else:
        md_lines.append("_No artifacts generated._")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    html = "<html><head><meta charset='utf-8'><title>Run Report</title></head><body>"
    html += "<pre>" + (md_path.read_text(encoding="utf-8").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")) + "</pre>"
    html += "</body></html>"
    html_path.write_text(html, encoding="utf-8")
    return {"report_md": str(md_path), "report_html": str(html_path)}


def run_task_uak_background(task_id: str) -> None:
    th = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(th)
        try:
            if not _is_task_canceled(task_id):
                _update_task(task_id, status="running", error=None, backend_interrupt_id=None, backend_resume_token=None)
        except Exception:
            pass
        main_task = th.create_task(_run_task_uak(task_id=task_id, resume=None, goal=None, history=None))
        _uak_register_running(task_id, loop=th, task=main_task)
        th.run_until_complete(main_task)
    except BaseException as e:
        # Always convert uncaught UAK/import errors into a visible task failure instead of "stuck queued".
        tb = traceback.format_exc(limit=30)
        try:
            logging.getLogger("owb.uak").exception("uak_task_failed task_id=%s", task_id)
        except Exception:
            pass
        try:
            if not _is_task_canceled(task_id):
                _update_task(task_id, status="failed", error=f"{e}\n{tb}")
        except Exception:
            pass
    finally:
        _uak_unregister_running(task_id)
        try:
            th.close()
        except Exception:
            pass


def resume_task_uak_background(*, task_id: str, approve: bool) -> None:
    th = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(th)
        try:
            if not _is_task_canceled(task_id):
                _update_task(task_id, status="running", error=None)
        except Exception:
            pass
        main_task = th.create_task(_run_task_uak(task_id=task_id, resume=approve, goal=None, history=None))
        _uak_register_running(task_id, loop=th, task=main_task)
        th.run_until_complete(main_task)
    except BaseException as e:
        tb = traceback.format_exc(limit=30)
        try:
            logging.getLogger("owb.uak").exception("uak_task_failed task_id=%s resume=%s", task_id, approve)
        except Exception:
            pass
        try:
            if not _is_task_canceled(task_id):
                _update_task(task_id, status="failed", error=f"{e}\n{tb}")
        except Exception:
            pass
    finally:
        _uak_unregister_running(task_id)
        try:
            th.close()
        except Exception:
            pass


def continue_task_uak_background(*, task_id: str, message: str) -> None:
    msg = (message or "").strip()
    if not msg:
        return

    # Load history before appending the new user message.
    history = _load_chat_history(task_id)
    _append_event_log(task_id=task_id, event_type="chat_message", payload={"role": "user", "content": msg})

    th = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(th)
        try:
            if not _is_task_canceled(task_id):
                _update_task(task_id, status="running", error=None)
        except Exception:
            pass
        main_task = th.create_task(_run_task_uak(task_id=task_id, resume=None, goal=msg, history=history))
        _uak_register_running(task_id, loop=th, task=main_task)
        th.run_until_complete(main_task)
    except BaseException as e:
        tb = traceback.format_exc(limit=30)
        try:
            logging.getLogger("owb.uak").exception("uak_task_failed task_id=%s continue=1", task_id)
        except Exception:
            pass
        try:
            if not _is_task_canceled(task_id):
                _update_task(task_id, status="failed", error=f"{e}\n{tb}")
        except Exception:
            pass
    finally:
        _uak_unregister_running(task_id)
        try:
            th.close()
        except Exception:
            pass


def _extract_assistant_text(output: Any) -> str:
    if isinstance(output, dict):
        if isinstance(output.get("output"), str):
            return output.get("output") or ""
        if isinstance(output.get("content"), str):
            return output.get("content") or ""
    if isinstance(output, str):
        return output
    try:
        return str(output)
    except Exception:
        return ""


async def _run_task_uak(*, task_id: str, resume: Optional[bool], goal: Optional[str], history: Optional[List[Dict[str, str]]]) -> None:
    """
    Run/resume a task via UAK runtime.

    - resume=None: start a fresh run
    - resume=True/False: resume from stored interrupt with approval decision
    """
    # This Workbench instance is expected to have access to *all* UAK skills.
    # Force-enable optional skills (third-party integrators can keep UAK's default "core" profile).
    os.environ["UAK_SKILL_PROFILE_DEFAULT"] = "all"

    from uak.agent.models import AgentSpec, ModelPolicy
    from uak.errors import InterruptRaised
    from uak.kernel import build_single_node_kernel
    from uak.versions import compute_kernel_versions

    # Load task/workspace/skill from Workbench DB
    t = _load_task(task_id)
    ws = _load_workspace(t["workspace_id"])
    sk = _load_skill(t["skill_id"])
    ws_root = Path(ws["path"]).resolve()

    # Clear classic plan/steps if this task is managed by UAK.
    if resume is None:
        try:
            exec_sql("DELETE FROM steps WHERE task_id=?", (task_id,))
            exec_sql("DELETE FROM approvals WHERE task_id=?", (task_id,))
        except Exception:
            pass

    # Build a short-lived kernel for this run/resume.
    # Use a Workbench-tweaked OpenAI-compatible provider to tolerate gateway quirks (e.g. reasoning_content streaming).
    from .uak_provider import WorkbenchOpenAIChatProvider

    api_key = os.environ.get("OPENAI_API_KEY") or ""
    base_url = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    llm_provider = WorkbenchOpenAIChatProvider(api_key=api_key, base_url=base_url)

    # Allow UAK tools to write run artifacts into the Workbench artifacts directory (outside workspace_root).
    # Keep it task-scoped for minimal surface area.
    artifacts_root = (settings.artifacts_dir / task_id).resolve()
    try:
        artifacts_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # MCP servers (configured in Workbench settings). Inject enabled servers into the UAK kernel so tools become available.
    mcp_servers_cfg = []
    try:
        from uak.tools.mcp import MCPServerConfig

        rows = q_all("SELECT * FROM mcp_servers WHERE enabled=1 ORDER BY updated_at DESC", ())
        for r in rows:
            name = str(r.get("name") or "").strip() or str(r.get("id") or "").strip()
            command = str(r.get("command") or "").strip() or None
            args = from_json(r.get("args_json")) or []
            env = from_json(r.get("env_json")) or {}
            env2 = {str(k): str(v) for k, v in (env or {}).items() if str(k).strip()}
            args2 = [str(x) for x in (args or []) if str(x).strip()]
            if not name:
                continue
            if not command:
                continue
            mcp_servers_cfg.append(MCPServerConfig(name=name, command=command, args=args2, env=env2))
    except Exception:
        mcp_servers_cfg = []

    def _build_kernel(*, mcp_servers: list[Any]) -> Any:
        # UAK requires an explicit enable flag for MCP.
        os.environ["UAK_MCP_ENABLED"] = "1" if mcp_servers else "0"
        k = build_single_node_kernel(
            db_path=_uak_db_path(),
            fs_roots=[str(ws_root), str(artifacts_root)],
            network_allowed=True,
            llm_provider=llm_provider,
            mcp_servers=mcp_servers or None,
        )
        # Swap policy engine to respect Workbench workspace policies.
        try:
            k.toolbus.policy = _WorkbenchPolicyEngine(workspace_id=ws["id"], task_id=task_id)  # type: ignore[attr-defined]
        except Exception:
            pass
        _register_workbench_tools(k, task_id=task_id, workspace_root=ws_root)
        _register_uak_evidence_tools(k, workspace_root=ws_root)
        _patch_uak_ppt_render_tool(k, task_id=task_id)
        return k

    kernel = _build_kernel(mcp_servers=mcp_servers_cfg)

    # Register an agent derived from the selected Workbench skill.
    agent_id = str(sk.get("id") or sk.get("name") or "default")
    model_fast = os.getenv("OPENAI_MODEL_FAST") or settings.model_fast
    model_pro = os.getenv("OPENAI_MODEL_PRO") or settings.model_pro
    primary = model_fast if t.get("mode") == "fast" else model_pro

    # Resolve goal early so tool allowlists can adapt to citations requirements.
    goal_text = str((goal or "").strip() or t.get("goal") or "")
    try:
        for m in reversed(list(history or _load_chat_history(task_id))):
            if m.get("role") == "user" and m.get("content"):
                goal_text = str(m["content"])
                break
    except Exception:
        pass

    skill_allowed_tools = list(sk.get("allowed_tools") or [])
    if not skill_allowed_tools:
        # Workbench semantics: empty list means "all Workbench tools".
        tool_allowlist = [t.name for t in list_app_tools()]
    else:
        tool_allowlist = [str(x).strip() for x in skill_allowed_tools if isinstance(x, str) and str(x).strip()]

    citations_mode = _citations_mode()
    citations_required = _citations_required_for_goal(goal_text)
    ppt_requested = _goal_requests_ppt(goal_text)
    if citations_required:
        for tn in ("web.search", "web.fetch", "doc.extract"):
            if tn not in tool_allowlist:
                tool_allowlist.append(tn)
    if ppt_requested and "ppt.render" not in tool_allowlist:
        tool_allowlist.append("ppt.render")
    # Allow Workbench agents to delegate to any UAK skill.
    if "skill.handoff" not in tool_allowlist:
        tool_allowlist.append("skill.handoff")
    # Expose MCP tools (if any enabled servers are configured) with wildcard allowlist.
    # Fine-grained approval is enforced by Workbench workspace policies via _WorkbenchPolicyEngine (scope=mcp).
    if mcp_servers_cfg and "mcp/*" not in tool_allowlist:
        tool_allowlist.append("mcp/*")
    prompt_vars = {
        "task_id": task_id,
        "workspace_root": str(ws_root),
        "outputs_dir": str((ws_root / "outputs" / task_id).resolve()),
        "artifacts_dir": str((settings.artifacts_dir / task_id).resolve()),
    }
    skill_prompt = render_prompt_template(str(sk.get("system_prompt") or "").strip(), vars=prompt_vars).strip()
    run_context = (
        "RUN_CONTEXT (do not ask the user for these):\n"
        f"- task_id: {task_id}\n"
        f"- workspace_root: {ws_root}\n"
        f"- outputs_dir: outputs/{task_id}\n"
        f"- artifacts_dir: {settings.artifacts_dir / task_id}\n"
        "- network: available (use web.search/web.fetch when needed)\n"
        "\n"
        "Filesystem paths are relative to workspace_root. If a path starts with 'workspace/', treat it as workspace_root.\n"
    ).strip()
    autonomy = (
        "AUTONOMY:\n"
        "- Do not ask the user to confirm your plan.\n"
        "- Do not narrate what you will do; proceed to tool calls.\n"
        "- Ask at most ONE clarification question only if truly blocked.\n"
        "- If reasonable assumptions are possible, state them briefly and proceed.\n"
    ).strip()
    instructions = (run_context + "\n\n" + autonomy + ("\n\n" + skill_prompt if skill_prompt else "")).strip()
    if citations_required:
        instructions = (instructions + "\n\n" + "Citations required:\n- Use evidence tools (web.search/web.fetch/doc.extract).\n- Add inline markers like [chunk:<chunk_id>] for key claims.\n- Only cite chunk_ids that appear in tool outputs.\n").strip()
    if ppt_requested:
        instructions = (
            instructions
            + "\n\n"
            + "If the user asks for a PPT/slides:\n"
            + "- Create a concise slide outline and call `ppt.render` to generate a .pptx artifact.\n"
            + f"- When calling `ppt.render`, set `output_path` to an absolute path under: {artifacts_root}\n"
            + "- Include citations (paper title/DOI/URL) in slide `citations` and add at least one [chunk:<chunk_id>] marker in your final answer.\n"
        ).strip()
    kernel.agents.upsert(
        AgentSpec(
            agent_id=agent_id,
            instructions=instructions,
            tool_allowlist=tool_allowlist,
            model_policy=ModelPolicy(primary=primary, fast=model_fast, fallback=[model_pro]),
        )
    )

    stop = asyncio.Event()
    event_task: Optional[asyncio.Task] = None

    try:
        if _is_task_canceled(task_id):
            return
        try:
            await kernel.initialize()
        except Exception as e_init:
            # Robustness: MCP server discovery can fail (missing deps/command issues).
            # Do not fail the whole run; continue without MCP tools and surface a clear warning to the user.
            if mcp_servers_cfg:
                try:
                    _append_event_log(
                        task_id=task_id,
                        event_type="chat_message",
                        payload={
                            "role": "system",
                            "content": (
                                "MCP 服务器初始化失败，已自动忽略 MCP 并继续运行。\n"
                                f"错误：{str(e_init)[:300]}"
                            ),
                        },
                    )
                except Exception:
                    pass
                try:
                    await kernel.shutdown()
                except Exception:
                    pass
                mcp_servers_cfg = []
                kernel = _build_kernel(mcp_servers=[])
                kernel.agents.upsert(
                    AgentSpec(
                        agent_id=agent_id,
                        instructions=instructions,
                        tool_allowlist=tool_allowlist,
                        model_policy=ModelPolicy(primary=primary, fast=model_fast, fallback=[model_pro]),
                    )
                )
                await kernel.initialize()
            else:
                raise
        # Use the latest user message as the run goal (for reports + UI).
        goal_text = str((goal or "").strip() or t.get("goal") or "")
        try:
            for m in reversed(_load_chat_history(task_id)):
                if m.get("role") == "user" and m.get("content"):
                    goal_text = str(m["content"])
                    break
        except Exception:
            pass

        # Create or reuse run_id.
        run_id = str(t.get("backend_run_id") or "")
        thread_id = str(t.get("backend_thread_id") or "")
        if resume is None:
            if _is_task_canceled(task_id):
                return
            run = await kernel.runtime.create_run(goal=goal_text)
            run_id = run.run_id
            thread_id = run.thread_id
            _update_task(
                task_id,
                backend="uak",
                backend_run_id=run_id,
                backend_thread_id=thread_id,
                backend_interrupt_id=None,
                backend_resume_token=None,
                backend_last_offset=0,
            )
        else:
            if not run_id:
                raise RuntimeError("missing backend_run_id for resume")

        # Tail UAK events into Workbench timeline.
        event_task = asyncio.create_task(
            _sync_uak_events_to_event_log(task_id=task_id, stores=kernel.stores, run_id=run_id, stop=stop)
        )

        # Build a deterministic UAK skill runtime.
        skill_rt = kernel.skills.build("agent", goal=goal_text, agent_id=agent_id)
        initial_state = dict(skill_rt.initial_state or {})
        try:
            cur = initial_state.get("ctx_extras")
            ctx_extras = dict(cur) if isinstance(cur, dict) else {}
            ctx_extras["citations_mode"] = citations_mode
            ctx_extras["artifacts_dir"] = str(artifacts_root)
            ctx_extras["workspace_root"] = str(ws_root)
            initial_state["ctx_extras"] = ctx_extras
        except Exception:
            pass
        if history:
            # Keep only previous messages (current user message is the run goal).
            initial_state["messages"] = list(history)
        versions = compute_kernel_versions(kernel)

        if resume is None:
            if _is_task_canceled(task_id):
                return
            _update_task(task_id, status="running", error=None)
            out = await kernel.runtime.start_run(
                run_id=run_id,
                graph=skill_rt.graph,
                initial_state=initial_state,
                state_schema=skill_rt.state_schema,
                reducers=skill_rt.reducers,
                versions=versions,
            )
            if _is_task_canceled(task_id):
                return
            assistant_text = _extract_assistant_text(out).strip()
            if not assistant_text:
                artifacts = _collect_artifacts(task_id)
                if artifacts:
                    assistant_text = f"任务已完成，已生成 {len(artifacts)} 个输出文件，请在右侧 Artifacts 查看。"
                else:
                    llm_rec = await _uak_get_last_llm_recording(stores=kernel.stores, run_id=run_id)
                    diag: dict[str, Any] = {}
                    try:
                        req = llm_rec.get("request") if isinstance(llm_rec.get("request"), dict) else {}
                        resp = llm_rec.get("response") if isinstance(llm_rec.get("response"), dict) else {}
                        raw = resp.get("raw") if isinstance(resp.get("raw"), dict) else {}
                        diag = {
                            "llm_recording_status": llm_rec.get("status"),
                            "model": req.get("model"),
                            "stream_meta": raw.get("owb_stream_meta"),
                            "stream_empty": bool(raw.get("owb_stream_empty")),
                            "stream_preview": raw.get("owb_stream_preview"),
                            "llm_error": llm_rec.get("error"),
                        }
                    except Exception:
                        diag = {"llm_recording": llm_rec}

                    assistant_msg = "模型未返回可展示的输出（content 为空）。请打开报告查看详情。"
                    err_msg = "UAK run returned empty output (no content/tool_calls/artifacts)."
                    try:
                        resp = llm_rec.get("response") if isinstance(llm_rec.get("response"), dict) else {}
                        raw = resp.get("raw") if isinstance(resp.get("raw"), dict) else {}
                        if bool(raw.get("owb_stream_empty")):
                            assistant_msg = (
                                "模型网关返回了空流（没有 content/tool_calls）。请检查 API Key / base_url / 模型名 / 网络后重试。"
                            )
                            err_msg = "Gateway returned empty SSE stream (no content/tool_calls)."
                    except Exception:
                        pass

                    report_paths = _write_uak_report(
                        ws_root=ws_root,
                        task_id=task_id,
                        goal=goal_text,
                        run_id=run_id,
                        output={"output": out, "diagnosis": diag, "error": err_msg},
                    )
                    _append_event_log(
                        task_id=task_id,
                        event_type="chat_message",
                        payload={"role": "assistant", "content": assistant_msg},
                    )
                    if _is_task_canceled(task_id):
                        return
                    _update_task(
                        task_id,
                        status="failed",
                        error=err_msg,
                        output_path=report_paths["report_md"],
                        backend_interrupt_id=None,
                        backend_resume_token=None,
                    )
                    return

            report_paths = _write_uak_report(ws_root=ws_root, task_id=task_id, goal=goal_text, run_id=run_id, output=out)
            _append_event_log(task_id=task_id, event_type="chat_message", payload={"role": "assistant", "content": assistant_text})
            if _is_task_canceled(task_id):
                return
            _update_task(task_id, status="succeeded", error=None, output_path=report_paths["report_md"])
            try:
                exec_sql("UPDATE tasks SET backend_interrupt_id=NULL, backend_resume_token=NULL WHERE id=?", (task_id,))
            except Exception:
                pass
            return

        # resume: apply approval decision
        interrupt_id = str(t.get("backend_interrupt_id") or "")
        resume_token = str(t.get("backend_resume_token") or "")
        if not interrupt_id or not resume_token:
            raise RuntimeError("missing interrupt for resume")

        if _is_task_canceled(task_id):
            return
        _update_task(task_id, status="running", error=None)
        out = await kernel.runtime.resume_run(
            run_id=run_id,
            interrupt_id=interrupt_id,
            resume_token=resume_token,
            inputs={"approve": bool(resume)},
            graph=skill_rt.graph,
            reducers=skill_rt.reducers,
            state_schema=skill_rt.state_schema,
            verifier_names=[],
        )
        if _is_task_canceled(task_id):
            return

        # If resume succeeded to completion, grant ask-once scope for the pending tool.
        if bool(resume):
            try:
                snap = await kernel.stores.load_latest_snapshot(run_id)
                if snap and isinstance(snap.state, dict):
                    pending_tool = str(snap.state.get("pending_tool_name") or "")
                    if pending_tool:
                        grant_ask_once_scope(task_id, scope_for_tool(pending_tool))
            except Exception:
                pass

        assistant_text = _extract_assistant_text(out).strip()
        if not assistant_text:
            artifacts = _collect_artifacts(task_id)
            if artifacts:
                assistant_text = f"任务已完成，已生成 {len(artifacts)} 个输出文件，请在右侧 Artifacts 查看。"
            else:
                llm_rec = await _uak_get_last_llm_recording(stores=kernel.stores, run_id=run_id)
                diag: dict[str, Any] = {}
                try:
                    req = llm_rec.get("request") if isinstance(llm_rec.get("request"), dict) else {}
                    resp = llm_rec.get("response") if isinstance(llm_rec.get("response"), dict) else {}
                    raw = resp.get("raw") if isinstance(resp.get("raw"), dict) else {}
                    diag = {
                        "llm_recording_status": llm_rec.get("status"),
                        "model": req.get("model"),
                        "stream_meta": raw.get("owb_stream_meta"),
                        "stream_empty": bool(raw.get("owb_stream_empty")),
                        "stream_preview": raw.get("owb_stream_preview"),
                        "llm_error": llm_rec.get("error"),
                    }
                except Exception:
                    diag = {"llm_recording": llm_rec}

                assistant_msg = "模型未返回可展示的输出（content 为空）。请打开报告查看详情。"
                err_msg = "UAK run returned empty output (no content/tool_calls/artifacts)."
                try:
                    resp = llm_rec.get("response") if isinstance(llm_rec.get("response"), dict) else {}
                    raw = resp.get("raw") if isinstance(resp.get("raw"), dict) else {}
                    if bool(raw.get("owb_stream_empty")):
                        assistant_msg = "模型网关返回了空流（没有 content/tool_calls）。请检查 API Key / base_url / 模型名 / 网络后重试。"
                        err_msg = "Gateway returned empty SSE stream (no content/tool_calls)."
                except Exception:
                    pass

                report_paths = _write_uak_report(
                    ws_root=ws_root,
                    task_id=task_id,
                    goal=goal_text,
                    run_id=run_id,
                    output={"output": out, "diagnosis": diag, "error": err_msg},
                )
                _append_event_log(
                    task_id=task_id,
                    event_type="chat_message",
                    payload={"role": "assistant", "content": assistant_msg},
                )
                if _is_task_canceled(task_id):
                    return
                _update_task(
                    task_id,
                    status="failed",
                    error=err_msg,
                    output_path=report_paths["report_md"],
                    backend_interrupt_id=None,
                    backend_resume_token=None,
                )
                return

        report_paths = _write_uak_report(ws_root=ws_root, task_id=task_id, goal=goal_text, run_id=run_id, output=out)
        _append_event_log(task_id=task_id, event_type="chat_message", payload={"role": "assistant", "content": assistant_text})
        if _is_task_canceled(task_id):
            return
        _update_task(
            task_id,
            status="succeeded",
            error=None,
            output_path=report_paths["report_md"],
            backend_interrupt_id=None,
            backend_resume_token=None,
        )
        return

    except asyncio.CancelledError:
        # User-initiated cancellation.
        try:
            if not _is_task_canceled(task_id):
                _update_task(
                    task_id,
                    status="canceled",
                    error="Canceled by user.",
                    backend_interrupt_id=None,
                    backend_resume_token=None,
                )
        except Exception:
            pass
        return

    except InterruptRaised as e:
        # Interrupted (usually approval_required). Record interrupt info for UI + resume.
        try:
            t2 = _load_task(task_id)
            run_id = str(t2.get("backend_run_id") or "")
        except Exception:
            run_id = ""
        try:
            _update_task(task_id, status="waiting_approval", backend_interrupt_id=e.interrupt_id, backend_resume_token=e.resume_token)
        except Exception:
            pass

        # Create a pending approval step in Workbench DB for existing UI flows.
        try:
            tool_call_id = ""
            tool_name = "approval_required"
            if run_id:
                snap = await kernel.stores.load_latest_snapshot(run_id)
                if snap and isinstance(snap.state, dict):
                    tool_call_id = str(snap.state.get("pending_tool_call_id") or "")
                    tool_name = str(snap.state.get("pending_tool_name") or tool_name)
            step_id = _insert_pending_approval_step(task_id=task_id, tool_name=tool_name, tool_call_id=tool_call_id)
            from .engine import _create_approval

            _create_approval(task_id, step_id, tool_name=tool_name)
        except Exception:
            pass
        return

    except Exception as e:
        msg = str(e).strip()
        if msg.startswith("empty_stream_output:") or msg.startswith("gateway_stream_error:"):
            if _is_task_canceled(task_id):
                return
            _append_event_log(
                task_id=task_id,
                event_type="chat_message",
                payload={
                    "role": "assistant",
                    "content": "模型网关返回异常（流式输出失败）。请检查 API Key / base_url / 模型名 / 网络后重试。",
                },
            )
            _update_task(task_id, status="failed", error=msg)
            return
        if str(e).strip() == "output_guardrail_failed":
            # Do not hard-fail the whole run on citation guardrail issues; salvage the last model output and surface
            # a clear warning so the user can retry with evidence tools if desired.
            try:
                t2 = _load_task(task_id)
                ws2 = _load_workspace(t2["workspace_id"])
                ws_root2 = Path(ws2["path"]).resolve()
                run_id2 = str(t2.get("backend_run_id") or "")
                goal2 = str(t2.get("goal") or "")
                try:
                    for m in reversed(_load_chat_history(task_id)):
                        if m.get("role") == "user" and m.get("content"):
                            goal2 = str(m["content"])
                            break
                except Exception:
                    pass

                text, reason = await _uak_extract_last_llm_output(stores=kernel.stores, run_id=run_id2)
                text = (text or "").strip()
                citation_reasons = {"missing_citations", "invalid_citations", "missing_evidence_pack", "missing_evidence"}
                if reason in citation_reasons:
                    warn = "输出未通过引用校验，已展示模型最后一次输出供参考。"
                    warn += "\n如需引用：请提供可引用材料（文件/链接）并允许联网；如不需要引用，请在指令中注明“无需引用/来源”。"
                else:
                    warn = "输出未通过校验，已展示模型最后一次输出供参考。"
                if reason:
                    warn = warn + f"（{reason}）"

                if not text:
                    _update_task(task_id, status="failed", error=warn)
                    return

                _append_event_log(task_id=task_id, event_type="chat_message", payload={"role": "assistant", "content": text})
                _append_event_log(task_id=task_id, event_type="chat_message", payload={"role": "system", "content": warn})

                report_paths = _write_uak_report(
                    ws_root=ws_root2,
                    task_id=task_id,
                    goal=goal2,
                    run_id=run_id2,
                    output={"output": text, "warning": warn, "guardrail": reason},
                )
                # Guardrail failures should surface as failures in the UI (even if we display the last output for debugging).
                _update_task(
                    task_id,
                    status="failed",
                    error=warn,
                    output_path=report_paths["report_md"],
                    backend_interrupt_id=None,
                    backend_resume_token=None,
                )
                try:
                    exec_sql("UPDATE tasks SET backend_interrupt_id=NULL, backend_resume_token=NULL WHERE id=?", (task_id,))
                except Exception:
                    pass
                return
            except Exception:
                # Fall back to the generic error path below.
                pass
        tb = traceback.format_exc(limit=12)
        if _is_task_canceled(task_id):
            return
        _update_task(task_id, status="failed", error=f"{e}\n{tb}")
        return

    finally:
        try:
            stop.set()
        except Exception:
            pass
        if event_task is not None:
            try:
                await asyncio.wait_for(event_task, timeout=1.0)
            except Exception:
                pass
        try:
            await kernel.shutdown()
        except Exception:
            pass
