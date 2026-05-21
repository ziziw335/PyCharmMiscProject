"""创建本地数据库（首次运行或重置时执行）。"""
import os

from dotenv import load_dotenv

load_dotenv()

from db import DATABASE_URL, USE_SQLITE, add_admin, init_db

if __name__ == "__main__":
    init_db()
    owner = int(os.getenv("BOT_OWNER_ID", "0") or 0)
    if owner:
        add_admin(owner, "owner")
    kind = "SQLite 本地库" if USE_SQLITE else "PostgreSQL"
    print(f"✅ 数据库已创建 ({kind})")
    print(f"   路径/连接: {DATABASE_URL}")
    if owner:
        print(f"   管理员已写入: {owner}")
