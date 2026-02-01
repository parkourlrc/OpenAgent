from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, List

from ..config import settings
from .base import ToolContext, ToolSpec, register


def _normalize_rel_path(rel_path: str) -> str:
    s = str(rel_path or "").strip()
    if not s:
        return "."
    # Many prompts refer to a conceptual "workspace/" root. Interpret it as the actual workspace root.
    # This prevents accidental creation of a nested "workspace/" folder.
    s2 = s.replace("\\", "/")
    while s2.startswith("./"):
        s2 = s2[2:]
    if s2.startswith("workspace/"):
        s2 = s2[len("workspace/") :]
    return s2 or "."


def _resolve(ctx: ToolContext, rel_path: str) -> Path:
    rel = _normalize_rel_path(rel_path)
    p = (ctx.workspace_root / rel).expanduser()
    try:
        rp = p.resolve()
    except FileNotFoundError:
        # resolve parent, then append name
        rp = p.parent.resolve() / p.name
    if not settings.fs_allow_outside_workspace:
        ws = ctx.workspace_root.resolve()
        if not str(rp).startswith(str(ws)):
            raise ValueError(f"path escapes workspace: {rel_path}")
    return rp


def fs_list(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    rel = args.get("path", ".")
    recursive = bool(args.get("recursive", False))
    include_hidden = bool(args.get("include_hidden", False))
    p = _resolve(ctx, rel)
    if not p.exists():
        raise FileNotFoundError(str(p))
    items = []
    if p.is_dir():
        if recursive:
            for root, dirs, files in os.walk(p):
                if not include_hidden:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    files = [f for f in files if not f.startswith(".")]
                for d in dirs:
                    fp = Path(root) / d
                    items.append({"path": str(fp.relative_to(ctx.workspace_root)), "type": "dir"})
                for f in files:
                    fp = Path(root) / f
                    items.append({"path": str(fp.relative_to(ctx.workspace_root)), "type": "file", "size": fp.stat().st_size})
        else:
            for child in p.iterdir():
                if not include_hidden and child.name.startswith("."):
                    continue
                items.append({"path": str(child.relative_to(ctx.workspace_root)), "type": "dir" if child.is_dir() else "file", "size": child.stat().st_size if child.is_file() else None})
    else:
        items.append({"path": str(p.relative_to(ctx.workspace_root)), "type": "file", "size": p.stat().st_size})
    return {"ok": True, "items": items}


def fs_read_text(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    rel = args["path"]
    max_bytes = int(args.get("max_bytes", 200_000))
    p = _resolve(ctx, rel)
    data = p.read_bytes()
    truncated = False
    if len(data) > max_bytes:
        data = data[:max_bytes]
        truncated = True
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    return {"ok": True, "path": rel, "truncated": truncated, "content": text}


def fs_write_text(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    rel = args["path"]
    content = args.get("content", "")
    append = bool(args.get("append", False))
    p = _resolve(ctx, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with p.open(mode, encoding="utf-8") as f:
        f.write(content)
    return {"ok": True, "path": rel, "bytes": len(content.encode("utf-8"))}


def fs_mkdir(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    rel = args["path"]
    exist_ok = bool(args.get("exist_ok", True))
    p = _resolve(ctx, rel)
    p.mkdir(parents=True, exist_ok=exist_ok)
    return {"ok": True, "path": rel}


def fs_move(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    src = _resolve(ctx, args["src"])
    dst = _resolve(ctx, args["dst"])
    overwrite = bool(args.get("overwrite", False))
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if overwrite:
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
        else:
            raise FileExistsError(str(dst))
    shutil.move(str(src), str(dst))
    return {"ok": True, "src": args["src"], "dst": args["dst"]}


def fs_delete(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    p = _resolve(ctx, args["path"])
    recursive = bool(args.get("recursive", False))
    if not p.exists():
        return {"ok": True, "deleted": False, "path": args["path"]}
    if p.is_dir():
        if not recursive:
            p.rmdir()
        else:
            shutil.rmtree(p)
    else:
        p.unlink()
    return {"ok": True, "deleted": True, "path": args["path"]}


def fs_stat(ctx: ToolContext, args: Dict[str, Any]) -> Dict[str, Any]:
    p = _resolve(ctx, args["path"])
    st = p.stat()
    return {"ok": True, "path": args["path"], "is_dir": p.is_dir(), "size": st.st_size, "mtime": st.st_mtime}


def register_filesystem_tools() -> None:
    register(
        ToolSpec(
            name="filesystem.list",
            description="List files/folders under the workspace.",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "relative path under workspace"},
                    "recursive": {"type": "boolean", "default": False},
                    "include_hidden": {"type": "boolean", "default": False},
                },
                "required": [],
            },
            func=fs_list,
            risky=False,
        )
    )
    register(
        ToolSpec(
            name="filesystem.read_text",
            description="Read a UTF-8 text file (truncates large files).",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_bytes": {"type": "integer", "default": 200000},
                },
                "required": ["path"],
            },
            func=fs_read_text,
            risky=False,
        )
    )
    register(
        ToolSpec(
            name="filesystem.write_text",
            description="Write (or append) a UTF-8 text file under workspace.",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "append": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
            },
            func=fs_write_text,
            risky=True,
        )
    )
    register(
        ToolSpec(
            name="filesystem.mkdir",
            description="Create a directory under workspace.",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "exist_ok": {"type": "boolean", "default": True},
                },
                "required": ["path"],
            },
            func=fs_mkdir,
            risky=True,
        )
    )
    register(
        ToolSpec(
            name="filesystem.move",
            description="Move/rename a file or folder within workspace.",
            json_schema={
                "type": "object",
                "properties": {
                    "src": {"type": "string"},
                    "dst": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["src", "dst"],
            },
            func=fs_move,
            risky=True,
        )
    )
    register(
        ToolSpec(
            name="filesystem.delete",
            description="Delete a file or folder under workspace.",
            json_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "recursive": {"type": "boolean", "default": False},
                },
                "required": ["path"],
            },
            func=fs_delete,
            risky=True,
        )
    )
    register(
        ToolSpec(
            name="filesystem.stat",
            description="Get file/folder metadata.",
            json_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            func=fs_stat,
            risky=False,
        )
    )
