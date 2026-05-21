"""Have I Been Pwned API v3 客户端（异步 aiohttp）。"""
from __future__ import annotations

import re
from urllib.parse import quote

HIBP_BASE = "https://haveibeenpwned.com/api/v3"
HIBP_SITE = "https://haveibeenpwned.com"


def breach_public_page_url(name: str) -> str:
    """单条泄露在 HIBP 官网的详情页（非首页）。"""
    slug = quote((name or "").strip(), safe="")
    return f"{HIBP_SITE}/breach/{slug}"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", re.IGNORECASE)


class HibpError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def is_valid_email(email: str) -> bool:
    return bool(email and EMAIL_RE.match((email or "").strip()))


def _headers(api_key: str | None, user_agent: str) -> dict:
    h = {"User-Agent": (user_agent or "PyCharmMiscProject-TelegramBot/1.0").strip()}
    if api_key:
        h["hibp-api-key"] = api_key.strip()
    return h


async def _request_json(session, url: str, headers: dict, *, allow_404: bool = False):
    timeout = session.timeout if getattr(session, "timeout", None) else None
    async with session.get(url, headers=headers, timeout=timeout) as resp:
        if resp.status == 404 and allow_404:
            return None
        if resp.status == 401:
            raise HibpError("API Key 无效或未授权", status=401)
        if resp.status == 429:
            raise HibpError("请求过于频繁", status=429)
        if resp.status >= 400:
            text = (await resp.text())[:500]
            raise HibpError(text or f"HTTP {resp.status}", status=resp.status)
        return await resp.json()


async def get_breach_by_name(
    session,
    name: str,
    api_key: str | None = None,
    user_agent: str = "",
):
    """按 Name 获取单条泄露详情（如 Adobe）。"""
    slug = quote((name or "").strip(), safe="")
    data = await _request_json(
        session,
        f"{HIBP_BASE}/breach/{slug}",
        _headers(api_key, user_agent),
        allow_404=True,
    )
    if data is None:
        raise HibpError("未找到该泄露事件", status=404)
    return data


async def list_all_breaches(
    session,
    user_agent: str,
    limit: int | None = None,
    api_key: str | None = None,
):
    data = await _request_json(
        session,
        f"{HIBP_BASE}/breaches",
        _headers(api_key, user_agent),
    )
    if not isinstance(data, list):
        return []
    return data[:limit] if limit else data


async def get_breaches_for_account(
    session,
    email: str,
    api_key: str | None = None,
    user_agent: str = "",
):
    account = quote((email or "").strip(), safe="")
    url = f"{HIBP_BASE}/breachedaccount/{account}?truncateResponse=false"
    data = await _request_json(
        session,
        url,
        _headers(api_key, user_agent),
        allow_404=True,
    )
    if data is None:
        return 404, []
    return 200, data if isinstance(data, list) else []


async def get_pastes_for_account(
    session,
    email: str,
    api_key: str | None = None,
    user_agent: str = "",
):
    account = quote((email or "").strip(), safe="")
    url = f"{HIBP_BASE}/pasteaccount/{account}"
    data = await _request_json(
        session,
        url,
        _headers(api_key, user_agent),
        allow_404=True,
    )
    if data is None:
        return 404, []
    return 200, data if isinstance(data, list) else []


async def ping(session, api_key: str | None, user_agent: str) -> dict:
    """测试 HIBP API 连通性（供 /hibp_ping 使用）。"""
    result = {
        "breaches_ok": False,
        "breaches_count": 0,
        "breaches_error": "",
        "account_note": "-",
    }

    try:
        items = await list_all_breaches(
            session, user_agent, limit=5, api_key=api_key or None
        )
        result["breaches_ok"] = True
        result["breaches_count"] = len(items)
    except HibpError as e:
        result["breaches_error"] = str(e)
    except Exception as e:
        result["breaches_error"] = str(e)

    if not (api_key or "").strip():
        result["account_note"] = "未配置 HIBP_API_KEY，无法测试邮箱查询"
        return result

    try:
        status, breaches = await get_breaches_for_account(
            session,
            "account-exists@hibp.integration-tests.com",
            api_key=api_key,
            user_agent=user_agent,
        )
        if status == 404:
            result["account_note"] = "Key 有效（测试邮箱无泄露，正常）"
        else:
            result["account_note"] = f"Key 有效（测试命中 {len(breaches)} 条泄露）"
    except HibpError as e:
        if e.status == 401:
            result["account_note"] = "Key 无效 (401)"
        else:
            result["account_note"] = str(e)
    except Exception as e:
        result["account_note"] = str(e)

    return result
