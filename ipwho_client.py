"""IP 归属地查询（ipwho.is 公开 API，仅查询公网 IP）。"""
from __future__ import annotations

import re

IPV4_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)$"
)


def extract_ipv4(text: str) -> str | None:
    t = (text or "").strip()
    for prefix in ("ip", "IP", "查询ip", "查询IP", "查ip", "查IP"):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix) :].strip().lstrip("：:").strip()
            break
    m = re.search(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b",
        t,
    )
    if m and IPV4_RE.match(m.group(0)):
        return m.group(0)
    return None


async def _parse_ipwho_response(resp) -> dict:
    data = await resp.json()
    if not isinstance(data, dict):
        raise ValueError("接口返回非 JSON")
    if not data.get("success"):
        msg = data.get("message") or "查询失败"
        raise ValueError(str(msg))
    return data


async def lookup_ip(session, ip: str) -> dict:
    url = f"https://ipwho.is/{ip}"
    async with session.get(url) as resp:
        return await _parse_ipwho_response(resp)


async def lookup_self_ip(session) -> dict:
    """查询 Bot 运行环境对外的公网 IP（非 Telegram 用户手机 IP）。"""
    async with session.get("https://ipwho.is/") as resp:
        return await _parse_ipwho_response(resp)
