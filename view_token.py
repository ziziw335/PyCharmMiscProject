"""群组账单网页只读令牌（与 WEB_TOKEN 全站后台密码分离）。"""
from __future__ import annotations

import hashlib
import hmac
import os
import time

from dotenv import load_dotenv

load_dotenv()

DEFAULT_TTL_DAYS = int(os.getenv("GROUP_VIEW_TOKEN_DAYS", "7") or 7)


def _secret_bytes() -> bytes:
    key = (os.getenv("GROUP_VIEW_SECRET") or os.getenv("WEB_TOKEN") or "").strip()
    if not key:
        raise RuntimeError("GROUP_VIEW_SECRET or WEB_TOKEN is required")
    return key.encode()


def make_group_view_token(chat_id: int, ttl_days: int | None = None) -> str:
    """生成仅可查看指定群账单页的短期令牌。"""
    ttl = DEFAULT_TTL_DAYS if ttl_days is None else max(1, int(ttl_days))
    exp = int(time.time()) + ttl * 86400
    cid = int(chat_id)
    payload = f"{cid}:{exp}"
    sig = hmac.new(_secret_bytes(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"gv1.{cid}.{exp}.{sig}"


def verify_group_view_token(chat_id: int, token: str | None) -> bool:
    if not token:
        return False
    token = str(token).strip()
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "gv1":
        return False
    try:
        cid = int(parts[1])
        exp = int(parts[2])
        sig = parts[3]
    except ValueError:
        return False
    if cid != int(chat_id):
        return False
    if int(time.time()) > exp:
        return False
    payload = f"{cid}:{exp}"
    expected = hmac.new(_secret_bytes(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, expected)
