#!/usr/bin/env bash
# 在 Mac 终端执行：同步代码到 VPS + 重启服务（不依赖 GitHub Actions）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
KEY="${HOME}/.ssh/id_ed25519_vps_deploy"
HOST="root@188.116.22.203"
DEPLOY="/opt/bot"

echo ">>> 1. 测试 SSH"
ssh -i "${KEY}" -o StrictHostKeyChecking=accept-new "${HOST}" echo OK

echo ">>> 2. rsync 代码到 VPS"
rsync -avz --delete \
  -e "ssh -i ${KEY}" \
  --exclude '.venv' --exclude '.git' --exclude '.env' \
  --exclude 'data/bot.db' --exclude '.idea' --exclude '__pycache__' \
  --exclude 'bin/gitleaks' \
  "${ROOT}/" "${HOST}:${DEPLOY}/"

echo ">>> 3. 服务器安装依赖并重启"
ssh -i "${KEY}" "${HOST}" bash -s <<'REMOTE'
set -euo pipefail
cd /opt/bot
test -d .venv || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
.venv/bin/pip install -q sherlock-project 2>/dev/null || true
if systemctl list-unit-files 2>/dev/null | grep -q telegram-bot.service; then
  systemctl restart telegram-bot telegram-web
  systemctl is-active telegram-bot telegram-web
else
  echo "未找到 systemd，请手动启动 app.py / web.py"
fi
REMOTE

echo "✅ VPS 部署完成"
