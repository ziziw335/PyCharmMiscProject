"""
示例：通过 python-dotenv 从 .env 读取密钥，避免硬编码。

本地开发：
  1. cp .env.example .env
  2. 在 .env 中填写 DATABASE_PASSWORD、API_TOKEN（勿提交 .env）
  3. python secrets_demo.py

依赖：python-dotenv（见 requirements.txt）
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

# 从项目根目录加载 .env（与 app.py、web.py 一致）
load_dotenv()


def require_env(name: str) -> str:
    """读取必填环境变量；缺失时给出明确提示。"""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"{name} is missing — copy .env.example to .env and set it locally."
        )
    return value


def get_database_password() -> str:
    return require_env("DATABASE_PASSWORD")


def get_api_token() -> str:
    return require_env("API_TOKEN")


def main() -> None:
    # 切勿在源码中写死 API_TOKEN / DATABASE_PASSWORD，应使用下方 getenv 方式。

    db_password = get_database_password()
    api_token = get_api_token()

    # 演示：只打印长度，避免在日志中泄露明文
    print("Loaded from .env (values not printed):")
    print(f"  DATABASE_PASSWORD length: {len(db_password)}")
    print(f"  API_TOKEN length: {len(api_token)}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        print("\nHint: cp .env.example .env  then edit .env", file=sys.stderr)
        sys.exit(1)
