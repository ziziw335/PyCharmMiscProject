# PyCharmMiscProject

Telegram 记账 Bot + Web 管理后台（Python / aiogram / FastAPI）。

- 功能与部署说明见 [如何使用.md](如何使用.md)
- 环境变量模板见 [.env.example](.env.example)

---

## 团队代码安全规范

本项目已接入 **Gitleaks** 敏感信息扫描（本地 pre-commit + GitHub Actions）。任何包含真实密钥的提交都会被拦截，请全员遵守以下约定。

### 1. 原则

| 可以做 | 禁止做 |
|--------|--------|
| 把密钥写在 **`.env`**（仅本机/服务器） | 在 `.py` / `.md` / 配置里 **硬编码** 密码、Token、API Key |
| 提交 **`.env.example`**（仅占位符、无真实值） | 把 **`.env`** 提交到 Git |
| 误报测试数据放在 `tests/` 或示例文件 | 为通过扫描而把真实密钥写进 allowlist |

### 2. 首次环境准备（每位同事执行一次）

在项目根目录打开终端：

```bash
cd /path/to/PyCharmMiscProject

# 安装 pre-commit（任选其一）
pip install pre-commit
# 或: brew install pre-commit

# 安装 Git 提交前钩子（每次 commit 前自动跑 Gitleaks）
pre-commit install

# 可选：首次全量自检
pre-commit run gitleaks --all-files
```

也可使用项目自带脚本（会下载 `bin/gitleaks` 并做全量扫描）：

```bash
bash scripts/setup-gitleaks.sh
```

### 3. 日常开发流程

1. 复制环境模板：`cp .env.example .env`，在 **`.env`** 中填写真实配置。
2. 正常改代码、暂存、`git commit`。
3. 若 pre-commit 中的 **gitleaks** 报错，说明暂存区或历史片段里疑似含有密钥，**不要**使用 `--no-verify` 跳过检查。

手动扫描（不提交也可跑）：

```bash
./bin/gitleaks detect --source . --config gitleaks.toml --no-git --verbose
# 或
pre-commit run gitleaks --all-files
```

### 4. 被 Gitleaks 拦截时怎么办

1. **看终端输出**：会标明文件路径、行号与规则类型（如 `generic-api-key`）。
2. **从代码中删除明文密钥**，改为从环境变量读取，例如：

   ```python
   import os
   from dotenv import load_dotenv

   load_dotenv()
   api_token = os.getenv("API_TOKEN", "").strip()
   if not api_token:
       raise RuntimeError("API_TOKEN is missing — set it in .env")
   ```

   可参考根目录示例：[secrets_demo.py](secrets_demo.py)。

3. **把真实值只写入 `.env`**（该文件已在 `.gitignore` 中，不会进仓库）：

   ```bash
   # .env（勿提交）
   API_TOKEN=你的真实令牌
   DATABASE_PASSWORD=你的数据库密码
   ```

4. 若仓库里需要文档说明变量名，只更新 **`.env.example`**，值留空或写占位符：

   ```bash
   # .env.example（可提交）
   API_TOKEN=
   DATABASE_PASSWORD=
   ```

5. 再次暂存并提交；确认 `git diff` 中 **没有** `.env` 文件。

6. **仅当** 确认为测试假数据误报时，再在 [gitleaks.toml](gitleaks.toml) 的 `[allowlist]` 中按路径/规则追加白名单，并经过 Code Review，**禁止**把生产密钥加入白名单。

### 5. 本地必须忽略的文件（`.gitignore`）

以下规则已写入项目 `.gitignore`，请勿移除：

- `.env` — 本地真实密钥
- `data/` — 本地数据库
- `.venv/` — 虚拟环境
- `bin/gitleaks` — 本地下载的扫描二进制

完整说明见 [.gitignore](.gitignore)。

### 6. CI（GitHub Actions）

推送到远程或发起 Pull Request 时，工作流 [.github/workflows/gitleaks.yml](.github/workflows/gitleaks.yml) 会自动执行 Gitleaks；发现泄露则 **CI 失败**，需修复后重新推送。

### 7. 联系人

对误报、白名单或扫描规则有疑问，请与仓库维护者沟通后再改 `gitleaks.toml`，避免为图省事关闭扫描。

---

## 生产自动部署（GitHub Actions）

推送至 `main` / `master` 时，[.github/workflows/deploy.yml](.github/workflows/deploy.yml) 会通过 SSH 部署到 Linux 服务器。

### 服务器一次性准备

```bash
# 安装 Docker 与 Compose 插件
# 克隆仓库（示例路径与 deploy_to_vps.sh 一致）
git clone git@github.com:YOUR_ORG/YOUR_REPO.git /opt/bot
cd /opt/bot
mkdir -p data
```

若此前用 **systemd** 跑 Bot，首次切 Docker 前请先 `systemctl stop telegram-bot telegram-web`，避免双实例抢 `BOT_TOKEN`。

### 必填 GitHub Secrets

| Secret | 说明 |
|--------|------|
| `SSH_HOST` | 服务器 IP |
| `SSH_USER` | SSH 用户 |
| `SSH_PRIVATE_KEY` | SSH 私钥 PEM |
| `BOT_TOKEN` | Telegram Bot |
| `WEB_TOKEN` | Web 后台密码 |
| `DATABASE_PASSWORD` | 数据库密码（按需） |
| `API_TOKEN` | 第三方 API（按需） |
| `DATABASE_URL` | 数据库连接串 |

可选：`DEPLOY_PATH`（默认 `/opt/bot`）、`SSH_PORT`、`GIT_DEPLOY_TOKEN`（私有库 HTTPS pull）、其余见 workflow 文件头注释。

部署步骤：SSH → `scripts/ci-deploy.sh`（写 `.env` → `git pull` → **Docker** 或 **systemd** 自动选择）。

**本地一键自检**（请在 Mac「终端」执行，不要在 Cursor 内置终端）：

```bash
cd /Users/xiaoyang/PyCharmMiscProject
bash scripts/run-all-local.sh
```

Secrets 清单见 [scripts/github-secrets-checklist.md](scripts/github-secrets-checklist.md)。
