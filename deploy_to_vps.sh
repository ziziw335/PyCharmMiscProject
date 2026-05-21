#!/bin/bash
# 上传本地全部代码变更到香港 VPS（不含 .env、数据库）
set -e
HOST="${VPS_HOST:-root@188.116.22.203}"
DIR="$(cd "$(dirname "$0")" && pwd)"

echo ">>> 上传到 $HOST:/opt/bot/"
cd "$DIR"

rsync -avz --progress \
  --exclude '.venv' \
  --exclude '.idea' \
  --exclude '__pycache__' \
  --exclude '.git' \
  --exclude '.env' \
  --exclude 'data/bot.db' \
  --exclude 'logs/' \
  ./ "$HOST:/opt/bot/"

echo ""
echo ">>> 安装/更新 Python 依赖（含 sherlock-project 查用户名）"
ssh "$HOST" 'cd /opt/bot && \
  test -d .venv || python3 -m venv .venv; \
  .venv/bin/pip install -q -r requirements.txt && \
  .venv/bin/pip install -q sherlock-project && \
  test -x .venv/bin/sherlock && echo "sherlock OK: $(.venv/bin/sherlock --version 2>/dev/null || echo installed)"'

echo ""
echo ">>> 重启 Bot 与 Web 后台"
ssh "$HOST" 'systemctl restart telegram-bot telegram-web && systemctl status telegram-bot telegram-web --no-pager | head -25'

echo ""
echo "✅ 完成。请在 Telegram 再试：查用户名"
