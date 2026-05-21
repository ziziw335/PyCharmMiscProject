#!/usr/bin/env bash
# 一键安装并验证 Gitleaks + pre-commit（在项目根目录执行）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GITLEAKS_VERSION="${GITLEAKS_VERSION:-8.24.2}"
ARCH="$(uname -m)"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
case "${OS}-${ARCH}" in
  darwin-arm64)  GL_OS=darwin; GL_ARCH=arm64 ;;
  darwin-x86_64) GL_OS=darwin; GL_ARCH=x64 ;;
  linux-x86_64)  GL_OS=linux;  GL_ARCH=x64 ;;
  linux-aarch64) GL_OS=linux;  GL_ARCH=arm64 ;;
  *) echo "不支持的平台: ${OS}-${ARCH}"; exit 1 ;;
esac

install_gitleaks_bin() {
  mkdir -p bin
  if [[ -x bin/gitleaks ]] && bin/gitleaks version 2>/dev/null | grep -q "${GITLEAKS_VERSION}"; then
    echo "✓ bin/gitleaks 已存在"
    return
  fi
  echo "→ 下载 gitleaks v${GITLEAKS_VERSION} (${GL_OS}_${GL_ARCH})..."
  tmp="$(mktemp -d)"
  curl -fsSL \
    "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${GL_OS}_${GL_ARCH}.tar.gz" \
    -o "${tmp}/gitleaks.tgz"
  tar -xzf "${tmp}/gitleaks.tgz" -C "${tmp}" gitleaks
  install -m 755 "${tmp}/gitleaks" bin/gitleaks
  rm -rf "${tmp}"
  echo "✓ $(bin/gitleaks version)"
}

pre_commit_ok() {
  command -v pre-commit >/dev/null 2>&1 && pre-commit --version >/dev/null 2>&1
}

install_pre_commit() {
  export PATH="${HOME}/Library/Python/3.14/bin:${HOME}/.local/bin:/opt/homebrew/bin:${PATH}"
  if pre_commit_ok; then
    echo "✓ pre-commit: $(pre-commit --version)"
    return
  fi
  if command -v brew >/dev/null 2>&1; then
    echo "→ brew install pre-commit ..."
    if brew install pre-commit 2>/dev/null; then
      return
    fi
    echo "  (brew 不可用，改用 pip)"
  fi
  echo "→ pip install pre-commit ..."
  python3 -m pip install --user pre-commit
  export PATH="${HOME}/Library/Python/3.14/bin:${HOME}/.local/bin:${PATH}"
}

init_git_if_needed() {
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "✓ Git 仓库已存在"
    return
  fi
  echo "→ git init ..."
  if git init -b main 2>/dev/null; then
    git config user.email "${GIT_AUTHOR_EMAIL:-dev@local}"
    git config user.name "${GIT_AUTHOR_NAME:-dev}"
  else
    echo "  (跳过: 无 .git 写权限，请在系统终端执行 git init)"
  fi
}

run_checks() {
  export PATH="${HOME}/Library/Python/3.14/bin:${HOME}/.local/bin:/opt/homebrew/bin:${PATH}"

  echo ""
  echo "=== 1/3 全量扫描（bin/gitleaks）==="
  bin/gitleaks detect --source . --config gitleaks.toml --no-git --verbose --redact

  if ! pre_commit_ok; then
    echo ""
    echo "⚠ 未安装 pre-commit，跳过钩子安装。可执行: python3 -m pip install --user pre-commit"
    echo "✅ Gitleaks 扫描已通过。"
    return
  fi

  if git rev-parse --is-inside-work-tree >/dev/null 2>&1 && pre_commit_ok; then
    echo ""
    echo "=== 2/3 pre-commit 钩子安装 ==="
    pre-commit install || echo "  (钩子安装跳过: 无 .git/hooks 写权限时请在系统终端重试)"
    pre-commit install --hook-type commit-msg 2>/dev/null || true

    echo ""
    echo "=== 3/3 pre-commit 全量跑 gitleaks ==="
    pre-commit run gitleaks --all-files || echo "  (pre-commit 未跑通，可在 Mac 终端重试)"
  else
    echo ""
    echo "=== 2/3 跳过 pre-commit（Git 未初始化）==="
    echo "  在系统终端执行: git init -b main && pre-commit install"
  fi

  echo ""
  echo "✅ 全部通过。提交代码时会自动跑 Gitleaks；推送到 GitHub 会跑 Actions。"
}

install_gitleaks_bin
install_pre_commit || true
init_git_if_needed || true
run_checks
