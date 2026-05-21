#!/usr/bin/env bash
# 从本地 deploy-secrets.local.env 批量写入 GitHub Actions Secrets
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENV_FILE="${ROOT}/scripts/deploy-secrets.local.env"
REPO="ziziw335/PyCharmMiscProject"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "请先创建并填写密钥文件："
  echo "  cp scripts/deploy-secrets.local.env.example scripts/deploy-secrets.local.env"
  echo "  open -e scripts/deploy-secrets.local.env"
  exit 1
fi

# shellcheck disable=SC1090
source "${ENV_FILE}"

if ! command -v gh >/dev/null 2>&1; then
  echo ">>> 安装 GitHub CLI..."
  if command -v brew >/dev/null 2>&1; then
    brew install gh
  else
    echo "请先安装 gh: https://cli.github.com/"
    exit 1
  fi
fi

if ! gh auth status >/dev/null 2>&1; then
  echo ">>> 登录 GitHub（浏览器会打开）..."
  gh auth login -h github.com -p ssh -w
fi

set_secret() {
  local name="$1"
  local value="$2"
  if [[ -z "${value}" ]] || [[ "${value}" == *"在这里"* ]] || [[ "${value}" == *"粘贴"* ]]; then
    echo "  跳过 ${name}（未填写）"
    return
  fi
  echo "  设置 ${name} ..."
  printf '%s' "${value}" | gh secret set "${name}" --repo "${REPO}"
}

echo ">>> 上传到 ${REPO}"

if [[ -z "${SSH_PRIVATE_KEY_FILE:-}" ]] || [[ ! -f "${SSH_PRIVATE_KEY_FILE}" ]]; then
  echo "ERROR: 请设置 SSH_PRIVATE_KEY_FILE 并先运行 bash scripts/setup-vps-deploy-key.sh"
  exit 1
fi
SSH_PRIVATE_KEY="$(cat "${SSH_PRIVATE_KEY_FILE}")"

set_secret SSH_HOST "${SSH_HOST:-}"
set_secret SSH_USER "${SSH_USER:-}"
set_secret SSH_PRIVATE_KEY "${SSH_PRIVATE_KEY}"
set_secret BOT_TOKEN "${BOT_TOKEN:-}"
set_secret WEB_TOKEN "${WEB_TOKEN:-}"
set_secret DEPLOY_PATH "${DEPLOY_PATH:-/opt/bot}"
set_secret DATABASE_URL "${DATABASE_URL:-}"
set_secret PUBLIC_WEB_BASE_URL "${PUBLIC_WEB_BASE_URL:-}"
set_secret BOT_OWNER_ID "${BOT_OWNER_ID:-}"
set_secret SUPER_ADMIN_ID "${SUPER_ADMIN_ID:-}"
set_secret DATABASE_PASSWORD "${DATABASE_PASSWORD:-}"
set_secret API_TOKEN "${API_TOKEN:-}"
set_secret TELEGRAM_SECRET_TOKEN "${TELEGRAM_SECRET_TOKEN:-}"
set_secret HIBP_API_KEY "${HIBP_API_KEY:-}"

echo ""
echo "✅ 已写入 GitHub Secrets"
echo "   查看: https://github.com/${REPO}/settings/secrets/actions"
echo "   然后到 Actions 页 Re-run Deploy to Production"
