# 填写 GitHub Secrets（3 种方式任选）

仓库：**ziziw335/PyCharmMiscProject**

---

## 方式 A：一键脚本（推荐）

### 第 1 步：生成 VPS 密钥并加到服务器

```bash
cd /Users/xiaoyang/PyCharmMiscProject
bash scripts/setup-vps-deploy-key.sh
```

按提示把公钥加到 VPS `~/.ssh/authorized_keys`。

### 第 2 步：填写本地密钥表

```bash
cp scripts/deploy-secrets.local.env.example scripts/deploy-secrets.local.env
open -e scripts/deploy-secrets.local.env
```

**必须改的两项：**

- `BOT_TOKEN=` → 去 [@BotFather](https://t.me/BotFather) 复制
- `WEB_TOKEN=` → 自定一个长密码

### 第 3 步：一键上传到 GitHub

```bash
bash scripts/push-github-secrets.sh
```

首次会打开浏览器让你登录 GitHub 授权 `gh`。

---

## 方式 B：网页手动添加

**直接打开（Secrets 列表）：**  
https://github.com/ziziw335/PyCharmMiscProject/settings/secrets/actions

**每添加一个点：**  
https://github.com/ziziw335/PyCharmMiscProject/settings/secrets/actions/new

| 点 New secret 后 Name 填 | Secret 填什么 |
|--------------------------|---------------|
| `SSH_HOST` | `188.116.22.203` |
| `SSH_USER` | `root` |
| `SSH_PRIVATE_KEY` | 运行 `cat ~/.ssh/id_ed25519_vps_deploy` 的**整段输出** |
| `BOT_TOKEN` | Telegram Bot Token |
| `WEB_TOKEN` | Web 后台密码 |
| `DEPLOY_PATH` | `/opt/bot` |
| `DATABASE_URL` | `sqlite:///data/bot.db` |
| `PUBLIC_WEB_BASE_URL` | `http://188.116.22.203:8081` |

路径：**仓库首页** → 上方 **Settings** → 左侧 **Secrets and variables** → **Actions**

---

## 方式 C：从 Mac 网页进 Settings 找不到时

1. 打开 https://github.com/ziziw335/PyCharmMiscProject  
2. 顶部菜单 **Settings**（仓库设置，不是头像里的）  
3. 左侧 **Secrets and variables** → **Actions**  
4. 绿色按钮 **New repository secret**

---

## 填完后

1. https://github.com/ziziw335/PyCharmMiscProject/actions  
2. 失败的 **Deploy to Production** → **Re-run all jobs**
