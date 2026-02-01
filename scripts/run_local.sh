#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../services/orchestrator"

# Create local data dirs under repo by default
export DATA_DIR="${DATA_DIR:-$(pwd)/../../data}"
export DB_PATH="${DB_PATH:-$DATA_DIR/workbench.db}"
export WORKSPACES_DIR="${WORKSPACES_DIR:-$DATA_DIR/workspaces}"
export ARTIFACTS_DIR="${ARTIFACTS_DIR:-$DATA_DIR/artifacts}"
export LOGS_DIR="${LOGS_DIR:-$DATA_DIR/logs}"
export SKILLS_DIR="${SKILLS_DIR:-$(pwd)/../../skills}"

# Provider config (set these in your shell or a .env file)
: "${OPENAI_BASE_URL:=https://api.openai.com/v1}"
: "${OPENAI_API_KEY:=CHANGE_ME}"
: "${UI_ADMIN_TOKEN:=admin}"

# Optional: install deps & browsers
# python -m pip install -r requirements.txt
# python -m playwright install chromium

python -m uvicorn app.main:app --host 0.0.0.0 --port 8787
