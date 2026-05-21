"""管理员查询工具：用户名(Sherlock)、IP、手机号归属、身份证解析等。"""
from __future__ import annotations

import asyncio
import json
import re
from html import escape

import requests

# Sherlock 站点（小写名，与 sherlock 数据兼容时再过滤）
SHERLOCK_SITES = [
    "weibo",
    "zhihu",
    "bilibili",
    "twitter",
    "instagram",
    "github",
    "facebook",
    "tiktok",
    "reddit",
]

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
IDCARD_RE = re.compile(r"^\d{15}$|^\d{17}[\dXx]$")
QQ_RE = re.compile(r"^[1-9]\d{4,11}$")


def _sherlock_bin() -> str:
    import os
    import sys
    import shutil

    bin_dir = os.path.dirname(os.path.abspath(sys.executable))
    p = os.path.join(bin_dir, "sherlock")
    if os.path.isfile(p):
        return p
    return shutil.which("sherlock") or "sherlock"


def _sherlock_sync(username: str) -> str:
    import subprocess

    username = (username or "").strip().lstrip("@")
    if not username or len(username) < 2:
        return "❌ 用户名太短"

    found_items: list[dict] = []

    try:
        cmd = [
            _sherlock_bin(),
            username,
            "--print-found",
            "--timeout",
            "10",
            "--no-color",
        ]
        # 限定站点可加 --site GitHub 等（名称须与 sherlock 数据一致）
        priority_sites = ["GitHub", "Twitter", "Instagram", "Weibo", "Zhihu", "Bilibili"]
        for site in priority_sites:
            cmd.extend(["--site", site])
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
        )
        if proc.returncode not in (0, 1, 2) and not proc.stdout:
            err = (proc.stderr or proc.stdout or "sherlock 执行失败")[:200]
            if "No such file" in err or "not found" in err.lower():
                return (
                    "❌ 未安装 sherlock-project\n"
                    "请在服务器执行：pip install sherlock-project"
                )
            return f"❌ 用户名查询出错：{escape(err)}"

        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("[+]"):
                continue
            # [+] GitHub: https://github.com/user
            body = line[3:].strip()
            if ":" in body:
                site, url = body.split(":", 1)
                found_items.append({"site": site.strip(), "url": url.strip()})
            else:
                found_items.append({"site": body, "url": ""})
    except FileNotFoundError:
        return (
            "❌ 未找到 sherlock 命令\n"
            "请在服务器执行：pip install sherlock-project"
        )
    except subprocess.TimeoutExpired:
        return "❌ 用户名查询超时，请稍后重试"
    except Exception as e:
        return f"❌ 用户名查询出错：{escape(str(e)[:120])}"

    if not found_items:
        return f"❌ 未找到 <b>{escape(username)}</b> 的公开账号（已查常见平台）"

    lines = [
        f"✅ <b>{escape(username)}</b> 找到 {len(found_items)} 个平台账号：",
        "",
    ]
    for r in found_items[:20]:
        site = escape(str(r.get("site") or "-"))
        url = escape(str(r.get("url") or ""))
        if url:
            lines.append(f"• {site}: <a href=\"{url}\">{url}</a>")
        else:
            lines.append(f"• {site}")
    if len(found_items) > 20:
        lines.append(f"\n<i>… 另有 {len(found_items) - 20} 条未显示</i>")
    return "\n".join(lines)


async def lookup_username(username: str) -> str:
    return await asyncio.to_thread(_sherlock_sync, username)


def _ip_api_sync(ip: str | None) -> str:
    try:
        if not ip:
            ip = requests.get("https://api.ipify.org?format=json", timeout=8).json()["ip"]
        data = requests.get(
            f"http://ip-api.com/json/{ip}?lang=zh-CN",
            timeout=10,
        ).json()
        if data.get("status") == "success":
            return (
                "🌐 <b>IP 查询结果</b>\n\n"
                f"IP：<code>{escape(data.get('query', ip))}</code>\n"
                f"国家：{escape(data.get('country', '-'))}\n"
                f"省份：{escape(data.get('regionName', '-'))}\n"
                f"城市：{escape(data.get('city', '-'))}\n"
                f"运营商：{escape(data.get('isp', '-'))}\n"
                f"组织：{escape(data.get('org', '-') or '-')}"
            )
        return "❌ IP 查询失败"
    except Exception as e:
        return f"❌ IP 查询出错：{escape(str(e)[:80])}"


async def lookup_ip_ipapi(ip: str | None = None) -> str:
    return await asyncio.to_thread(_ip_api_sync, ip)


def _parse_taobao_phone(text: str) -> dict:
    """解析淘宝号段接口返回（不用 eval）。"""
    m = re.search(r"\{[^{}]+\}", text)
    if not m:
        return {}
    blob = m.group(0)
    blob = blob.replace("'", '"')
    blob = re.sub(r"(\w+):", r'"\1":', blob)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        out = {}
        for key in ("province", "city", "catName", "carrier", "phoneNumber"):
            km = re.search(rf'["\']?{key}["\']?\s*:\s*["\']([^"\']+)["\']', text, re.I)
            if km:
                out[key] = km.group(1)
        return out


def _phone_sync(phone: str) -> str:
    phone = re.sub(r"\D", "", phone or "")
    if not PHONE_RE.match(phone):
        return "❌ 请输入 11 位中国大陆手机号"

    try:
        url = f"http://tcc.taobao.com/cc/json/mobile_tel_segment.htm?tel={phone}"
        resp = requests.get(url, timeout=8)
        data = _parse_taobao_phone(resp.text)
        if not data:
            return f"📱 {phone} 归属地查询失败"
        province = data.get("province") or data.get("prov") or "未知"
        city = data.get("city") or "未知"
        carrier = data.get("catName") or data.get("carrier") or "未知"
        return (
            "📱 <b>手机号查询结果</b>\n\n"
            f"号码：<code>{escape(phone)}</code>\n"
            f"省份：{escape(province)}\n"
            f"城市：{escape(city)}\n"
            f"运营商：{escape(carrier)}\n\n"
            "<i>免费版仅归属地；姓名/身份证/实名需合规付费 API（聚合数据、阿里云市场等）。</i>"
        )
    except Exception as e:
        return f"❌ 手机号查询出错：{escape(str(e)[:80])}"


async def lookup_phone(phone: str) -> str:
    return await asyncio.to_thread(_phone_sync, phone)


def _idcard_sync(idcard: str) -> str:
    idcard = (idcard or "").strip().upper()
    if not IDCARD_RE.match(idcard):
        return "❌ 身份证号码格式错误"

    try:
        area = idcard[:6]
        if len(idcard) == 18:
            birth = idcard[6:14]
            gender_digit = int(idcard[-2])
        else:
            birth = "19" + idcard[6:12]
            gender_digit = int(idcard[-1])
        year, month, day = birth[:4], birth[4:6], birth[6:8]
        gender = "男" if gender_digit % 2 == 1 else "女"
        return (
            "🆔 <b>身份证解析结果</b>\n\n"
            f"号码：<code>{escape(idcard)}</code>\n"
            f"地区码：{escape(area)}\n"
            f"出生日期：{year}年{month}月{day}日\n"
            f"性别：{gender}\n\n"
            "<i>此为号码规则解析，非公安户籍查询。</i>"
        )
    except Exception:
        return "❌ 身份证解析出错"


async def lookup_idcard(idcard: str) -> str:
    return await asyncio.to_thread(_idcard_sync, idcard)


def lookup_qq(qq: str) -> str:
    qq = (qq or "").strip()
    if not QQ_RE.match(qq):
        return "❌ QQ 号格式不正确（5–12 位数字）"
    return (
        f"🔍 <b>QQ 查询</b>：<code>{escape(qq)}</code>\n\n"
        "本 Bot 免费版不提供绑定手机/姓名反查。\n"
        "合规付费入口：聚合数据 Juhe.cn、阿里云/腾讯云市场相关接口。"
    )


def lookup_wechat(wechat: str) -> str:
    wechat = (wechat or "").strip()
    if len(wechat) < 2:
        return "❌ 微信号太短"
    return (
        f"📧 <b>微信号</b>：<code>{escape(wechat)}</code>\n\n"
        "本 Bot 免费版不提供绑定手机号反查。\n"
        "合规付费入口：聚合数据、阿里云市场、企业查询类服务。"
    )


def extract_phone(text: str) -> str | None:
    t = re.sub(r"\D", "", text or "")
    m = re.search(r"1[3-9]\d{9}", t)
    return m.group(0) if m else None


def extract_idcard(text: str) -> str | None:
    t = (text or "").strip().upper()
    for prefix in ("查身份证", "身份证", "idcard"):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix) :].lstrip("：: ").strip()
            break
    m = IDCARD_RE.search(t.replace(" ", ""))
    return m.group(0) if m else None


def extract_qq(text: str) -> str | None:
    t = (text or "").strip()
    for prefix in ("查QQ", "QQ", "qq"):
        if t.upper().startswith(prefix.upper()):
            t = t[len(prefix) :].lstrip("：: ").strip()
            break
    m = QQ_RE.search(t)
    return m.group(0) if m else None


def extract_wechat(text: str) -> str | None:
    t = (text or "").strip()
    for prefix in ("查微信", "微信", "wechat"):
        if t.startswith(prefix):
            t = t[len(prefix) :].lstrip("：: ").strip()
            break
    if 2 <= len(t) <= 32 and " " not in t:
        return t
    return None


def extract_username(text: str) -> str | None:
    t = (text or "").strip()
    for prefix in ("查用户名", "用户名", "username"):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix) :].lstrip("：: ").strip()
            break
    t = t.lstrip("@")
    if 2 <= len(t) <= 32 and re.match(r"^[\w.\-]+$", t):
        return t
    return None
