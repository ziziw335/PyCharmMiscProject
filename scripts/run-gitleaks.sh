#!/usr/bin/env bash
# pre-commit / 本地手动调用：优先扫 staged，否则全目录
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x bin/gitleaks ]]; then
  bash "${ROOT}/scripts/setup-gitleaks.sh" 2>/dev/null || true
fi
G="${ROOT}/bin/gitleaks"
CFG="${ROOT}/gitleaks.toml"

if git rev-parse --git-dir >/dev/null 2>&1; then
  exec "${G}" git --pre-commit --config "${CFG}" --staged --verbose --redact
else
  exec "${G}" detect --source . --config "${CFG}" --no-git --verbose --redact
fi
