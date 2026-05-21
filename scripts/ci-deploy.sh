#!/usr/bin/env bash
# 在服务器上执行：写 .env → 更新代码 → Docker 或 systemd 上线
set -euo pipefail

APP_DIR="${DEPLOY_PATH:-/opt/bot}"
cd "${APP_DIR}"

echo ">>> [1/5] 写入 .env"
bash scripts/ci-write-env.sh .env

echo ">>> [2/5] 更新代码"
if [[ -d .git ]]; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
  if [[ -n "${GIT_DEPLOY_TOKEN:-}" ]]; then
    ORIGIN="$(git remote get-url origin)"
    case "${ORIGIN}" in
      https://github.com/*)
        AUTH_ORIGIN="https://x-access-token:${GIT_DEPLOY_TOKEN}@${ORIGIN#https://}"
        git pull --ff-only "${AUTH_ORIGIN}" "${BRANCH}"
        ;;
      *)
        git fetch origin
        git pull --ff-only origin "${BRANCH}" \
          || git pull --ff-only origin main \
          || git pull --ff-only origin master
        ;;
    esac
  else
    git fetch origin
    git pull --ff-only origin "${BRANCH}" \
      || git pull --ff-only origin main \
      || git pull --ff-only origin master
  fi
else
  echo "  未检测到 .git，跳过 git pull（可用 deploy_to_vps.sh rsync 同步）"
fi

echo ">>> [3/5] 选择部署方式"
# 优先 systemd（当前 VPS 生产环境），避免无 Docker 时误走 compose
if systemctl list-unit-files 2>/dev/null | grep -qE 'telegram-bot\.service'; then
  echo ">>> [4/5] systemd 部署（无 Docker 时使用）"
  test -d .venv || python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
  .venv/bin/pip install -q sherlock-project 2>/dev/null || true
  systemctl restart telegram-bot telegram-web
  echo ">>> [5/5] 服务状态"
  systemctl is-active telegram-bot telegram-web
  systemctl status telegram-bot telegram-web --no-pager | head -20
elif command -v docker >/dev/null 2>&1 && [[ -f docker-compose.yml ]]; then
  if docker compose version >/dev/null 2>&1; then DC="docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
  else DC=""; fi
  if [[ -n "${DC}" ]]; then
    echo ">>> [4/5] Docker Compose 部署"
    ${DC} up -d --build --remove-orphans
    ${DC} ps
  fi
else
  echo "ERROR: 未找到 telegram-bot.service 也未找到 Docker"
  exit 1
fi

echo "Deploy OK at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
