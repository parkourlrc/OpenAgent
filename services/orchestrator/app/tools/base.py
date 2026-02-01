from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import settings

ToolFunc = Callable[["ToolContext", Dict[str, Any]], Dict[str, Any]]


@dataclass
class ToolContext:
    workspace_root: Path
    task_id: str
    step_id: str


@dataclass
class ToolSpec:
    name: str
    description: str
    json_schema: Dict[str, Any]
    func: ToolFunc
    risky: bool = False  # requires approval by default


_REGISTRY: Dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> None:
    if spec.name in _REGISTRY:
        raise ValueError(f"tool already registered: {spec.name}")
    _REGISTRY[spec.name] = spec


def get_tool(name: str) -> ToolSpec:
    if name not in _REGISTRY:
        raise KeyError(f"unknown tool: {name}")
    return _REGISTRY[name]


def list_tools(allowed: Optional[List[str]] = None) -> List[ToolSpec]:
    if allowed is None:
        return list(_REGISTRY.values())
    out = []
    for name in allowed:
        if name in _REGISTRY:
            out.append(_REGISTRY[name])
    return out


def openai_tool_schema(spec: ToolSpec) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.json_schema,
        },
    }


def run_tool(ctx: ToolContext, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    spec = get_tool(name)
    return spec.func(ctx, args)
