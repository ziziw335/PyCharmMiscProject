"""银行卡号查询：BIN 识别发卡行；可选第三方 API。姓名/手机号无法仅凭卡号从公开渠道获取。"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent
BINS_PATH = PROJECT_DIR / "data" / "bank_bins.json"

_CARD_RE = re.compile(r"\d{13,19}")


class BankCardError(Exception):
    pass


def extract_card_number(text: str) -> str | None:
    """从文本中提取银行卡号（13–19 位数字）。"""
    t = re.sub(r"[\s\-]", "", (text or "").strip())
    for prefix in ("查卡", "银行卡", "卡号", "bankcard", "card"):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix) :].lstrip("：:").strip()
            break
    m = _CARD_RE.search(t)
    if not m:
        return None
    digits = m.group(0)
    if len(digits) < 13 or len(digits) > 19:
        return None
    return digits


def luhn_valid(card_no: str) -> bool:
    digits = [int(c) for c in card_no if c.isdigit()]
    if len(digits) < 13:
        return False
    s = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        s += d
    return s % 10 == 0


def mask_card(card_no: str) -> str:
    if len(card_no) <= 10:
        return card_no
    return f"{card_no[:6]}****{card_no[-4:]}"


_bins_cache: dict | None = None


def _load_bins() -> dict:
    global _bins_cache
    if _bins_cache is not None:
        return _bins_cache
    if not BINS_PATH.exists():
        _bins_cache = {}
        return _bins_cache
    with open(BINS_PATH, encoding="utf-8") as f:
        _bins_cache = json.load(f)
    return _bins_cache


def lookup_local_bin(card_no: str) -> dict | None:
    bins = _load_bins()
    for length in range(8, 5, -1):
        prefix = card_no[:length]
        if prefix in bins:
            row = bins[prefix]
            return {
                "bank": row.get("bank") or "",
                "card_type": row.get("card_type") or "",
                "province": row.get("province") or "",
                "city": row.get("city") or "",
                "bin": prefix,
            }
    return None


def _pick(data: dict, *keys: str) -> str:
    for k in keys:
        v = data.get(k)
        if v is not None and str(v).strip() not in ("", "-", "null", "None"):
            return str(v).strip()
    return ""


async def _fetch_remote_api(session, card_no: str) -> dict | None:
    """可选：.env 配置 BANK_CARD_API_URL，支持 {card} 占位符。"""
    tpl = (os.getenv("BANK_CARD_API_URL") or "").strip()
    if not tpl or not session:
        return None
    url = tpl.replace("{card}", card_no)
    headers = {}
    api_key = (os.getenv("BANK_CARD_API_KEY") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        if "?" in url:
            url += f"&key={api_key}"
        else:
            url += f"?key={api_key}"

    async with session.get(url, headers=headers, timeout=12) as resp:
        raw = await resp.text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise BankCardError(f"接口返回非 JSON（HTTP {resp.status}）")

    if isinstance(data, dict):
        if data.get("code") not in (None, 0, 200, "0", "200") and data.get("success") is not True:
            msg = _pick(data, "msg", "message", "error") or "查询失败"
            if str(data.get("code")) not in ("0", "200"):
                raise BankCardError(msg)
        inner = data.get("data") if isinstance(data.get("data"), dict) else data
        if isinstance(inner, list) and inner:
            inner = inner[0]
        if not isinstance(inner, dict):
            inner = data
        return {
            "bank": _pick(
                inner,
                "bank",
                "bankName",
                "bank_name",
                "issuer",
                "bankTitle",
                "开户行",
                "bankcard",
            ),
            "card_type": _pick(
                inner, "cardType", "card_type", "type", "category", "卡类型"
            ),
            "province": _pick(inner, "province", "prov", "area", "归属省份"),
            "city": _pick(inner, "city", "归属城市"),
            "name": _pick(inner, "name", "userName", "username", "holder", "姓名", "accountName"),
            "phone": _pick(
                inner, "phone", "mobile", "tel", "phoneNumber", "mobilePhone", "手机号", "绑定手机"
            ),
            "bin": _pick(inner, "bin", "cardBin"),
        }
    return None


async def lookup_bank_card(session, card_no: str) -> dict:
    """
    查询银行卡信息。
    返回字段：bank, card_type, province, city, name, phone, valid_luhn, source, privacy_note
    """
    card_no = re.sub(r"\D", "", card_no)
    if len(card_no) < 13 or len(card_no) > 19:
        raise BankCardError("卡号应为 13–19 位数字")

    result = {
        "card_no": card_no,
        "card_masked": mask_card(card_no),
        "valid_luhn": luhn_valid(card_no),
        "bank": "",
        "card_type": "",
        "province": "",
        "city": "",
        "bin": "",
        "name": "",
        "phone": "",
        "source": "",
        "privacy_note": "",
    }

    local = lookup_local_bin(card_no)
    if local:
        result.update(local)
        result["source"] = "local_bin"

    if session:
        try:
            remote = await _fetch_remote_api(session, card_no)
            if remote:
                for k, v in remote.items():
                    if v and not result.get(k):
                        result[k] = v
                result["source"] = (
                    "local_bin+api" if result["source"] == "local_bin" else "api"
                )
        except BankCardError:
            raise
        except Exception as e:
            if not result["bank"]:
                raise BankCardError(f"远程接口异常：{e}") from e

    if not result["bank"]:
        result["bank"] = "未能识别（卡 BIN 未收录，可配置 BANK_CARD_API_URL）"

    if not result["name"] and not result["phone"]:
        result["privacy_note"] = (
            "姓名、绑定手机号无法仅凭卡号从银行公开接口查询；"
            "需持卡人授权或合规四要素验证服务（可在 .env 配置 BANK_CARD_API_URL）。"
        )
    elif not result["name"]:
        result["privacy_note"] = "未返回持卡人姓名"
    elif not result["phone"]:
        result["privacy_note"] = "未返回绑定手机号"

    return result


def format_bank_card_reply(data: dict) -> str:
    from html import escape

    lines = [
        "🏦 <b>银行卡查询结果</b>",
        "",
        f"卡号：<code>{escape(data.get('card_masked') or '')}</code>",
        f"LUHN 校验：{'✅ 通过' if data.get('valid_luhn') else '⚠️ 未通过（可能输入有误）'}",
        f"发卡行 / 开户行：<b>{escape(data.get('bank') or '-')}</b>",
        f"卡类型：{escape(data.get('card_type') or '-')}",
    ]
    if data.get("bin"):
        lines.append(f"BIN：<code>{escape(data['bin'])}</code>")
    if data.get("province") or data.get("city"):
        area = " ".join(x for x in [data.get("province"), data.get("city")] if x)
        lines.append(f"归属地：{escape(area)}")
    lines.append(f"持卡人姓名：{escape(data.get('name') or '—（未提供）')}")
    lines.append(f"绑定手机号：{escape(data.get('phone') or '—（未提供）')}")
    if data.get("privacy_note"):
        lines.extend(["", f"<i>{escape(data['privacy_note'])}</i>"])
    if data.get("source"):
        lines.append(f"\n<i>数据来源：{escape(data['source'])}</i>")
    return "\n".join(lines)
