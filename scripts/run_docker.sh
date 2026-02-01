#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env found. Copy from .env.example:"
  echo "  cp .env.example .env"
  exit 1
fi

docker compose up --build
