from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any, Dict, List, Literal, Optional


TaskStatus = Literal["queued", "planning", "running", "waiting_approval", "failed", "succeeded", "canceled"]
StepStatus = Literal["pending", "running", "waiting_approval", "failed", "succeeded"]
ApprovalStatus = Literal["pending", "approved", "rejected"]


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1)
    # If omitted, workspace will be created under WORKSPACES_DIR with a safe slug.
    path: Optional[str] = None


class Workspace(BaseModel):
    id: str
    name: str
    path: str
    created_at: str


class SkillImport(BaseModel):
    yaml_path: str


class SkillCreate(BaseModel):
    name: str
    description: Optional[str] = None
    system_prompt: str
    allowed_tools: List[str] = Field(default_factory=list)
    default_mode: Literal["fast", "pro"] = "fast"


class Skill(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    yaml_path: Optional[str] = None
    system_prompt: str
    allowed_tools: List[str]
    default_mode: Literal["fast", "pro"]
    created_at: str


class TaskCreate(BaseModel):
    workspace_id: str
    skill_id: str
    goal: str
    mode: Optional[Literal["fast", "pro"]] = None


class TaskContinue(BaseModel):
    message: str = Field(..., min_length=1)


class Step(BaseModel):
    id: str
    task_id: str
    idx: int
    name: str
    tool: str
    args: Dict[str, Any]
    status: StepStatus
    requires_approval: bool
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str


class Task(BaseModel):
    id: str
    workspace_id: str
    skill_id: str
    status: TaskStatus
    mode: Literal["fast", "pro"]
    goal: str
    plan: Optional[Dict[str, Any]] = None
    created_at: str
    updated_at: str
    current_step: int
    output_path: Optional[str] = None
    error: Optional[str] = None


class TaskDetail(BaseModel):
    task: Task
    steps: List[Step]
    approvals: List[Dict[str, Any]]


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: Optional[str] = None


class ScheduleCreate(BaseModel):
    name: str
    cron_expr: str
    workspace_id: str
    skill_id: str
    mode: Literal["fast", "pro"] = "fast"
    enabled: bool = True
    payload: Optional[Dict[str, Any]] = None


class Schedule(BaseModel):
    id: str
    name: str
    cron_expr: str
    workspace_id: str
    skill_id: str
    mode: Literal["fast", "pro"]
    enabled: bool
    payload: Optional[Dict[str, Any]] = None
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    created_at: str
    updated_at: str


class KBIngestRequest(BaseModel):
    workspace_id: str
    # optional: if omitted, all files under workspace are scanned
    paths: Optional[List[str]] = None
    # chunking
    chunk_size: int = 1200
    chunk_overlap: int = 200
    # embeddings model override
    embeddings_model: Optional[str] = None


class KBQueryRequest(BaseModel):
    workspace_id: str
    query: str
    top_k: int = 6
    embeddings_model: Optional[str] = None
