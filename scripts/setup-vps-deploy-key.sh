#!/usr/bin/env bash
# 生成专门给 GitHub Actions 登录 VPS 用的 SSH 密钥
set -euo pipefail

KEY="${HOME}/.ssh/id_ed25519_vps_deploy"
PUB="${KEY}.pub"

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"

if [[ ! -f "${PUB}" ]]; then
  echo ">>> 生成 VPS 部署密钥..."
  ssh-keygen -t ed25519 -f "${KEY}" -N "" -C "github-actions-deploy"
fi

echo ""
echo "=========================================="
echo "1) 把下面公钥加到 VPS（复制整行）："
echo "   ssh root@188.116.22.203"
echo "   echo '公钥' >> ~/.ssh/authorized_keys"
echo "=========================================="
cat "${PUB}"
echo "=========================================="
echo ""
echo "2) 在 scripts/deploy-secrets.local.env 里设置："
echo "   SSH_PRIVATE_KEY_FILE=${KEY}"
echo ""
echo "3) 测试: ssh -i ${KEY} root@188.116.22.203 echo OK"
