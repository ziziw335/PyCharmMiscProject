"""银行卡四要素核验（合规）：姓名+身份证+卡号+手机号 是否一致。"""
from __future__ import annotations

import json
import os
import re

from dotenv import load_dotenv

load_dotenv()

# 易源 / 阿里云市场常见网关（可在 .env 覆盖）
DEFAULT_VERIFY_URL = "https://ali-bankcard4.showapi.com/bank4"


class BankVerifyError(Exception):
    pass


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def parse_four_elements(text: str) -> dict | None:
    """
    解析四要素，支持：
    1) 四行：姓名 / 身份证 / 卡号 / 手机号
    2) 一行逗号或竖线分隔
    3) 带标签：姓名张三 身份证110... 卡号6222... 手机138...
    """
    raw = (text or "").strip()
    if not raw:
        return None

    # 四行
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) >= 4:
        name, cert_id, card, phone = lines[0], lines[1], lines[2], lines[3]
        card = _digits_only(card)
        cert_id = _digits_only(cert_id)
        phone = _digits_only(phone)
        if name and len(cert_id) >= 15 and 13 <= len(card) <= 19 and 11 <= len(phone) <= 11:
            return {
                "name": name,
                "cert_id": cert_id,
                "card": card,
                "phone": phone,
            }

    # 分隔符
    for sep in ("|", ",", "，", "\t"):
        if sep in raw:
            parts = [p.strip() for p in raw.split(sep) if p.strip()]
            if len(parts) >= 4:
                name, cert_id, card, phone = parts[0], parts[1], parts[2], parts[3]
                card = _digits_only(card)
                cert_id = _digits_only(cert_id)
                phone = _digits_only(phone)
                if name and len(cert_id) >= 15 and 13 <= len(card) <= 19 and len(phone) == 11:
                    return {
                        "name": name,
                        "cert_id": cert_id,
                        "card": card,
                        "phone": phone,
                    }

    # 标签提取
    name_m = re.search(r"(?:姓名|持卡人)[:：\s]*([^\s\d]{2,20})", raw)
    cert_m = re.search(r"(?:身份证|证件号?)[:：\s]*(\d{15,18}[xX]?)", raw)
    card_m = re.search(r"(?:卡号|银行卡)[:：\s]*(\d{13,19})", raw)
    phone_m = re.search(r"(?:手机|手机号|电话)[:：\s]*(\d{11})", raw)
    if name_m and cert_m and card_m and phone_m:
        return {
            "name": name_m.group(1).strip(),
            "cert_id": _digits_only(cert_m.group(1)),
            "card": card_m.group(1),
            "phone": phone_m.group(1),
        }

    return None


def _pick(data: dict, *keys: str) -> str:
    for k in keys:
        v = data.get(k)
        if v is not None and str(v).strip() not in ("", "-", "null", "None"):
            return str(v).strip()
    return ""


def _parse_verify_response(data: dict) -> dict:
    """兼容易源 showapi、常见 code/data 结构。"""
    inner = data
    if isinstance(data.get("showapi_res_body"), str):
        try:
            inner = json.loads(data["showapi_res_body"])
        except json.JSONDecodeError:
            inner = data
    elif isinstance(data.get("showapi_res_body"), dict):
        inner = data["showapi_res_body"]
    elif isinstance(data.get("data"), dict):
        inner = data["data"]

    code = _pick(
        inner,
        "respCode",
        "code",
        "bizCode",
        "resultCode",
        "status",
    ) or _pick(data, "showapi_res_code", "code")

    msg = _pick(
        inner,
        "respMessage",
        "message",
        "msg",
        "desc",
        "resultMsg",
    ) or _pick(data, "showapi_res_error", "message")

    # 常见：0000 / 1 / 101 表示一致
    matched = False
    c = str(code).upper()
    if c in ("0000", "0", "1", "101", "SUCCESS", "PASS", "TRUE"):
        matched = True
    if "一致" in msg or "匹配" in msg or "通过" in msg or "成功" in msg:
        matched = True
    if "不一致" in msg or "不匹配" in msg or "失败" in msg:
        matched = False

    bank = _pick(inner, "bank", "bankName", "bank_name", "belongArea", "开户行")
    return {
        "matched": matched,
        "code": code,
        "message": msg or ("核验通过" if matched else "核验未通过"),
        "bank": bank,
        "raw": inner,
    }


async def verify_bank_four_elements(
    session,
    *,
    name: str,
    cert_id: str,
    card: str,
    phone: str,
) -> dict:
    """
    调用第三方四要素接口。需在 .env 配置：
    BANK_VERIFY_APPCODE 或 BANK_VERIFY_API_KEY
    BANK_VERIFY_API_URL（可选，默认易源 bank4 网关）
    """
    appcode = (os.getenv("BANK_VERIFY_APPCODE") or os.getenv("BANK_VERIFY_API_KEY") or "").strip()
    if not appcode:
        raise BankVerifyError(
            "未配置 BANK_VERIFY_APPCODE。\n"
            "请到易源/阿里云市场购买「银行卡四要素」接口，把 APPCODE 写入 .env"
        )

    url = (os.getenv("BANK_VERIFY_API_URL") or DEFAULT_VERIFY_URL).strip()
    params = {
        "acct_name": name.strip(),
        "acct_pan": _digits_only(card),
        "cert_id": _digits_only(cert_id),
        "phone_num": _digits_only(phone),
        "cert_type": "01",
        "needBelongArea": "true",
    }
    headers = {"Authorization": f"APPCODE {appcode}"}

    async with session.get(url, params=params, headers=headers, timeout=15) as resp:
        raw = await resp.text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise BankVerifyError(f"接口返回非 JSON（HTTP {resp.status}）")

    if isinstance(data, dict):
        sc = data.get("showapi_res_code")
        if sc is not None and int(sc) != 0:
            err = data.get("showapi_res_error") or "接口错误"
            raise BankVerifyError(str(err))

    result = _parse_verify_response(data if isinstance(data, dict) else {})
    result["name"] = name
    result["card_masked"] = f"{card[:6]}****{card[-4:]}" if len(card) > 10 else card
    result["phone_masked"] = f"{phone[:3]}****{phone[-4:]}" if len(phone) == 11 else phone
    result["cert_masked"] = f"{cert_id[:4]}****{cert_id[-4:]}" if len(cert_id) > 8 else cert_id
    return result


def format_verify_reply(r: dict) -> str:
    from html import escape

    status = "✅ <b>四要素一致</b>" if r.get("matched") else "❌ <b>四要素不一致或未通过</b>"
    lines = [
        "🏦 <b>银行卡四要素核验</b>",
        "",
        status,
        f"说明：{escape(r.get('message') or '-')}",
        "",
        f"姓名：{escape(r.get('name') or '-')}",
        f"身份证：{escape(r.get('cert_masked') or '-')}",
        f"卡号：{escape(r.get('card_masked') or '-')}",
        f"手机：{escape(r.get('phone_masked') or '-')}",
    ]
    if r.get("bank"):
        lines.append(f"发卡行/归属：{escape(r['bank'])}")
    if r.get("code"):
        lines.append(f"\n<i>响应码：{escape(str(r['code']))}</i>")
    lines.extend(
        [
            "",
            "<i>此为合规核验：需同时提供四项信息，不能仅凭卡号反查姓名/手机。</i>",
        ]
    )
    return "\n".join(lines)
