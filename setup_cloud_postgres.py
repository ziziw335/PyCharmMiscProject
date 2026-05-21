#!/usr/bin/env python3
"""
连接云 PostgreSQL（Neon / Supabase 等）并初始化表结构。

用法（把连接串换成 Neon 控制台复制的）:
  .venv/bin/python setup_cloud_postgres.py "postgresql://用户:密码@ep-xxx.neon.tech/neondb?sslmode=require"

可选：从本地 SQLite 迁移数据
  .venv/bin/python setup_cloud_postgres.py "postgresql://..." --migrate-sqlite
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent
ENV_PATH = PROJECT_DIR / ".env"


def _mask_url(url: str) -> str:
    return re.sub(r":([^:@/]+)@", r":****@", url, count=1)


def update_env_file(database_url: str, pgsslmode: str = "require") -> None:
    if not ENV_PATH.exists():
        raise FileNotFoundError(f"找不到 {ENV_PATH}")

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    seen_db = seen_ssl = False

    for line in lines:
        if line.startswith("DATABASE_URL="):
            out.append(f"DATABASE_URL={database_url}")
            seen_db = True
        elif line.startswith("PGSSLMODE="):
            out.append(f"PGSSLMODE={pgsslmode}")
            seen_ssl = True
        else:
            out.append(line)

    if not seen_db:
        out.append(f"DATABASE_URL={database_url}")
    if not seen_ssl:
        out.append(f"PGSSLMODE={pgsslmode}")

    # 保留 SQLite 备份说明
    if not any("sqlite" in ln.lower() for ln in out if ln.strip().startswith("#")):
        out.insert(0, "# 云 PostgreSQL 已启用；本地库备份见 data/bot.db")

    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def test_postgres_url(url: str, pgsslmode: str) -> None:
    import psycopg2

    kwargs = {}
    if pgsslmode:
        kwargs["sslmode"] = pgsslmode
    conn = psycopg2.connect(url, **kwargs)
    try:
        cur = conn.cursor()
        cur.execute("SELECT version()")
        ver = cur.fetchone()[0]
        cur.close()
        print("✅ 云 PostgreSQL 连接成功")
        print(f"   {_mask_url(url)}")
        print(f"   版本: {ver[:80]}...")
    finally:
        conn.close()


def run_init_db() -> None:
    os.environ.setdefault("PGSSLMODE", "require")
    from db import DATABASE_URL, USE_SQLITE, init_db, add_admin

    if USE_SQLITE:
        raise RuntimeError("DATABASE_URL 仍是 SQLite，请检查 .env 是否已保存")

    init_db()
    owner = int(os.getenv("BOT_OWNER_ID", "0") or 0)
    if owner:
        add_admin(owner, "owner")
    print("✅ 表结构已创建/更新 (init_db)")
    print(f"   当前 DATABASE_URL={_mask_url(DATABASE_URL)}")


def migrate_sqlite_to_postgres() -> None:
    import sqlite3

    sqlite_path = PROJECT_DIR / "data" / "bot.db"
    if not sqlite_path.exists():
        print("⚠️ 未找到 data/bot.db，跳过 SQLite 迁移")
        return

    from db import get_db

    tables: list[tuple[str, str, str]] = [
        ("admins", "user_id, role", "ON CONFLICT (user_id) DO NOTHING"),
        ("groups", "chat_id, name, updated_at", "ON CONFLICT (chat_id) DO NOTHING"),
        ("settings", "chat_id, key, value", "ON CONFLICT (chat_id, key) DO NOTHING"),
        (
            "access_users",
            "user_id, username, granted_by, granted_at, expires_at",
            "ON CONFLICT (user_id) DO NOTHING",
        ),
        (
            "transactions",
            "chat_id, user_id, username, display_name, target_name, kind, "
            "raw_amount, unit_amount, rate_used, fee_used, note, original_text, created_at, undone",
            "",
        ),
    ]

    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = sqlite3.Row
    total = 0

    try:
        with get_db(commit=True) as (_, cur):
            for table, cols, on_conflict in tables:
                try:
                    rows = src.execute(f"SELECT {cols} FROM {table}").fetchall()
                except sqlite3.OperationalError:
                    continue
                if not rows:
                    continue
                placeholders = ", ".join(["%s"] * len(rows[0]))
                suffix = f" {on_conflict}" if on_conflict else ""
                for row in rows:
                    cur.execute(
                        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}){suffix}",
                        tuple(row),
                    )
                total += len(rows)
                print(f"   迁移 {table}: {len(rows)} 行")
    finally:
        src.close()

    print(f"✅ SQLite 迁移完成（约 {total} 行，冲突行已跳过）")


def main() -> int:
    parser = argparse.ArgumentParser(description="连接并初始化云 PostgreSQL")
    parser.add_argument(
        "database_url",
        nargs="?",
        help="Neon 等提供的 postgresql:// 连接串",
    )
    parser.add_argument(
        "--migrate-sqlite",
        action="store_true",
        help="从 data/bot.db 迁移部分数据到云库",
    )
    parser.add_argument("--no-write-env", action="store_true", help="只测试，不写 .env")
    args = parser.parse_args()

    load_dotenv()

    url = (args.database_url or "").strip()
    if not url:
        print("❌ 请提供云数据库连接串，例如：")
        print(
            '  .venv/bin/python setup_cloud_postgres.py '
            '"postgresql://用户:密码@ep-xxx.neon.tech/neondb?sslmode=require"'
        )
        print("\n获取连接串：")
        print("  1. 打开 https://neon.tech 注册并新建项目")
        print("  2. Dashboard → Connection string → 复制 Pooled connection")
        print("  3. 粘贴到上面命令里（保留引号）")
        return 1

    if url.startswith("sqlite"):
        print("❌ 请使用 postgresql:// 开头的云连接串，不是 SQLite")
        return 1

    if not url.startswith(("postgresql://", "postgres://")):
        print("❌ 连接串应以 postgresql:// 或 postgres:// 开头")
        return 1

    pgsslmode = os.getenv("PGSSLMODE", "require").strip() or "require"
    if "sslmode=" not in url and pgsslmode:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode={pgsslmode}"

    print("🔌 正在测试云数据库连接…")
    try:
        test_postgres_url(url, pgsslmode)
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        print("\n常见原因：")
        print("  • 连接串复制不完整（缺密码或主机名）")
        print("  • 密码含特殊字符 @#% 等，需在 Neon 里用编码后的密码或重新生成")
        print("  • 未加 sslmode=require（Neon 必须 SSL）")
        return 1

    if not args.no_write_env:
        update_env_file(url, pgsslmode)
        print(f"✅ 已写入 {ENV_PATH}")

    os.environ["DATABASE_URL"] = url
    os.environ["PGSSLMODE"] = pgsslmode

    # 确保 db 模块读到新 URL
    for mod in list(sys.modules):
        if mod == "db" or mod.startswith("db."):
            del sys.modules[mod]

    try:
        run_init_db()
        if args.migrate_sqlite:
            migrate_sqlite_to_postgres()
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        return 1

    print("\n🎉 完成。请重启 PyCharm 里的「运行 Bot」和「运行 Web后台」。")
    print("   自检: .venv/bin/python test_db.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
