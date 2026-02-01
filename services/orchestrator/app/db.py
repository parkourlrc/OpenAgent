from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import settings


_DB_LOCK = threading.Lock()


def _dict_factory(cursor: sqlite3.Cursor, row: Tuple[Any, ...]) -> Dict[str, Any]:
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


def connect() -> sqlite3.Connection:
    # Ensure parent folders exist (desktop .exe runs under AppData).
    try:
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    last_err: Optional[BaseException] = None
    for i in range(5):
        try:
            con = sqlite3.connect(str(settings.db_path), check_same_thread=False, timeout=30)
            con.row_factory = _dict_factory
            # WAL improves concurrent read/write patterns, but can fail transiently if the file is being created.
            try:
                con.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError:
                con.execute("PRAGMA journal_mode=DELETE;")
            con.execute("PRAGMA foreign_keys=ON;")
            return con
        except sqlite3.OperationalError as e:
            last_err = e
            msg = str(e).lower()
            if "disk i/o error" in msg or "unable to open database file" in msg:
                time.sleep(0.2 * (i + 1))
                continue
            raise
    raise sqlite3.OperationalError(f"failed to open sqlite db: {last_err}")


@contextmanager
def get_conn() -> Iterable[sqlite3.Connection]:
    # sqlite is fine with multiple connections + WAL; lock schema ops
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _DB_LOCK:
        with get_conn() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS skills (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    yaml_path TEXT,
                    system_prompt TEXT NOT NULL,
                    allowed_tools_json TEXT NOT NULL,
                    default_mode TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    plan_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    current_step INTEGER NOT NULL DEFAULT 0,
                    output_path TEXT,
                    error TEXT,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                    FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS steps (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    idx INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requires_approval INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    decided_at TEXT,
                    decision TEXT,
                    reason TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    FOREIGN KEY(step_id) REFERENCES steps(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    cron_expr TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    payload_json TEXT,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                    FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
                );

                -- Event log for timeline/replay.
                CREATE TABLE IF NOT EXISTS event_log (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    step_id TEXT,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    ts REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_event_log_task_ts ON event_log (task_id, ts);

                -- Workspace-level permission policies (ask_once / always_allow / always_deny).
                CREATE TABLE IF NOT EXISTS workspace_policies (
                    workspace_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    policy TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, scope),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
                );

                -- Recipes/templates for one-click workflows.
                CREATE TABLE IF NOT EXISTS recipes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    goal_template TEXT NOT NULL,
                    form_json TEXT,
                    default_mode TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                -- Extra skill metadata (enabled flag, install source, etc).
                CREATE TABLE IF NOT EXISTS skill_meta (
                    skill_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    source TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(skill_id) REFERENCES skills(id) ON DELETE CASCADE
                );

                -- MCP server registry (config + enable/disable + healthcheck args).
                CREATE TABLE IF NOT EXISTS mcp_servers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    command TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    env_json TEXT NOT NULL,
                    healthcheck_args_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS kb_docs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    indexed_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS kb_chunks (
                    id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    chunk_idx INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    embedding_blob BLOB NOT NULL,
                    embedding_dim INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(doc_id) REFERENCES kb_docs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_steps_task ON steps(task_id);
                CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id);
                CREATE INDEX IF NOT EXISTS idx_kb_docs_workspace ON kb_docs(workspace_id);
                """
            )

            # Best-effort schema evolution for runner backends (e.g., UAK).
            # SQLite has no IF NOT EXISTS for columns; ignore errors if already applied.
            for ddl in (
                "ALTER TABLE tasks ADD COLUMN backend TEXT;",
                "ALTER TABLE tasks ADD COLUMN backend_run_id TEXT;",
                "ALTER TABLE tasks ADD COLUMN backend_thread_id TEXT;",
                "ALTER TABLE tasks ADD COLUMN backend_interrupt_id TEXT;",
                "ALTER TABLE tasks ADD COLUMN backend_resume_token TEXT;",
                "ALTER TABLE tasks ADD COLUMN backend_last_offset INTEGER;",
            ):
                try:
                    con.execute(ddl)
                except Exception:
                    pass


def q_one(sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
    with get_conn() as con:
        cur = con.execute(sql, params)
        row = cur.fetchone()
        return row


def q_all(sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
    with get_conn() as con:
        cur = con.execute(sql, params)
        rows = cur.fetchall()
        return list(rows)


def exec_sql(sql: str, params: Tuple[Any, ...] = ()) -> None:
    with get_conn() as con:
        con.execute(sql, params)


def exec_many(sql: str, params_list: List[Tuple[Any, ...]]) -> None:
    with get_conn() as con:
        con.executemany(sql, params_list)


def to_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def from_json(s: Optional[str]) -> Any:
    if not s:
        return None
    return json.loads(s)
