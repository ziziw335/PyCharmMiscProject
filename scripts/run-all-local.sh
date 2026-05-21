#!/usr/bin/env bash
# 在 Mac 系统终端（非 Cursor 内置终端）执行，一次性跑通本地检查 + Git + pre-commit
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/Library/Python/3.14/bin:${HOME}/.local/bin:/opt/homebrew/bin:${PATH}"

echo "========== 1. Gitleaks =========="
bash scripts/setup-gitleaks.sh

echo ""
echo "========== 2. Git 仓库 =========="
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git init -b main 2>/dev/null; then
    git config user.email "${GIT_AUTHOR_EMAIL:-dev@local}"
    git config user.name "${GIT_AUTHOR_NAME:-dev}"
    echo "✓ git init 完成"
  else
    echo "⚠ git init 失败（Cursor 沙箱限制）→ 请在 Mac「终端」中执行: cd $ROOT && git init -b main"
  fi
else
  echo "✓ 已是 Git 仓库"
fi

echo ""
echo "========== 3. ci-write-env 自检 =========="
BOT_TOKEN=demo WEB_TOKEN=demo DATABASE_PASSWORD=demo API_TOKEN=demo \
  bash scripts/ci-write-env.sh /tmp/gitleaks-env-test.$$
cat /tmp/gitleaks-env-test.$$ | sed 's/=.*/=***/'
rm -f /tmp/gitleaks-env-test.$$

echo ""
echo "========== 4. pre-commit（若可用）=========="
if command -v pre-commit >/dev/null 2>&1 && pre-commit --version >/dev/null 2>&1; then
  pre-commit install || true
  pre-commit run gitleaks --all-files || true
else
  echo "跳过：请在终端执行  python3 -m pip install --user pre-commit"
fi

echo ""
echo "========== 5. Docker（可选）=========="
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  docker compose config -q
  echo "docker compose config OK"
else
  echo "跳过：本机未安装 Docker，生产机可用 systemd 部署（见 scripts/ci-deploy.sh）"
fi

echo ""
echo "========== 6. 工作流 YAML =========="
python3 -c "
import yaml
from pathlib import Path
for p in Path('.github/workflows').glob('*.yml'):
    yaml.safe_load(p.read_text())
    print('  OK', p.name)
"

echo ""
echo "✅ 本地检查完成。"
echo ""
echo "下一步（推送到 GitHub 自动部署）："
echo "  1. 在 GitHub → Settings → Secrets 配置 SSH_HOST / SSH_USER / SSH_PRIVATE_KEY / BOT_TOKEN / WEB_TOKEN 等"
echo "  2. git add -A && git commit -m 'chore: security and deploy' && git push origin main"
echo ""
echo "或手动部署到 VPS："
echo "  bash deploy_to_vps.sh"
echo "  # 或在服务器: DEPLOY_PATH=/opt/bot bash scripts/ci-deploy.sh"
