#!/bin/bash
# 群成员打开账单网页：必须先运行 PyCharm「运行 Web后台」，再执行本脚本
set -e
cd "$(dirname "$0")"

if ! lsof -i :8081 -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "❌ 8081 没有 Web 后台。请先在 PyCharm 运行「运行 Web后台」(web.py)"
  exit 1
fi

pkill -f "ngrok http" 2>/dev/null || true
sleep 1
echo "启动 ngrok → 127.0.0.1:8081 （不要再用 80 端口）"
exec ngrok http 8081
