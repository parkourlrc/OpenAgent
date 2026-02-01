# OpenAgent Workbench

简体中文 | [English](README.en.md)

一个 **桌面优先（Desktop-first）** 的 Agent Workbench：双击即可使用（Windows `.exe` / 安装包），内置 WebView（不需要打开外部浏览器），并支持 **任意 OpenAI-compatible API**（OpenAI / LiteLLM / 其他网关）。

你可以把它理解为：一个可运行、可扩展的 “Agent 2.0 Workbench”，把 **对话、规划/执行过程、审批、技能/插件、产物预览** 打通成一个“真正的桌面产品体验”。

---

## 快速开始（Windows）

### 方式 A：安装包（推荐）

运行 `dist-installer/` 下最新的：

- `OpenAgentWorkbench-Setup-*.exe`

### 方式 B：绿色版 `.exe`

直接运行：

- `dist-desktop/OpenAgentWorkbench.exe`

---

## 模型配置（必填）

打开 **设置 → 模型配置**，填写：

- `base_url`（你的 OpenAI-compatible 网关）
- `api_key`
- `model`（模型名）

保存后回到首页输入任务 → 开始运行。

---

## 开发者：构建

### 构建桌面应用

```powershell
./scripts/build_desktop.ps1
```

产物：

- `dist-desktop/OpenAgentWorkbench.exe`

### 构建安装包

```powershell
./scripts/build_installer.ps1
```

产物：

- `dist-installer/OpenAgentWorkbench-Setup-*.exe`


---

## 联系方式

加我联系方式，拉您进用户群呐：

Telegram:@ryonliu

如需稳定便宜的API，查看：https://0-0.pro/
