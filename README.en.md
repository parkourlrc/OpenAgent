# OpenAgent Workbench

[简体中文](README.md) | English

OpenAgent Workbench is a **desktop-first Agent Workbench**:

- Double-click to run (Windows `.exe` / installer), with an embedded WebView (no external browser needed)
- Works with **any OpenAI-compatible API** (OpenAI / LiteLLM / other gateways)
- Supports **streaming**, task timeline (plan/execution), approvals, Skills and MCP
- Built-in artifact/file preview (PPT/PDF/DOCX/Markdown/Images/Audio/Video/Code/Excel)

---

## Quick Start (Windows)

### Option A: Install (recommended)

- Run the latest installer in `dist-installer/`:
  - `OpenAgentWorkbench-Setup-*.exe`

### Option B: Portable `.exe`

- Run:
  - `dist-desktop/OpenAgentWorkbench.exe`

---

## Configure model (required)

Open **Settings → Model Config**, set:

- `base_url` (your OpenAI-compatible gateway)
- `api_key`
- `model` name

Then go back to Home and run a task.

---

## Build (for developers)

### Build desktop app

```powershell
./scripts/build_desktop.ps1
```

Outputs:

- `dist-desktop/OpenAgentWorkbench.exe`

### Build installer

```powershell
./scripts/build_installer.ps1
```

Outputs:

- `dist-installer/OpenAgentWorkbench-Setup-*.exe`

---

## Contact

加我联系方式，拉您进用户群呐：

Telegram:@ryonliu

如需稳定便宜的API，查看：https://0-0.pro/

