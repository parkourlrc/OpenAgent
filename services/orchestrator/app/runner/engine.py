from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import settings
from ..events import emit
from ..db import exec_sql, from_json, get_conn, q_all, q_one, to_json
from ..tools.base import ToolContext, get_tool, run_tool
from .planner import generate_plan
from .executor import propose_patch
from .critic import review as critic_review
from ..permissions import scope_for_tool, get_workspace_policy, grant_ask_once_scope, is_ask_once_scope_granted


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _uuid() -> str:
    return uuid.uuid4().hex


def render_prompt_template(text: str, *, vars: Dict[str, str]) -> str:
    """
    Render a small subset of prompt placeholders deterministically.

    Supports:
    - <var>
    - {{ var }} / {{var}}
    """
    out = str(text or "")
    if not out or not vars:
        return out
    try:
        import re

        for k, v in vars.items():
            if not k:
                continue
            val = str(v)
            out = out.replace(f"<{k}>", val)
            out = re.sub(r"\{\{\s*" + re.escape(k) + r"\s*\}\}", val, out)
    except Exception:
        # Best-effort only; never break runs due to templating.
        for k, v in vars.items():
            try:
                out = out.replace(f"<{k}>", str(v))
            except Exception:
                pass
    return out


def _log_event(*, task_id: str, step_id: Optional[str], event_type: str, payload: Dict[str, Any]) -> Optional[int]:
    try:
        with get_conn() as con:
            cur = con.execute(
                "INSERT INTO event_log (id, task_id, step_id, type, payload_json, ts, created_at) VALUES (?,?,?,?,?,?,?)",
                (_uuid(), task_id, step_id, event_type, to_json(payload), time.time(), _now()),
            )
            return int(cur.lastrowid or 0) or None
    except Exception:
        return None


def create_workspace(*, name: str, path: Path) -> Dict[str, Any]:
    ws_id = _uuid()
    created_at = _now()
    exec_sql("INSERT INTO workspaces (id, name, path, created_at) VALUES (?,?,?,?)", (ws_id, name, str(path), created_at))
    return {"id": ws_id, "name": name, "path": str(path), "created_at": created_at}


def create_skill(*, name: str, description: str, yaml_path: Optional[str], system_prompt: str, allowed_tools: List[str], default_mode: str) -> Dict[str, Any]:
    skill_id = _uuid()
    created_at = _now()
    exec_sql(
        "INSERT INTO skills (id, name, description, yaml_path, system_prompt, allowed_tools_json, default_mode, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (skill_id, name, description, yaml_path, system_prompt, to_json(allowed_tools), default_mode, created_at),
    )
    # Track enable/disable and provenance separately to avoid schema migration.
    exec_sql(
        "INSERT OR IGNORE INTO skill_meta (skill_id, enabled, source, updated_at) VALUES (?,?,?,?)",
        (skill_id, 1, yaml_path or "", created_at),
    )
    return {"id": skill_id}


def create_task(*, workspace_id: str, skill_id: str, goal: str, mode: str) -> str:
    task_id = _uuid()
    now = _now()
    exec_sql(
        "INSERT INTO tasks (id, workspace_id, skill_id, status, mode, goal, plan_json, created_at, updated_at, current_step, output_path, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, workspace_id, skill_id, "queued", mode, goal, None, now, now, 0, None, None),
    )
    # Seed chat history for the task.
    try:
        payload = {"role": "user", "content": goal}
        seq = _log_event(task_id=task_id, step_id=None, event_type="chat_message", payload=payload)
        data = {"task_id": task_id, "type": "chat_message", "payload": payload}
        if seq is not None:
            data["seq"] = int(seq)
        emit("event_log", data)
    except Exception:
        pass
    return task_id


def _update_task(task_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    sets = ", ".join([f"{k}=?" for k in fields.keys()])
    params = tuple(fields.values()) + (task_id,)
    exec_sql(f"UPDATE tasks SET {sets} WHERE id=?", params)
    try:
        emit("task_update", {"task_id": task_id, "fields": fields})
    except Exception:
        pass
    _log_event(task_id=task_id, step_id=None, event_type="task_update", payload={"fields": fields})


def _update_step(step_id: str, **fields: Any) -> None:
    fields["updated_at"] = _now()
    sets = ", ".join([f"{k}=?" for k in fields.keys()])
    params = tuple(fields.values()) + (step_id,)
    exec_sql(f"UPDATE steps SET {sets} WHERE id=?", params)
    task_id: Optional[str] = None
    try:
        row = q_one("SELECT task_id FROM steps WHERE id=?", (step_id,))
        task_id = row["task_id"] if row else None
    except Exception:
        task_id = None
    try:
        emit("step_update", {"step_id": step_id, "task_id": task_id, "fields": fields})
    except Exception:
        pass
    if task_id:
        _log_event(task_id=task_id, step_id=step_id, event_type="step_update", payload={"fields": fields})


def _insert_steps(task_id: str, steps: List[Dict[str, Any]], start_idx: int) -> None:
    now = _now()
    for offset, s in enumerate(steps):
        step_id = _uuid()
        idx = start_idx + offset
        exec_sql(
            "INSERT INTO steps (id, task_id, idx, name, tool, args_json, status, requires_approval, result_json, error, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                step_id,
                task_id,
                idx,
                s.get("name") or f"Step {idx+1}",
                s["tool"],
                to_json(s.get("args") or {}),
                "pending",
                1 if bool(s.get("requires_approval", False)) else 0,
                None,
                None,
                now,
                now,
            ),
        )


def _load_workspace(workspace_id: str) -> Dict[str, Any]:
    ws = q_one("SELECT * FROM workspaces WHERE id=?", (workspace_id,))
    if not ws:
        raise KeyError("workspace not found")
    return ws


def _load_skill(skill_id: str) -> Dict[str, Any]:
    sk = q_one("SELECT * FROM skills WHERE id=?", (skill_id,))
    if not sk:
        raise KeyError("skill not found")
    sk["allowed_tools"] = from_json(sk.get("allowed_tools_json")) or []
    return sk


def _load_task(task_id: str) -> Dict[str, Any]:
    t = q_one("SELECT * FROM tasks WHERE id=?", (task_id,))
    if not t:
        raise KeyError("task not found")
    t["plan"] = from_json(t.get("plan_json"))
    return t


def _load_steps(task_id: str) -> List[Dict[str, Any]]:
    rows = q_all("SELECT * FROM steps WHERE task_id=? ORDER BY idx ASC", (task_id,))
    for r in rows:
        r["args"] = from_json(r.get("args_json")) or {}
        r["result"] = from_json(r.get("result_json"))
        r["requires_approval"] = bool(r.get("requires_approval"))
    return rows


def _create_approval(task_id: str, step_id: str, *, tool_name: str) -> None:
    approval_id = _uuid()
    exec_sql(
        "INSERT INTO approvals (id, task_id, step_id, status, requested_at, decided_at, decision, reason) VALUES (?,?,?,?,?,?,?,?)",
        (approval_id, task_id, step_id, "pending", _now(), None, None, None),
    )
    # Also surface as a system chat bubble so users can respond conversationally (works across WebView/desktop clients).
    try:
        scope = scope_for_tool(tool_name)
        content = (
            f"Approval required for tool: `{tool_name}` ({scope}). Reply: approve / reject.\n"
            f"需要确认：是否允许调用工具 `{tool_name}`（{scope}）。请回复：同意 / 拒绝。"
        )
        payload2 = {"role": "system", "content": content}
        seq = _log_event(task_id=task_id, step_id=step_id, event_type="chat_message", payload=payload2)
        data = {"task_id": task_id, "type": "chat_message", "payload": payload2}
        if seq is not None:
            data["seq"] = int(seq)
        emit("event_log", data)
    except Exception:
        pass
    try:
        _log_event(
            task_id=task_id,
            step_id=step_id,
            event_type="approval_requested",
            payload={"tool": tool_name, "scope": scope_for_tool(tool_name), "approval_id": approval_id},
        )
    except Exception:
        pass
    try:
        emit(
            "approval_requested",
            {"task_id": task_id, "step_id": step_id, "tool": tool_name, "scope": scope_for_tool(tool_name), "approval_id": approval_id},
        )
    except Exception:
        pass


def _approval_status_for_step(step_id: str) -> Optional[str]:
    row = q_one("SELECT * FROM approvals WHERE step_id=? ORDER BY requested_at DESC LIMIT 1", (step_id,))
    return row["status"] if row else None


def _tool_requires_approval(tool_name: str) -> bool:
    # policy: risky tools require approval; can be loosened by config
    if tool_name == "shell.exec":
        return settings.require_approval_shell
    if tool_name in ("filesystem.write_text", "filesystem.mkdir", "filesystem.move"):
        return settings.require_approval_fs_write
    if tool_name == "filesystem.delete":
        return settings.require_approval_fs_delete
    if tool_name == "browser.click":
        return settings.require_approval_browser_click
    # others: only if tool spec marks risky
    try:
        spec = get_tool(tool_name)
        return spec.risky
    except Exception:
        return True


def _collect_artifacts(task_id: str) -> List[Dict[str, Any]]:
    # Collect files under artifacts_dir/task_id
    base = settings.artifacts_dir / task_id
    if not base.exists():
        return []
    items = []
    for p in base.rglob("*"):
        if p.is_file():
            items.append({"path": str(p), "size": p.stat().st_size})
    return items


def _write_run_report(ws_root: Path, task_id: str, goal: str, plan: Dict[str, Any], steps: List[Dict[str, Any]], artifacts: List[Dict[str, Any]]) -> Dict[str, str]:
    out_dir = ws_root / "outputs" / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "report.md"
    html_path = out_dir / "report.html"

    md_lines = []
    md_lines.append(f"# Run Report: {task_id}")
    md_lines.append("")
    md_lines.append(f"## Goal")
    md_lines.append(goal)
    md_lines.append("")
    md_lines.append("## Plan Summary")
    md_lines.append(plan.get("summary", ""))
    md_lines.append("")
    md_lines.append("## Steps")
    for s in steps:
        status = s["status"]
        md_lines.append(f"- **{s['idx']+1}. {s['name']}** (`{s['tool']}`) — {status}")
        if s.get("error"):
            md_lines.append(f"  - Error: {s['error']}")
    md_lines.append("")
    md_lines.append("## Artifacts")
    if artifacts:
        for a in artifacts:
            md_lines.append(f"- `{a['path']}` ({a['size']} bytes)")
    else:
        md_lines.append("_No artifacts generated._")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    # Simple HTML wrapper
    html = "<html><head><meta charset='utf-8'><title>Run Report</title></head><body>"
    html += "<pre>" + (md_path.read_text(encoding="utf-8").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")) + "</pre>"
    html += "</body></html>"
    html_path.write_text(html, encoding="utf-8")

    return {"report_md": str(md_path), "report_html": str(html_path)}


def _apply_patch(task_id: str, patch: Dict[str, Any]) -> None:
    # patch schema:
    # {
    #   reason, add_steps, replace_steps_from_idx, remove_steps
    # }
    replace_from = patch.get("replace_steps_from_idx")
    remove_steps = patch.get("remove_steps") or []
    add_steps = patch.get("add_steps") or []
    # remove specified idxs
    for idx in remove_steps:
        exec_sql("DELETE FROM steps WHERE task_id=? AND idx=?", (task_id, int(idx)))
    if replace_from is not None:
        exec_sql("DELETE FROM steps WHERE task_id=? AND idx>=?", (task_id, int(replace_from)))
        # renumber? simplest: keep idxs as provided by planner; we append from replace_from sequentially
        _insert_steps(task_id, add_steps, start_idx=int(replace_from))
    else:
        # append at end
        last = q_one("SELECT MAX(idx) AS m FROM steps WHERE task_id=?", (task_id,))
        start_idx = (last["m"] + 1) if last and last["m"] is not None else 0
        _insert_steps(task_id, add_steps, start_idx=start_idx)


def _is_task_canceled(task_id: str) -> bool:
    try:
        row = q_one("SELECT status FROM tasks WHERE id=?", (task_id,))
        return bool(row) and str(row.get("status") or "").strip().lower() == "canceled"
    except Exception:
        return False


def run_task(task_id: str) -> None:
    # main runner loop; can be resumed if waiting_approval
    try:
        t = _load_task(task_id)
        if str(t.get("status") or "").strip().lower() == "canceled":
            return
        ws = _load_workspace(t["workspace_id"])
        sk = _load_skill(t["skill_id"])
        ws_root = Path(ws["path"]).resolve()
        allowed_tools: List[str] = sk["allowed_tools"] or []
        prompt_vars = {
            "task_id": task_id,
            "workspace_root": str(ws_root),
            "outputs_dir": str((ws_root / "outputs" / task_id).resolve()),
            "artifacts_dir": str((settings.artifacts_dir / task_id).resolve()),
        }
        skill_prompt = render_prompt_template(str(sk.get("system_prompt") or ""), vars=prompt_vars)

        # planning if no plan
        if not t.get("plan"):
            if _is_task_canceled(task_id):
                return
            _update_task(task_id, status="planning")
            plan = generate_plan(goal=t["goal"], allowed_tools=allowed_tools, mode=t["mode"], skill_system_prompt=skill_prompt)
            _update_task(task_id, plan_json=to_json(plan))
            # insert steps
            exec_sql("DELETE FROM steps WHERE task_id=?", (task_id,))
            _insert_steps(task_id, plan["steps"], start_idx=0)
            if _is_task_canceled(task_id):
                return
            _update_task(task_id, status="running", current_step=0)
        else:
            if _is_task_canceled(task_id):
                return
            _update_task(task_id, status="running")

        # execution loop
        max_critic_iters = 3
        for critic_iter in range(max_critic_iters):
            if _is_task_canceled(task_id):
                return
            t = _load_task(task_id)
            plan = t["plan"] or {}
            steps = _load_steps(task_id)
            idx = int(t["current_step"])

            while idx < len(steps):
                if _is_task_canceled(task_id):
                    return
                step = steps[idx]
                if step["status"] in ("succeeded",):
                    idx += 1
                    _update_task(task_id, current_step=idx)
                    continue

                # Check if waiting approval
                if step["status"] == "waiting_approval":
                    ap = _approval_status_for_step(step["id"])
                    if ap == "approved":
                        # proceed to execute now
                        _update_step(step["id"], status="pending")
                    else:
                        if not _is_task_canceled(task_id):
                            _update_task(task_id, status="waiting_approval")
                        return  # stop until approval

                _update_step(step["id"], status="running", error=None)

                tool_name = step["tool"]
                scope = scope_for_tool(tool_name)
                requires_approval = bool(step["requires_approval"]) or _tool_requires_approval(tool_name)

                # Allow per-workspace policies to opt-in for network scope.
                if not requires_approval and scope == "network":
                    pol = get_workspace_policy(ws["id"], scope)
                    if pol and pol != "always_allow":
                        requires_approval = True

                # If already approved for this step, do not ask again.
                ap_status = _approval_status_for_step(step["id"])
                if requires_approval:
                    policy = get_workspace_policy(ws["id"], scope) or ("always_allow" if scope == "network" else "ask_once")
                    if policy == "always_deny":
                        msg = f"Denied by policy ({scope})."
                        _update_step(step["id"], status="failed", error=msg)
                        _update_task(task_id, status="failed", error=msg)
                        return
                    if policy == "always_allow" or (policy == "ask_once" and is_ask_once_scope_granted(task_id, scope)):
                        _update_step(step["id"], requires_approval=0)
                    elif ap_status != "approved":
                        # create approval record and pause
                        _create_approval(task_id, step["id"], tool_name=tool_name)
                        _update_step(step["id"], status="waiting_approval", requires_approval=1)
                        _update_task(task_id, status="waiting_approval")
                        return
                    else:
                        # already approved for this step
                        _update_step(step["id"], requires_approval=0)

                # run tool
                ctx = ToolContext(workspace_root=ws_root, task_id=task_id, step_id=step["id"])
                try:
                    result = run_tool(ctx, step["tool"], step["args"])
                    _update_step(step["id"], status="succeeded", result_json=to_json(result))
                    idx += 1
                    if _is_task_canceled(task_id):
                        return
                    _update_task(task_id, current_step=idx, status="running")
                except Exception as e:
                    tb = traceback.format_exc(limit=8)
                    _update_step(step["id"], status="failed", error=str(e))
                    if _is_task_canceled(task_id):
                        return
                    _update_task(task_id, status="failed", error=f"{e}\n{tb}")
                    return

                # optional plan patching (executor)
                recent = []
                try:
                    recent = [result] if isinstance(result, dict) else []
                except Exception:
                    recent = []
                try:
                    patch = propose_patch(goal=t["goal"], plan=plan, current_step_idx=idx, recent_results=recent, allowed_tools=allowed_tools, mode=t["mode"], skill_system_prompt=skill_prompt)
                    if patch:
                        _apply_patch(task_id, patch)
                        steps = _load_steps(task_id)  # reload after patch
                except Exception:
                    # ignore patch failures
                    pass

            # all steps done for this critic iteration
            artifacts = _collect_artifacts(task_id)
            report_paths = _write_run_report(ws_root, task_id, t["goal"], plan, steps, artifacts)
            _update_task(task_id, output_path=report_paths["report_md"])

            crit = critic_review(goal=t["goal"], plan=plan, artifacts=artifacts, mode=t["mode"], skill_system_prompt=skill_prompt)
            if crit.get("ok") is True:
                if _is_task_canceled(task_id):
                    return
                _update_task(task_id, status="succeeded", error=None)
                return

            # add fix steps and continue another iteration
            fix_steps = crit.get("fix_steps") or []
            if not fix_steps:
                if _is_task_canceled(task_id):
                    return
                _update_task(task_id, status="failed", error="Critic reported issues but provided no fix steps.")
                return
            # append fix steps and loop
            _apply_patch(task_id, {"add_steps": fix_steps, "replace_steps_from_idx": None, "remove_steps": [], "reason": "critic_fix"})
            # continue: will pick up new steps
            if _is_task_canceled(task_id):
                return
            _update_task(task_id, status="running")

        # if we exhaust iterations
        if _is_task_canceled(task_id):
            return
        _update_task(task_id, status="failed", error="Exceeded critic iterations; run did not converge.")
    except Exception as e:
        tb = traceback.format_exc(limit=10)
        if _is_task_canceled(task_id):
            return
        _update_task(task_id, status="failed", error=f"{e}\n{tb}")


def start_task_background(task_id: str) -> None:
    backend = ""
    try:
        st = q_one("SELECT status FROM tasks WHERE id=?", (task_id,))
        if st and str(st.get("status") or "").strip().lower() == "canceled":
            return
        row = q_one("SELECT backend FROM tasks WHERE id=?", (task_id,))
        backend = str((row or {}).get("backend") or "").strip().lower()
    except Exception:
        backend = ""

    if backend not in ("classic", "uak"):
        pref = (os.getenv("OWB_AGENT_BACKEND") or "").strip().lower()
        if pref in ("classic", "legacy"):
            backend = "classic"
        elif pref == "uak":
            backend = "uak"
        else:
            # Auto: prefer UAK when installed.
            try:
                import uak  # type: ignore

                backend = "uak"
            except Exception:
                backend = "classic"
        try:
            _update_task(task_id, backend=backend)
        except Exception:
            pass

    if backend == "uak":
        try:
            from .uak_engine import run_task_uak_background

            t = threading.Thread(target=run_task_uak_background, args=(task_id,), daemon=True)
        except Exception:
            # If UAK is missing/broken, fall back to classic.
            try:
                _update_task(task_id, backend="classic")
            except Exception:
                pass
            t = threading.Thread(target=run_task, args=(task_id,), daemon=True)
    else:
        t = threading.Thread(target=run_task, args=(task_id,), daemon=True)
    t.start()


def cancel_task(task_id: str, *, reason: Optional[str] = None) -> bool:
    row = q_one("SELECT status, backend FROM tasks WHERE id=?", (task_id,))
    if not row:
        return False
    status = str(row.get("status") or "").strip().lower()
    if status in ("succeeded", "failed", "canceled"):
        return True

    msg = (reason or "").strip() or "Canceled by user."
    try:
        _update_task(task_id, status="canceled", error=msg, backend_interrupt_id=None, backend_resume_token=None)
    except Exception:
        try:
            _update_task(task_id, status="canceled", error=msg)
        except Exception:
            pass

    backend = str(row.get("backend") or "").strip().lower()
    if backend == "uak":
        try:
            from .uak_engine import cancel_uak_task

            cancel_uak_task(task_id)
        except Exception:
            pass

    return True


def continue_task_background(*, task_id: str, message: str) -> None:
    msg = (message or "").strip()
    if not msg:
        return

    try:
        row = q_one("SELECT backend FROM tasks WHERE id=?", (task_id,))
        backend = str((row or {}).get("backend") or "").strip().lower()
    except Exception:
        backend = ""

    if backend != "uak":
        raise RuntimeError("continue is supported only for UAK backend tasks")

    from .uak_engine import continue_task_uak_background

    t = threading.Thread(target=continue_task_uak_background, kwargs={"task_id": task_id, "message": msg}, daemon=True)
    t.start()


def approve_step(task_id: str, step_id: str, decision: str, reason: Optional[str]) -> None:
    # decision approve/reject
    status = "approved" if decision == "approve" else "rejected"
    exec_sql(
        "UPDATE approvals SET status=?, decided_at=?, decision=?, reason=? WHERE id=(SELECT id FROM approvals WHERE step_id=? ORDER BY requested_at DESC LIMIT 1)",
        (status, _now(), decision, reason, step_id),
    )
    if status == "approved":
        try:
            step = q_one("SELECT tool FROM steps WHERE id=?", (step_id,))
            if step and step.get("tool"):
                tool = str(step["tool"])
                scope = scope_for_tool(tool)
                grant_ask_once_scope(task_id, scope)
                _log_event(
                    task_id=task_id,
                    step_id=step_id,
                    event_type="approval_decided",
                    payload={"decision": "approve", "reason": reason or "", "tool": tool, "scope": scope},
                )
                emit("approval_decided", {"task_id": task_id, "step_id": step_id, "decision": "approve", "reason": reason or "", "tool": tool, "scope": scope})
        except Exception:
            pass

        # If this is a UAK-backed task, resume via UAK runtime.
        try:
            row = q_one("SELECT backend FROM tasks WHERE id=?", (task_id,))
            backend = str((row or {}).get("backend") or "").strip().lower()
        except Exception:
            backend = ""
        if backend == "uak":
            try:
                from .uak_engine import resume_task_uak_background

                _update_task(task_id, status="running")
                t = threading.Thread(target=resume_task_uak_background, kwargs={"task_id": task_id, "approve": True}, daemon=True)
                t.start()
                return
            except Exception:
                pass

        # Classic resume
        _update_task(task_id, status="running")
        start_task_background(task_id)
    else:
        try:
            step = q_one("SELECT tool FROM steps WHERE id=?", (step_id,))
            tool = str(step["tool"]) if step and step.get("tool") else ""
            _log_event(
                task_id=task_id,
                step_id=step_id,
                event_type="approval_decided",
                payload={"decision": "reject", "reason": reason or "", "tool": tool, "scope": scope_for_tool(tool) if tool else ""},
            )
            emit(
                "approval_decided",
                {"task_id": task_id, "step_id": step_id, "decision": "reject", "reason": reason or "", "tool": tool, "scope": scope_for_tool(tool) if tool else ""},
            )
        except Exception:
            pass

        # For UAK-backed tasks, resume with approve=false so the trace records the denial.
        try:
            row = q_one("SELECT backend FROM tasks WHERE id=?", (task_id,))
            backend = str((row or {}).get("backend") or "").strip().lower()
        except Exception:
            backend = ""
        if backend == "uak":
            try:
                from .uak_engine import resume_task_uak_background

                _update_step(step_id, status="failed", error=f"Rejected by user: {reason or ''}".strip())
                _update_task(task_id, status="running", error=None)
                t = threading.Thread(target=resume_task_uak_background, kwargs={"task_id": task_id, "approve": False}, daemon=True)
                t.start()
                return
            except Exception:
                pass

        _update_step(step_id, status="failed", error=f"Rejected by user: {reason or ''}".strip())
        _update_task(task_id, status="failed", error=f"Rejected by user: {reason or ''}".strip())
