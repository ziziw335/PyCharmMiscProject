"""检查数据库是否可用。"""
import os
import re
import sys

from dotenv import load_dotenv


def _mask_db_url(url: str) -> str:
    return re.sub(r":([^:@/]+)@", r":****@", url, count=1)

load_dotenv()

from db import DATABASE_URL, USE_SQLITE, PROJECT_DIR, init_db

if USE_SQLITE:
    from db import get_db, _sqlite_file_path

    try:
        init_db()
        with get_db() as (_, cur):
            cur.execute("SELECT 1")
            cur.fetchone()
        db_file = _sqlite_file_path()
        print("✅ 本地 SQLite 数据库正常（连接成功）")
        print(f"   配置: {DATABASE_URL}")
        print(f"   文件: {db_file}")
        print(f"   退出: 0 — 无需 PostgreSQL，可直接运行 app.py / web.py")
        sys.exit(0)
    except Exception as e:
        print(f"❌ SQLite 失败: {e}")
        print(f"   配置: {DATABASE_URL}")
        print(f"   项目目录: {PROJECT_DIR}")
        sys.exit(1)

url = os.getenv("DATABASE_URL", "").strip()
if not url:
    print("❌ DATABASE_URL 为空")
    print("   本地开发可在 .env 使用: DATABASE_URL=sqlite:///data/bot.db")
    sys.exit(1)

try:
    import psycopg2

    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute("SELECT 1")
    cur.close()
    conn.close()
    print("✅ PostgreSQL 连接成功")
    print(f"   连接串: {_mask_db_url(url)[:80]}...")
    sys.exit(0)
except Exception as e:
    print(f"❌ PostgreSQL 连接失败: {e}")
    print("   若只用本机 Bot，可改 .env 为:")
    print("   DATABASE_URL=sqlite:///data/bot.db")
    sys.exit(1)
