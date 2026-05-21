#!/usr/bin/env bash
# 在服务器上由 CI 调用：根据已注入的环境变量生成 .env（不打印密钥）
set -euo pipefail

TARGET="${1:-.env}"
umask 077

# 仅写入非空变量；占位符便于运维核对项是否齐全
write_kv() {
  local key="$1"
  local val="${!key:-}"
  if [[ -n "${val}" ]]; then
    printf '%s=%s\n' "${key}" "${val}"
  fi
}

{
  write_kv BOT_TOKEN
  write_kv WEB_TOKEN
  write_kv DATABASE_PASSWORD
  write_kv API_TOKEN
  write_kv DATABASE_URL
  write_kv PGSSLMODE
  write_kv BOT_OWNER_ID
  write_kv SUPER_ADMIN_ID
  write_kv TELEGRAM_SECRET_TOKEN
  write_kv GROUP_VIEW_SECRET
  write_kv PUBLIC_WEB_BASE_URL
  write_kv PAYMENT_ADDRESS
  write_kv HIBP_API_KEY
  write_kv BANK_VERIFY_APPCODE
  write_kv TRONGRID_API_KEY
  write_kv PORT
  write_kv WEB_PORT
  write_kv BOT_BASE_URL
  write_kv WEB_BASE_URL
} > "${TARGET}.tmp"

mv "${TARGET}.tmp" "${TARGET}"
chmod 600 "${TARGET}"
echo "Wrote ${TARGET} ($(wc -l < "${TARGET}") keys, values hidden)"
