#!/usr/bin/env bash
# 生成 SSH 密钥并提示添加到 GitHub（解决 Permission denied (publickey)）
set -euo pipefail

KEY="${HOME}/.ssh/id_ed25519_github"
PUB="${KEY}.pub"

mkdir -p "${HOME}/.ssh"
chmod 700 "${HOME}/.ssh"

if [[ ! -f "${PUB}" ]]; then
  echo ">>> 生成 SSH 密钥..."
  ssh-keygen -t ed25519 -C "github-$(whoami)@$(hostname -s)" -f "${KEY}" -N ""
  chmod 600 "${KEY}"
  chmod 644 "${PUB}"
else
  echo ">>> 已存在密钥: ${PUB}"
fi

# 让 GitHub 走这把钥匙
if [[ ! -f "${HOME}/.ssh/config" ]] || ! grep -q "Host github.com" "${HOME}/.ssh/config" 2>/dev/null; then
  cat >> "${HOME}/.ssh/config" <<EOF

Host github.com
  HostName github.com
  User git
  IdentityFile ${KEY}
  IdentitiesOnly yes
EOF
  chmod 600 "${HOME}/.ssh/config"
  echo ">>> 已写入 ~/.ssh/config"
fi

echo ""
echo "=========================================="
echo "请复制下面整段公钥，添加到 GitHub："
echo "  https://github.com/settings/keys"
echo "  点击 New SSH key → 粘贴 → Add SSH key"
echo "=========================================="
cat "${PUB}"
echo "=========================================="
echo ""
echo "添加完成后，在终端测试："
echo "  ssh -T git@github.com"
echo "  看到 Hi 你的用户名! 即成功"
echo ""
echo "然后推送："
echo "  cd $(cd "$(dirname "$0")/.." && pwd)"
echo "  git push -u origin main"
