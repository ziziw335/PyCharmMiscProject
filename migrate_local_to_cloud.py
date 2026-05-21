#!/usr/bin/env python3
"""把本地 data/bot.db 迁到 .env 里配置的 PostgreSQL（Neon 等）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent


def main() -> int:
    load_dotenv(PROJECT_DIR / ".env")
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url or url.startswith("sqlite"):
        print("❌ 请先在 .env 填写 postgresql:// 连接串，再运行本脚本")
        return 1

    sqlite_path = PROJECT_DIR / "data" / "bot.db"
    if not sqlite_path.exists():
        print(f"❌ 未找到 {sqlite_path}")
        return 1

    from setup_cloud_postgres import migrate_sqlite_to_postgres, run_init_db, test_postgres_url

    pgsslmode = os.getenv("PGSSLMODE", "require").strip() or "require"
    print("🔌 测试云数据库…")
    try:
        test_postgres_url(url, pgsslmode)
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        print("   请到 Neon 控制台复制新的 Connection string 写入 .env")
        return 1

    os.environ["DATABASE_URL"] = url
    os.environ["PGSSLMODE"] = pgsslmode
    for mod in list(sys.modules):
        if mod == "db" or mod.startswith("db."):
            del sys.modules[mod]

    run_init_db()
    migrate_sqlite_to_postgres()
    print("✅ 迁移完成，请重启 Bot 和 Web 后台")
    return 0


if __name__ == "__main__":
    sys.exit(main())
