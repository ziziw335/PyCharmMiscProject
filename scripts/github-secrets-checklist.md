# GitHub Actions Secrets 配置清单

仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

## 必填（部署）

| Secret | 示例 / 说明 |
|--------|-------------|
| `SSH_HOST` | `188.116.22.203` |
| `SSH_USER` | `root` |
| `SSH_PRIVATE_KEY` | `-----BEGIN OPENSSH PRIVATE KEY-----` 整段 |
| `BOT_TOKEN` | Telegram BotFather 令牌 |
| `WEB_TOKEN` | Web 后台登录密码 |

## 建议填写

| Secret | 说明 |
|--------|------|
| `DATABASE_URL` | `sqlite:///data/bot.db` 或 PostgreSQL 连接串 |
| `DATABASE_PASSWORD` | 数据库密码（若单独管理） |
| `API_TOKEN` | 第三方 API |
| `BOT_OWNER_ID` | Telegram 用户 ID |
| `SUPER_ADMIN_ID` | 超级管理员 ID |
| `PUBLIC_WEB_BASE_URL` | `http://188.116.22.203:8081` |
| `DEPLOY_PATH` | 默认 `/opt/bot`，可不填 |

## 可选

| Secret | 说明 |
|--------|------|
| `SSH_PORT` | 非 22 时填写 |
| `GIT_DEPLOY_TOKEN` | 私有仓库 `git pull` 用 PAT |
| `TELEGRAM_SECRET_TOKEN` | Webhook 模式 |
| `HIBP_API_KEY` | 泄露查询 |

配置完成后，推送 `main` 分支即可在 **Actions** 页查看 **Deploy to Production**。
