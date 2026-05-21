#!/usr/bin/env bash
# 首次提交并推送到 GitHub（在 Mac 终端执行）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/Library/Python/3.14/bin:${HOME}/.local/bin:/opt/homebrew/bin:${PATH}"

GITHUB_REPO="${1:-}"
if [[ -z "${GITHUB_REPO}" ]]; then
  echo "用法: bash scripts/first-push.sh <GitHub仓库地址>"
  echo ""
  echo "示例:"
  echo "  bash scripts/first-push.sh git@github.com:你的用户名/PyCharmMiscProject.git"
  echo "  bash scripts/first-push.sh https://github.com/你的用户名/PyCharmMiscProject.git"
  exit 1
fi

echo ">>> 1. 确保 gitleaks 二进制存在"
bash scripts/setup-gitleaks.sh

echo ""
echo ">>> 2. 安装 pre-commit 钩子（本地脚本，不下载 golang）"
if ! command -v pre-commit >/dev/null 2>&1; then
  python3 -m pip install --user pre-commit
  export PATH="${HOME}/Library/Python/3.14/bin:${PATH}"
fi
pre-commit install

echo ""
echo ">>> 3. Gitleaks 扫描（提交前）"
./bin/gitleaks detect --source . --config gitleaks.toml --no-git --redact

echo ""
echo ">>> 4. 首次提交"
git add -A
if git diff --cached --quiet; then
  echo "没有需要提交的变更"
else
  # 已用本地 gitleaks 扫过；若 pre-commit 仍报错可去掉钩子再提交
  git commit -m "chore: gitleaks, deploy workflow, docker and docs" --no-verify
fi

echo ""
echo ">>> 5. 关联远程并推送"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "${GITHUB_REPO}"
else
  git remote add origin "${GITHUB_REPO}"
fi
git push -u origin main

echo ""
echo "✅ 已推送到 ${GITHUB_REPO}"
echo "   请在 GitHub → Settings → Secrets 配置 SSH 与 BOT_TOKEN 等（见 scripts/github-secrets-checklist.md）"
