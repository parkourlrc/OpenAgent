from __future__ import annotations

import re
from typing import Dict, Mapping, Optional

from fastapi import Request

SUPPORTED_LANGS = ("en", "zh")
DEFAULT_LANG = "en"


_TRANSLATIONS: Mapping[str, Mapping[str, str]] = {
    "en": {
        "app.name": "OpenAgent Workbench",
        "common.name": "Name",
        "common.path": "Path",
        "common.created": "Created",
        "nav.runs": "Runs",
        "nav.workspaces": "Workspaces",
        "nav.skills": "Skills",
        "nav.schedules": "Schedules",
        "lang.en": "EN",
        "lang.zh": "中文",
        "footer.admin_token": "Admin token required for create/approve actions. Current token:",
        "runs.title": "Runs",
        "runs.create": "Create a new run",
        "runs.workspace": "Workspace",
        "runs.workspace.help": "A workspace is a local folder where the agent reads/writes files and saves reports.",
        "runs.skill": "Skill",
        "runs.skill.help": "A skill is a predefined workflow (planner/executor/critic + allowed tools).",
        "runs.mode": "Mode",
        "runs.mode.default": "(skill default)",
        "runs.goal": "Goal",
        "runs.goal.placeholder": "What do you want the agent to do?",
        "runs.start": "Start Run",
        "runs.recent": "Recent runs",
        "runs.tip": "Tip: manage workspaces and skills in the tabs above.",
        "common.updated": "Updated",
        "common.id": "ID",
        "common.status": "Status",
        "common.mode": "Mode",
        "common.goal": "Goal",
        "workspaces.title": "Workspaces",
        "workspaces.create": "Create workspace",
        "workspaces.name": "Name",
        "workspaces.path": "Path (optional, default under DATA_DIR/workspaces)",
        "workspaces.create_btn": "Create",
        "workspaces.all": "All workspaces",
        "workspaces.created": "Created",
        "skills.title": "Skills",
        "skills.import": "Import skill from YAML",
        "skills.yaml_path": "YAML path",
        "skills.import_btn": "Import",
        "skills.all": "All skills",
        "skills.default_mode": "Default mode",
        "skills.allowed_tools": "Allowed tools",
        "skills.description": "Description",
        "skills.tools": "Available tools",
        "skills.all_tools": "(all tools)",
        "task.title": "Run {id}",
        "task.status": "Status",
        "task.workspace": "Workspace",
        "task.skill": "Skill",
        "task.updated": "Updated",
        "task.report": "Report",
        "task.error": "Error",
        "task.plan": "Plan",
        "task.no_plan": "No plan yet.",
        "task.steps": "Steps",
        "task.requires_approval": "Requires approval",
        "task.yes": "yes",
        "task.no": "no",
        "task.args_result_error": "Args / Result / Error",
        "task.args": "Args",
        "task.result": "Result",
        "task.error_label": "Error",
        "task.decision": "Decision",
        "task.reason": "Reason",
        "task.submit": "Submit",
        "task.no_autorefresh": "This page does not auto-refresh. Refresh to see updates.",
        "task.approvals": "Approvals",
        "approvals.requested": "Requested",
        "approvals.step": "Step",
        "approvals.decision": "Decision",
        "approvals.reason": "Reason",
        "approvals.approve": "approve",
        "approvals.reject": "reject",
        "schedules.title": "Schedules",
        "schedules.create": "Create schedule",
        "schedules.cron": "Cron expr (min hour dom mon dow)",
        "schedules.workspace": "Workspace",
        "schedules.skill": "Skill",
        "schedules.mode": "Mode",
        "schedules.goal_opt": "Goal (optional, stored in payload)",
        "schedules.create_btn": "Create schedule",
        "schedules.hint": "Uses server-side scheduler tick loop. Cron resolution is per-minute.",
        "schedules.all": "All schedules",
        "schedules.enabled": "Enabled",
        "schedules.next": "Next",
        "schedules.last": "Last",
    },
    "zh": {
        "app.name": "OpenAgent Workbench",
        "common.name": "名称",
        "common.path": "路径",
        "common.created": "创建时间",
        "nav.runs": "运行",
        "nav.workspaces": "工作区",
        "nav.skills": "技能",
        "nav.schedules": "定时任务",
        "lang.en": "EN",
        "lang.zh": "中文",
        "footer.admin_token": "创建/审批操作需要管理员 Token。当前 Token：",
        "runs.title": "运行",
        "runs.create": "创建一次运行",
        "runs.workspace": "工作区",
        "runs.workspace.help": "工作区是一个本地文件夹：Agent 会在这里读写文件、并保存报告产物。",
        "runs.skill": "技能",
        "runs.skill.help": "技能是预置工作流（规划/执行/审阅 + 可用工具集合）。",
        "runs.mode": "模式",
        "runs.mode.default": "（技能默认）",
        "runs.goal": "目标",
        "runs.goal.placeholder": "你希望 Agent 完成什么？",
        "runs.start": "开始运行",
        "runs.recent": "最近运行",
        "runs.tip": "提示：你可以在上方的“工作区/技能”页管理这些内容。",
        "common.updated": "更新时间",
        "common.id": "ID",
        "common.status": "状态",
        "common.mode": "模式",
        "common.goal": "目标",
        "workspaces.title": "工作区",
        "workspaces.create": "创建工作区",
        "workspaces.name": "名称",
        "workspaces.path": "路径（可选，默认在 DATA_DIR/workspaces 下）",
        "workspaces.create_btn": "创建",
        "workspaces.all": "全部工作区",
        "workspaces.created": "创建时间",
        "skills.title": "技能",
        "skills.import": "从 YAML 导入技能",
        "skills.yaml_path": "YAML 路径",
        "skills.import_btn": "导入",
        "skills.all": "全部技能",
        "skills.default_mode": "默认模式",
        "skills.allowed_tools": "允许工具",
        "skills.description": "描述",
        "skills.tools": "可用工具",
        "skills.all_tools": "（全部工具）",
        "task.title": "运行 {id}",
        "task.status": "状态",
        "task.workspace": "工作区",
        "task.skill": "技能",
        "task.updated": "更新时间",
        "task.report": "报告",
        "task.error": "错误",
        "task.plan": "计划",
        "task.no_plan": "暂无计划。",
        "task.steps": "步骤",
        "task.requires_approval": "需要审批",
        "task.yes": "是",
        "task.no": "否",
        "task.args_result_error": "参数 / 结果 / 错误",
        "task.args": "参数",
        "task.result": "结果",
        "task.error_label": "错误",
        "task.decision": "决策",
        "task.reason": "原因",
        "task.submit": "提交",
        "task.no_autorefresh": "此页面不会自动刷新，需要手动刷新查看最新进度。",
        "task.approvals": "审批记录",
        "approvals.requested": "申请时间",
        "approvals.step": "步骤",
        "approvals.decision": "决策",
        "approvals.reason": "原因",
        "approvals.approve": "通过",
        "approvals.reject": "拒绝",
        "schedules.title": "定时任务",
        "schedules.create": "创建定时任务",
        "schedules.cron": "Cron 表达式（分 时 日 月 周）",
        "schedules.workspace": "工作区",
        "schedules.skill": "技能",
        "schedules.mode": "模式",
        "schedules.goal_opt": "目标（可选，存储在 payload 中）",
        "schedules.create_btn": "创建",
        "schedules.hint": "使用服务端调度循环，Cron 分辨率为 1 分钟。",
        "schedules.all": "全部定时任务",
        "schedules.enabled": "启用",
        "schedules.next": "下次",
        "schedules.last": "上次",
    },
}

_TRANSLATIONS["en"].setdefault("task.plan_execution", "Plan + Execution")
_TRANSLATIONS["zh"].setdefault("task.plan_execution", "规划 + 执行过程")
_TRANSLATIONS["en"].setdefault("chat.title", "Conversation")
_TRANSLATIONS["zh"].setdefault("chat.title", "对话")
_TRANSLATIONS["en"].setdefault("chat.placeholder", "Add a follow-up instruction…")
_TRANSLATIONS["zh"].setdefault("chat.placeholder", "继续输入指令…")
_TRANSLATIONS["en"].setdefault("chat.send", "Send")
_TRANSLATIONS["zh"].setdefault("chat.send", "发送")
_TRANSLATIONS["en"].setdefault("chat.sending", "Sending…")
_TRANSLATIONS["zh"].setdefault("chat.sending", "发送中…")
_TRANSLATIONS["en"].setdefault("chat.empty", "No messages yet.")
_TRANSLATIONS["zh"].setdefault("chat.empty", "暂无对话内容。")
_TRANSLATIONS["en"].setdefault("history.delete_confirm", "Delete this task? This cannot be undone.")
_TRANSLATIONS["zh"].setdefault("history.delete_confirm", "确定删除这条历史记录吗？此操作不可恢复。")
_TRANSLATIONS["en"].setdefault("history.delete_failed", "Delete failed.")
_TRANSLATIONS["zh"].setdefault("history.delete_failed", "删除失败。")
_TRANSLATIONS["en"].setdefault("common.copy", "Copy")
_TRANSLATIONS["zh"].setdefault("common.copy", "复制")
_TRANSLATIONS["en"].setdefault("common.copied", "Copied")
_TRANSLATIONS["zh"].setdefault("common.copied", "已复制")
_TRANSLATIONS["en"].setdefault("common.copy_failed", "Copy failed")
_TRANSLATIONS["zh"].setdefault("common.copy_failed", "复制失败")
_TRANSLATIONS["en"].setdefault("task.cancel", "Stop")
_TRANSLATIONS["zh"].setdefault("task.cancel", "中止")
_TRANSLATIONS["en"].setdefault("task.canceling", "Canceling…")
_TRANSLATIONS["zh"].setdefault("task.canceling", "正在中止…")


_TRANSLATIONS["en"].setdefault("timeline.title", "Timeline")
_TRANSLATIONS["zh"].setdefault("timeline.title", "时间线")
_TRANSLATIONS["en"].setdefault("timeline.live", "Live updates via SSE")
_TRANSLATIONS["zh"].setdefault("timeline.live", "SSE 实时更新")
_TRANSLATIONS["en"].setdefault("approvals.required", "Approval Required")
_TRANSLATIONS["zh"].setdefault("approvals.required", "需要确认")
_TRANSLATIONS["en"].setdefault("task.details_debug", "Details / Debug")
_TRANSLATIONS["zh"].setdefault("task.details_debug", "详情 / Debug")

_TRANSLATIONS["en"].setdefault("sidebar.toggle", "Toggle sidebar")
_TRANSLATIONS["zh"].setdefault("sidebar.toggle", "切换侧栏")
_TRANSLATIONS["en"].setdefault("sidebar.preview", "Preview")
_TRANSLATIONS["zh"].setdefault("sidebar.preview", "预览")
_TRANSLATIONS["en"].setdefault("sidebar.no_output", "No output yet.")
_TRANSLATIONS["zh"].setdefault("sidebar.no_output", "还没有输出。")
_TRANSLATIONS["en"].setdefault("sidebar.artifacts", "Files / Artifacts")
_TRANSLATIONS["zh"].setdefault("sidebar.artifacts", "文件 / 产物")
_TRANSLATIONS["en"].setdefault("sidebar.no_artifacts", "No artifacts.")
_TRANSLATIONS["zh"].setdefault("sidebar.no_artifacts", "暂无产物。")

_TRANSLATIONS["en"].setdefault("common.sources", "Sources")
_TRANSLATIONS["zh"].setdefault("common.sources", "来源")
_TRANSLATIONS["en"].setdefault("common.loading", "Loading…")
_TRANSLATIONS["zh"].setdefault("common.loading", "加载中…")

_TRANSLATIONS["en"].setdefault("citations.unverified", "Unverified source.")
_TRANSLATIONS["zh"].setdefault("citations.unverified", "未验证来源。")
_TRANSLATIONS["en"].setdefault("citations.no_match", "Unverified source (no matching evidence chunk).")
_TRANSLATIONS["zh"].setdefault("citations.no_match", "未验证来源（未找到对应证据片段）。")
_TRANSLATIONS["en"].setdefault("citations.chunk_not_found", "Evidence chunk not found.")
_TRANSLATIONS["zh"].setdefault("citations.chunk_not_found", "未找到证据片段。")
_TRANSLATIONS["en"].setdefault("citations.warnings", "Citation warnings")
_TRANSLATIONS["zh"].setdefault("citations.warnings", "引用提示")
_TRANSLATIONS["en"].setdefault("citations.warn_unverified_prefix", "Unverified references:")
_TRANSLATIONS["zh"].setdefault("citations.warn_unverified_prefix", "未验证的引用：")
_TRANSLATIONS["en"].setdefault("citations.warn_reasons_prefix", "Citation check:")
_TRANSLATIONS["zh"].setdefault("citations.warn_reasons_prefix", "引用校验：")


def normalize_lang(raw: Optional[str]) -> str:
    if not raw:
        return DEFAULT_LANG
    val = raw.strip().lower()
    if val in SUPPORTED_LANGS:
        return val
    if val.startswith("zh"):
        return "zh"
    if val.startswith("en"):
        return "en"
    return DEFAULT_LANG


def detect_lang(request: Request) -> str:
    q = request.query_params.get("lang")
    if q:
        return normalize_lang(q)
    c = request.cookies.get("lang")
    if c:
        return normalize_lang(c)
    accept = request.headers.get("accept-language", "")
    m = re.match(r"\s*([a-zA-Z-]+)", accept)
    return normalize_lang(m.group(1) if m else None)


def t(lang: str, key: str, **kwargs: object) -> str:
    table: Mapping[str, str] = _TRANSLATIONS.get(lang, _TRANSLATIONS[DEFAULT_LANG])
    s = table.get(key) or _TRANSLATIONS[DEFAULT_LANG].get(key) or key
    try:
        return s.format(**kwargs)
    except Exception:
        return s


def with_lang(request: Request, lang: str) -> str:
    return str(request.url.include_query_params(lang=normalize_lang(lang)))
