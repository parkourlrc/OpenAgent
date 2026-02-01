from __future__ import annotations

import threading
from typing import Dict, Optional

from .db import exec_sql, q_all, q_one


POLICIES = ("ask_once", "always_allow", "always_deny")
SCOPES = ("shell", "fs_read", "fs_write", "fs_delete", "browser_click", "network", "mcp", "other")


def scope_for_tool(tool_name: str) -> str:
    t = (tool_name or "").strip()
    if t == "shell.exec":
        return "shell"
    # UAK built-ins (artifact generation). Treat as local file writes under the workspace/artifacts roots.
    if t == "ppt.render":
        return "fs_write"
    if t in ("filesystem.list", "filesystem.read_text", "filesystem.stat"):
        return "fs_read"
    if t in ("filesystem.write_text", "filesystem.mkdir", "filesystem.move"):
        return "fs_write"
    if t == "filesystem.delete":
        return "fs_delete"
    if t == "browser.click":
        return "browser_click"
    if t.startswith("web."):
        return "network"
    if t.startswith("browser."):
        return "network"
    if t.startswith("mcp/"):
        return "mcp"
    return "other"


def get_workspace_policies(workspace_id: str) -> Dict[str, str]:
    rows = q_all("SELECT scope, policy FROM workspace_policies WHERE workspace_id=?", (workspace_id,))
    out: Dict[str, str] = {}
    for r in rows:
        scope = str(r.get("scope") or "")
        policy = str(r.get("policy") or "")
        if scope and policy in POLICIES:
            out[scope] = policy
    return out


def get_workspace_policy(workspace_id: str, scope: str) -> Optional[str]:
    row = q_one("SELECT policy FROM workspace_policies WHERE workspace_id=? AND scope=?", (workspace_id, scope))
    if not row:
        return None
    pol = str(row.get("policy") or "")
    return pol if pol in POLICIES else None


def set_workspace_policy(*, workspace_id: str, scope: str, policy: str, updated_at: str) -> None:
    if policy not in POLICIES:
        raise ValueError("invalid policy")
    exec_sql(
        "INSERT INTO workspace_policies (workspace_id, scope, policy, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(workspace_id, scope) DO UPDATE SET policy=excluded.policy, updated_at=excluded.updated_at",
        (workspace_id, scope, policy, updated_at),
    )


_ASK_ONCE_LOCK = threading.Lock()
_ASK_ONCE_GRANTS: Dict[str, Dict[str, bool]] = {}  # task_id -> scope -> granted


def is_ask_once_scope_granted(task_id: str, scope: str) -> bool:
    with _ASK_ONCE_LOCK:
        return bool(_ASK_ONCE_GRANTS.get(task_id, {}).get(scope))


def grant_ask_once_scope(task_id: str, scope: str) -> None:
    if not scope:
        return
    with _ASK_ONCE_LOCK:
        d = _ASK_ONCE_GRANTS.setdefault(task_id, {})
        d[scope] = True


def clear_task_grants(task_id: str) -> None:
    with _ASK_ONCE_LOCK:
        _ASK_ONCE_GRANTS.pop(task_id, None)
