import asyncio
import json
import os
import re
import ssl
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from html import escape
from io import BytesIO
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import aiohttp
import certifi
import requests
import uvicorn
from PIL import Image, ImageDraw, ImageFont
from aiogram import BaseMiddleware, Bot, Dispatcher, F, types
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    BufferedInputFile,
    CopyTextButton,
)
from aiogram.types import Update
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from db import (
    init_db,
    get_setting,
    set_setting,
    get_admin,
    add_admin,
    remove_admin,
    get_all_admins,
    get_admin_permissions_dict,
    ADMIN_PERMISSION_DEFS,
    DEFAULT_ADMIN_PERMISSIONS,
    save_group,
    save_member,
    get_groups,
    is_operator,
    add_operator,
    remove_operator,
    get_members,
    add_transaction,
    get_last_transaction,
    add_wallet_check,
    get_wallet_checks_page,
    count_wallet_checks,
    undo_transaction,
    clear_transactions_for_range,
    get_transactions,
    set_trial_code,
    get_trial_code,
    add_access_user,
    has_access_user,
    get_access_users,
    has_claimed_free_trial,
    mark_claimed_free_trial,
    create_rental_order,
    get_rental_order,
    get_pending_rental_orders,
    get_rental_orders_by_status,
    mark_rental_order_paid,
    mark_rental_order_rejected,
    get_access_user_by_id,
    has_expiry_notice,
    add_expiry_notice,
    get_expired_access_users,
    get_access_users_page,
    count_access_users_filtered,
    approve_rental_order,
    get_db,
    save_chat_log,
    save_dm_log,
)
from hibp_client import (
    HibpError,
    is_valid_email,
    ping as hibp_ping,
    get_breaches_for_account,
    get_pastes_for_account,
    list_all_breaches,
    get_breach_by_name,
    breach_public_page_url,
)
from ipwho_client import extract_ipv4, lookup_ip, lookup_self_ip
from bank_card_client import (
    BankCardError,
    extract_card_number,
    format_bank_card_reply,
    lookup_bank_card,
)
from bank_verify_client import (
    BankVerifyError,
    format_verify_reply,
    parse_four_elements,
    verify_bank_four_elements,
)
from osint_tools_client import (
    extract_idcard,
    extract_phone,
    extract_qq,
    extract_username,
    extract_wechat,
    lookup_idcard,
    lookup_ip_ipapi,
    lookup_phone,
    lookup_qq,
    lookup_username,
    lookup_wechat,
)

# ================= ENV =================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PORT = int(os.getenv("PORT", "8080"))

BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0") or 0)
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0") or 0)

WEB_TOKEN = os.getenv("WEB_TOKEN", "").strip()
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN", "").strip()

WEB_ADMIN_NAME = os.getenv("WEB_ADMIN_NAME", "BOT 888").strip() or "BOT 888"

BOT_BASE_URL = (
    os.getenv("BOT_BASE_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or os.getenv("BASE_URL")
    or ""
).rstrip("/")

WEB_BASE_URL = (
    os.getenv("WEB_BASE_URL")
    or BOT_BASE_URL
).rstrip("/")

# 群成员能打开的网页地址（可留空，Bot 启动时会自动读取 ngrok http 8081）
PUBLIC_WEB_BASE_URL = (os.getenv("PUBLIC_WEB_BASE_URL") or "").rstrip("/")
_ngrok_public_url_cache: str | None = None

TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY", "").strip()
TRONGRID_API_URL = "https://api.trongrid.io"

PAYMENT_ADDRESS = os.getenv("PAYMENT_ADDRESS", "").strip()
PAYMENT_SUPPORT = os.getenv("PAYMENT_SUPPORT", "").strip()


def get_payment_address() -> str:
    """收款地址：优先读数据库配置，其次 .env。"""
    addr = (get_setting(-1, "payment_address", "") or "").strip()
    if addr:
        return addr
    return PAYMENT_ADDRESS

AUTO_PAY_INTERVAL = int(os.getenv("AUTO_PAY_INTERVAL", "15"))
AUTO_PAY_TX_LIMIT = int(os.getenv("AUTO_PAY_TX_LIMIT", "20"))
AUTO_PAY_TOLERANCE = float(os.getenv("AUTO_PAY_TOLERANCE", "0.0001"))

WELCOME_ENABLED = os.getenv("WELCOME_ENABLED", "1").strip() == "1"
WELCOME_TEXT = os.getenv("WELCOME_TEXT", "欢迎 {name} 加入本群。").strip()

ENV_MODE = os.getenv("ENV", "dev").lower()
IS_PRODUCTION = ENV_MODE == "prod"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in environment variables")

if not WEB_TOKEN:
    raise RuntimeError("WEB_TOKEN is missing in environment variables")

if not BOT_OWNER_ID:
    raise RuntimeError("BOT_OWNER_ID is missing — set your Telegram user ID in .env")

USE_POLLING = os.getenv("USE_POLLING", "").strip().lower() in ("1", "true", "yes")
if not USE_POLLING and BOT_BASE_URL.startswith("http://"):
    USE_POLLING = True

if not USE_POLLING and not TELEGRAM_SECRET_TOKEN:
    raise RuntimeError(
        "TELEGRAM_SECRET_TOKEN is missing — required for webhook mode. "
        "Local dev: set USE_POLLING=1 in .env"
    )

# ================= GLOBALS =================
BEIJING_TZ = ZoneInfo("Asia/Shanghai")
# 主网 USDT-TRC20 官方合约
USDT_TRC20_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
USDT_TRC20_CONTRACT_ALT = "TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj"
USDT_TRC20_CONTRACTS = frozenset({USDT_TRC20_CONTRACT, USDT_TRC20_CONTRACT_ALT})
TRON_ADDR_RE = re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b")

BOT_USERNAME = None
HTTP_SESSION = None

RATE_CACHE = {"value": None, "ts": 0}
RATE_CACHE_TTL = 30
USDT_DAILY_UPDATE_KEY = "usdt_daily_update_date"
BACKGROUND_STATUS_KEY = "background_tasks_status"

HIBP_API_KEY = os.getenv("HIBP_API_KEY", "").strip()
HIBP_USER_AGENT = os.getenv("HIBP_USER_AGENT", "PyCharmMiscProject-TelegramBot/1.0").strip()
HIBP_COOLDOWN = int(os.getenv("HIBP_COOLDOWN_SECONDS", "8") or 8)
HIBP_PAGE_SIZE = int(os.getenv("HIBP_PAGE_SIZE", "10") or 10)
_hibp_last_query: dict[int, float] = {}
_hibp_pages: dict[int, dict] = {}
_hibp_breach_lists: dict[int, list] = {}

# ================= BOT =================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(link_preview_is_disabled=True),
)
dp = Dispatcher(storage=MemoryStorage())


def _install_bot_dm_logging():
    """Bot 私聊回复写入 dm_logs（出站）。"""
    _orig_send = bot.send_message

    async def _send_message(chat_id, text=None, **kwargs):
        msg = await _orig_send(chat_id, text=text, **kwargs)
        try:
            cid = int(chat_id)
            if cid > 0:
                body = (text or kwargs.get("caption") or "（消息）").strip()
                if body:
                    save_dm_log(cid, cid, "", "Bot", "out", body)
        except Exception as e:
            print("dm log outbound error:", repr(e))
        return msg

    bot.send_message = _send_message


_install_bot_dm_logging()
BOT_USERNAME = None


class PrivateDmLogMiddleware(BaseMiddleware):
    """记录私聊消息，且不阻断后续业务 handler（aiogram 默认只执行第一个匹配的 handler）。"""

    async def __call__(self, handler, event, data):
        if isinstance(event, types.Message):
            log_private_dm_message(event)
        return await handler(event, data)


dp.message.outer_middleware(PrivateDmLogMiddleware())

HTTP_SESSION = None  # dùng chung cho aiohttp
BACKGROUND_TASKS: list[asyncio.Task] = []

TASK_DESCRIPTIONS = {
    "daily_usdt_update_loop": "每日 8:00（北京时）更新 USDT 汇率",
    "expiry_warning_loop": "权限到期提醒（每 5 分钟扫描）",
    "auto_check_payments": "USDT 自动到账对账",
    "telegram_polling": "Telegram 长轮询收消息（本地模式）",
    "background_status_persist_loop": "同步后台任务状态到数据库（供 web.py 展示）",
}

# ================= BACKGROUND TASKS =================
async def daily_usdt_update_loop():
    while True:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print("daily_usdt_update_loop error:", repr(e))
            traceback.print_exc()
            await asyncio.sleep(60)
            
async def expiry_warning_loop():
    while True:
        try:
            now_ts = int(time.time())
            rows = get_expired_access_users(now_ts)

            for user_id, username, expires_at in rows:
                notice_key = f"expired:{expires_at}"
                if has_expiry_notice(user_id, notice_key):
                    continue
                try:
                    await bot.send_message(
                        user_id,
                        "⛔ 您的使用权限已到期。\n如需继续使用，请联系管理员或自助续费。"
                    )
                except Exception as e:
                    print("expiry notify error:", repr(e))
                add_expiry_notice(user_id, notice_key)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print("expiry_warning_loop error:", repr(e))
            traceback.print_exc()

        await asyncio.sleep(300)
        
async def activate_rental_order(order_code, granted_by=None):
    try:
        return approve_rental_order(order_code, granted_by=granted_by)
    except Exception as e:
        print("activate_rental_order error:", e)
        return None, None, str(e)

async def get_usdt_in_transactions(address, limit=AUTO_PAY_TX_LIMIT):
    data = await trongrid_get(
        f"/v1/accounts/{address}/transactions/trc20",
        params={"limit": limit, "only_confirmed": "true"},
    )
    return data.get("data", []) if data else []

def parse_usdt_tx(tx):
    try:
        return {
            "to": tx.get("to"),
            "amount": float(tx.get("value", 0)) / 1_000_000,
            "txid": tx.get("transaction_id"),
        }
    except Exception:
        return None

async def auto_check_payments():
    while True:
        try:
            pay_addr = get_payment_address()
            if not pay_addr:
                await asyncio.sleep(AUTO_PAY_INTERVAL)
                continue

            orders = get_pending_rental_orders(limit=100)
            txs = await get_usdt_in_transactions(pay_addr)
            parsed = [p for p in (parse_usdt_tx(tx) for tx in txs) if p]

            used_txids = set()

            for order_code, user_id, username, full_name, category_title, plan_label, amount, created_at in orders:
                amount = float(amount)

                for tx in parsed:
                    txid = tx.get("txid")
                    if not txid or txid in used_txids:
                        continue

                    if tx.get("to") == pay_addr and abs(float(tx.get("amount", 0)) - amount) < AUTO_PAY_TOLERANCE:
                        _, new_expires_at, err = await activate_rental_order(order_code)

                        if not err:
                            used_txids.add(txid)
                            try:
                                await bot.send_message(
                                    user_id,
                                    (
                                        "✅ 自动到账\n"
                                        f"订单：<code>{order_code}</code>\n"
                                        f"金额：{amount}U\n"
                                        f"到期：{fmt_ts(new_expires_at)}"
                                    ),
                                    parse_mode="HTML",
                                )
                            except Exception as e:
                                print("notify auto paid error:", repr(e))

                            print("AUTO PAID:", order_code, txid)
                            break

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print("AUTO PAY ERROR:", repr(e))
            traceback.print_exc()

        await asyncio.sleep(AUTO_PAY_INTERVAL)

def _make_http_session() -> aiohttp.ClientSession:
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=20),
        connector=connector,
    )


# ================= APP LIFESPAN =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    global BOT_USERNAME, HTTP_SESSION

    HTTP_SESSION = _make_http_session()

    init_db()

    try:
        me = await bot.get_me()
        BOT_USERNAME = me.username
        print("Bot username:", BOT_USERNAME)
    except Exception as e:
        print("get_me error:", repr(e))
        traceback.print_exc()

    polling_task = None

    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as e:
        print("delete_webhook error:", repr(e))

    detect_ngrok_public_url(force=True)
    if USE_POLLING:
        print("本地模式：使用 Polling 收消息（无需 HTTPS）")
        link_base = get_web_base_for_links()
        if web_base_is_local(link_base):
            print(
                "⚠️ 群账单网页：请先运行「Web后台」+ 终端执行 ngrok http 8081"
            )
        else:
            print("✅ 群账单公网地址:", link_base)
        polling_task = asyncio.create_task(
            dp.start_polling(bot, handle_signals=False),
            name="telegram_polling",
        )
    elif BOT_BASE_URL.startswith("https://"):
        webhook_url = f"{BOT_BASE_URL}/webhook"
        try:
            await bot.set_webhook(
                url=webhook_url,
                secret_token=TELEGRAM_SECRET_TOKEN or None,
                drop_pending_updates=False,
            )
            print("Webhook set:", webhook_url)
        except Exception as e:
            print("set_webhook error:", repr(e))
            traceback.print_exc()
    else:
        print("未配置 HTTPS 的 BOT_BASE_URL，已跳过 Webhook")

    tasks = [
        asyncio.create_task(daily_usdt_update_loop(), name="daily_usdt_update_loop"),
        asyncio.create_task(expiry_warning_loop(), name="expiry_warning_loop"),
        asyncio.create_task(auto_check_payments(), name="auto_check_payments"),
        asyncio.create_task(background_status_persist_loop(), name="background_status_persist_loop"),
    ]
    if polling_task:
        tasks.append(polling_task)

    BACKGROUND_TASKS.clear()
    BACKGROUND_TASKS.extend(tasks)

    try:
        yield
    finally:
        if USE_POLLING:
            try:
                await dp.stop_polling()
            except Exception as e:
                print("stop_polling error:", repr(e))

        for task in tasks:
            task.cancel()
        BACKGROUND_TASKS.clear()

        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"task shutdown error {task.get_name()}:", repr(e))

        try:
            if HTTP_SESSION and not HTTP_SESSION.closed:
                await HTTP_SESSION.close()
        except Exception as e:
            print("HTTP_SESSION close error:", repr(e))

        try:
            await bot.session.close()
        except Exception as e:
            print("bot session close error:", repr(e))
            
app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root():
    return {"ok": True, "service": "vip666-1"}
    
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        if TELEGRAM_SECRET_TOKEN:
            got_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if got_secret != TELEGRAM_SECRET_TOKEN:
                print("WEBHOOK SECRET MISMATCH")
                return JSONResponse({"ok": False, "error": "secret mismatch"}, status_code=403)

        data = await request.json()
        print("WEBHOOK DATA:", json.dumps(data, ensure_ascii=False)[:2000])

        update = Update.model_validate(data)
        await dp.feed_update(bot, update)

        print("UPDATE FED OK")
        return JSONResponse({"ok": True})
    except Exception as e:
        print("WEBHOOK ERROR:", repr(e))
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
        
# ================= STATES =================
class BroadcastFSM(StatesGroup):
    waiting_content = State()
    waiting_confirm = State()

class TrialFSM(StatesGroup):
    waiting_code = State()

class AdminFSM(StatesGroup):
    waiting_add_admin = State()
    waiting_del_admin = State()
    waiting_trial_code = State()

class AddressQueryFSM(StatesGroup):
    waiting_address = State()


class ToolFSM(StatesGroup):
    waiting_email = State()
    waiting_ip = State()
    waiting_bank_card = State()
    waiting_bank_verify = State()
    waiting_username = State()
    waiting_phone = State()
    waiting_idcard = State()
    waiting_qq = State()
    waiting_wechat = State()


TOOL_QUERY_PROMPTS = {
    "username": "👤 请发送要查询的<b>用户名</b>（可带 @）：\n例：<code>查用户名 jack</code>",
    "phone": "📱 请发送<b>11 位手机号</b>：\n例：<code>查手机号 13800138000</code>",
    "idcard": "🆔 请发送<b>身份证号</b>：\n例：<code>查身份证 110101199001011234</code>",
    "qq": "🔍 请发送<b>QQ 号</b>：\n例：<code>查QQ 123456789</code>",
    "wechat": "📧 请发送<b>微信号</b>：\n例：<code>查微信 wxid_xxx</code>",
    "ip": "🌐 请发送要查询的 IP；只发「查IP」则查本机出口 IP",
}


# ================= BASIC HELPERS =================
@dp.message(lambda message: message.text and message.text.lower() == "ping")
async def ping_test(message: types.Message):
    print("PING TEST RECEIVED:", message.text)
    await message.answer("pong")
    
def is_cmd(message: types.Message, *cmds):
    if not message or not message.text:
        return False
    head = message.text.strip().split()[0].lower()
    head = head.split("@")[0]
    return head in [c.lower() for c in cmds]

def is_group_message(message: types.Message):
    return bool(message and message.chat and message.chat.type in ("group", "supergroup"))

def is_private(message: types.Message):
    return bool(message and message.chat and message.chat.type == "private")

def should_ignore_message(m: types.Message):
    return (not m or not m.from_user or m.from_user.is_bot or not m.text)

def fmt_num(x):
    if x is None:
        return "0"
    try:
        x = float(x)
        if abs(x - int(x)) < 1e-9:
            return str(int(x))
        return f"{x:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(x)

def fmt_ts(ts):
    if not ts:
        return "-"
    try:
        ts = int(ts)
        if ts > 10_000_000_000:
            ts = ts // 1000
        return datetime.fromtimestamp(ts, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "-"

def get_chat_setting(chat_id, key, default=None):
    v = get_setting(chat_id, key, None)
    if v is None and chat_id != -1:
        v = get_setting(-1, key, None)
    return v if v is not None else default

def set_chat_setting(chat_id, key, value):
    set_setting(chat_id, key, value)

def message_to_log_text(m: types.Message) -> str:
    if m.text:
        return m.text.strip()
    if m.caption:
        return f"[{m.content_type}] {m.caption.strip()}"
    if m.content_type and m.content_type != "text":
        return f"[{m.content_type}]"
    return ""


def log_private_dm_message(m: types.Message):
    if not is_private(m) or not m.from_user:
        return
    text = message_to_log_text(m)
    if not text and m.content_type == "text":
        return
    if not text:
        text = f"[{m.content_type or 'message'}]"
    try:
        peer_id = int(m.chat.id)
        if m.from_user.is_bot:
            save_dm_log(
                peer_id,
                peer_id,
                "",
                "Bot",
                "out",
                text,
            )
        else:
            save_dm_log(
                peer_id,
                m.from_user.id,
                m.from_user.username or "",
                m.from_user.full_name or "",
                "in",
                text,
            )
    except Exception as e:
        print("save_dm_log error:", repr(e))


def ensure_group(m: types.Message):
    if is_group_message(m):
        save_group(m.chat.id, m.chat.title or "Unnamed group")
        if m.from_user and not m.from_user.is_bot:
            save_member(
                m.chat.id,
                m.from_user.id,
                m.from_user.username or "",
                m.from_user.full_name or "",
            )
            if m.text and not m.text.startswith("/"):
                try:
                    save_chat_log(
                        m.chat.id,
                        m.from_user.id,
                        m.from_user.username or "",
                        m.from_user.full_name or "",
                        m.text,
                    )
                except Exception as e:
                    print("save_chat_log error:", repr(e))

@app.head("/")
def home_head():
    return {"ok": True}
    
def get_rate(chat_id):
    try:
        return float(get_chat_setting(chat_id, "rate", "190"))
    except Exception:
        return 190.0

def get_fee(chat_id):
    try:
        return float(get_chat_setting(chat_id, "fee", "7"))
    except Exception:
        return 7.0

def get_enabled(chat_id):
    return str(get_chat_setting(chat_id, "enabled", "1")) == "1"

def is_bot_owner(user_id):
    return bool(BOT_OWNER_ID and int(user_id) == int(BOT_OWNER_ID))

def is_super_admin(user_id):
    return bool(SUPER_ADMIN_ID and int(user_id) == int(SUPER_ADMIN_ID))

def get_user_role(user_id):
    if is_bot_owner(user_id):
        return "owner"
    if is_super_admin(user_id):
        return "super"

    role = get_admin(user_id)
    if role == "super":
        return "super"
    if role == "admin":
        return "admin"
    return None

def has_permission(user_id, perm: str) -> bool:
    if is_bot_owner(user_id) or is_super_admin(user_id):
        return True
    role = get_user_role(user_id)
    if role == "super":
        return True
    if role == "admin":
        return bool(get_admin_permissions_dict(user_id).get(perm))
    return False

def can_use_manage_panel(user_id):
    return has_permission(user_id, "panel")

def can_use_bot_ops(user_id):
    return has_permission(user_id, "bot_ops")

def can_view_transaction_history(user_id):
    return has_permission(user_id, "history")

def can_manage_codes(user_id):
    return has_permission(user_id, "codes")

def can_manage_admins(user_id):
    return is_bot_owner(user_id) or is_super_admin(user_id)

def deny_text():
    return "❌ 无权限"

def has_bot_access(user_id):
    return get_user_role(user_id) in ("owner", "super", "admin") or has_access_user(user_id)

def is_admin_or_operator(chat_id, user: types.User | None):
    if not user:
        return False
    if can_use_bot_ops(user.id):
        return True
    return is_operator(chat_id, user_id=user.id, username=user.username or "")

def is_tron_address(addr: str):
    if not addr:
        return False
    return bool(re.fullmatch(r"T[1-9A-HJ-NP-Za-km-z]{33}", addr.strip()))

def extract_tron_address(text: str):
    if not text:
        return None
    m = TRON_ADDR_RE.search(text.strip())
    return m.group(0) if m else None

async def send_long_text(chat_id, text, reply_markup=None, parse_mode="HTML"):
    text = text or ""
    max_len = 3500

    if len(text) <= max_len:
        return await bot.send_message(
            chat_id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    parts = []
    buf = ""
    for line in text.splitlines(True):
        if len(buf) + len(line) > max_len:
            if buf:
                parts.append(buf)
            buf = line
        else:
            buf += line

    if buf:
        parts.append(buf)

    for i, part in enumerate(parts):
        await bot.send_message(
            chat_id,
            part,
            reply_markup=reply_markup if i == len(parts) - 1 else None,
            parse_mode=parse_mode,
        )

def extract_username_only(text: str):
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("@"):
        text = text[1:].strip()
    if re.fullmatch(r"[A-Za-z0-9_]{4,}", text or ""):
        return text.lower()
    return None


def find_member_by_username(chat_id, username: str):
    username = (username or "").strip().lower()
    if not username:
        return None

    members = get_members(chat_id) or []
    for m in members:
        try:
            if isinstance(m, dict):
                mid = int(m.get("user_id") or 0)
                mun = (m.get("username") or "").strip().lower()
                mname = (m.get("full_name") or "").strip()
            else:
                mid = int(m[1])
                mun = (m[2] or "").strip().lower()
                mname = (m[3] or "").strip()

            if mun == username:
                return {
                    "user_id": mid,
                    "username": mun,
                    "full_name": mname,
                }
        except Exception:
            continue

    return None

def day_range(ts=None):
    dt = datetime.now(BEIJING_TZ) if ts is None else datetime.fromtimestamp(int(ts), BEIJING_TZ)
    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return int(start.timestamp()), int(end.timestamp())

def month_range(offset_months=0):
    now = datetime.now(BEIJING_TZ)
    year = now.year
    month = now.month - offset_months

    while month <= 0:
        month += 12
        year -= 1

    start = datetime(year, month, 1, tzinfo=BEIJING_TZ)
    if month == 12:
        nxt = datetime(year + 1, 1, 1, tzinfo=BEIJING_TZ)
    else:
        nxt = datetime(year, month + 1, 1, tzinfo=BEIJING_TZ)

    end = nxt - timedelta(seconds=1)
    return int(start.timestamp()), int(end.timestamp())

def build_vip_welcome_text(display_name, username="", user_id=None, activator_name=None):
    safe_name = escape(display_name or "User")
    safe_username = f"@{escape(username)}" if username else "未设置"
    safe_user_id = str(user_id or "-")

    lines = [
        "╔══════════════════════╗",
        f"   💎 <b>VIP {safe_name}</b> 💎",
        "╚══════════════════════╝",
        "",
        f"👤 客户：{safe_username}",
        f"🆔 ID：<code>{safe_user_id}</code>",
        "🏆 等级：VIP",
        "⚡ 状态：已开通VIP",
    ]

    if activator_name:
        lines.append(f"🔐 开通人：<b>{escape(activator_name)}</b>")

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "✨ 服务已就绪",
        "请选择下方功能 👇",
    ])

    return "\n".join(lines)

def build_normal_welcome_text(display_name, username="", user_id=None):
    safe_name = escape(display_name or "User")
    safe_username = f"@{escape(username)}" if username else "未设置"
    safe_user_id = str(user_id or "-")

    lines = [
        "╔══════════════════════╗",
        f"   🌟 <b>普通用户 {safe_name}</b> 🌟",
        "╚══════════════════════╝",
        "",
        f"👤 客户：{safe_username}",
        f"🆔 ID：<code>{safe_user_id}</code>",
        "🏆 等级：普通用户",
        "⚡ 状态：未开通",
        "",
        "━━━━━━━━━━━━━━━━━━",
        "✨ 当前账号尚未激活",
        "请先申请试用或输入续费码开通 👇",
    ]

    return "\n".join(lines)

async def get_activator_name(granted_by):
    if not granted_by:
        return None

    try:
        granted_by = int(granted_by)
    except Exception:
        return None

    if BOT_OWNER_ID and granted_by == BOT_OWNER_ID:
        return "Bot Owner"

    if SUPER_ADMIN_ID and granted_by == SUPER_ADMIN_ID:
        return "Super Admin"

    admin_role = get_admin(granted_by)
    if admin_role:
        try:
            chat = await bot.get_chat(granted_by)
            return chat.full_name or chat.username or f"Admin {granted_by}"
        except Exception:
            return f"Admin {granted_by}"

    try:
        chat = await bot.get_chat(granted_by)
        return chat.full_name or chat.username or str(granted_by)
    except Exception:
        return str(granted_by)
        
# ================= AMOUNT PARSER =================
def parse_amount_expr(expr, chat_id, default_direct_unit=False):
    if not expr:
        return None

    expr = expr.strip().replace(" ", "")
    if not expr:
        return None

    body = expr[1:] if expr[0] in "+-" else expr
    body = body.strip()
    if not body:
        return None

    rate_default = get_rate(chat_id)
    fee_default = get_fee(chat_id)

    # Ví dụ: 777u
    if body.lower().endswith("u"):
        num = body[:-1]
        try:
            unit_amount = abs(float(num))
            return {
                "raw_amount": None,
                "unit_amount": unit_amount,
                "rate_used": rate_default,
                "fee_used": 0.0,
            }
        except Exception:
            return None

    # Ví dụ: 1000/7.8
    if "/" in body:
        try:
            raw_s, rate_s = body.split("/", 1)
            raw_amount = abs(float(raw_s))
            rate_used = float(rate_s)
            if rate_used == 0:
                return None

            fee_used = fee_default
            unit_amount = raw_amount / rate_used * (1 - fee_used / 100.0)

            return {
                "raw_amount": raw_amount,
                "unit_amount": unit_amount,
                "rate_used": rate_used,
                "fee_used": fee_used,
            }
        except Exception:
            return None

    # Ví dụ: 100*1.2
    if "*" in body:
        try:
            left_s, right_s = body.split("*", 1)
            raw_amount = abs(float(left_s))
            factor = float(right_s)
            unit_amount = raw_amount * factor

            return {
                "raw_amount": raw_amount,
                "unit_amount": unit_amount,
                "rate_used": factor,
                "fee_used": 0.0,
            }
        except Exception:
            return None

    # Ví dụ: 1000
    try:
        val = abs(float(body))
    except Exception:
        return None

    if default_direct_unit:
        return {
            "raw_amount": None,
            "unit_amount": val,
            "rate_used": rate_default,
            "fee_used": 0.0,
        }

    unit_amount = val / rate_default * (1 - fee_default / 100.0)
    return {
        "raw_amount": val,
        "unit_amount": unit_amount,
        "rate_used": rate_default,
        "fee_used": fee_default,
    }

# ================= UI =================
def menu_kb(user_id=None):
    keyboard = [
        [KeyboardButton(text="🔥 开始记账")],
        [
            KeyboardButton(text="💎 申请试用"),
            KeyboardButton(text="📝 使用说明"),
        ],
        [
            KeyboardButton(text="📈 实时U价"),
            KeyboardButton(text="🔍 地址查询"),
        ],
        [KeyboardButton(text="🔑 自助续费")],
        [KeyboardButton(text="🔒 泄露自查")],
    ]

    if user_id is None or can_view_transaction_history(user_id):
        keyboard[-2].append(KeyboardButton(text="📜 交易历史"))

    if user_id is not None and can_use_manage_panel(user_id):
        keyboard.append([KeyboardButton(text="🛠 管理面板")])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=False,
    )

def start_inline_kb(user_id=None):
    if BOT_USERNAME:
        add_url = f"https://t.me/{BOT_USERNAME}?startgroup=add"
    else:
        add_url = "https://t.me/"

    buttons = [
        [InlineKeyboardButton(text="➕ 添加机器人到群", url=add_url)],
        [InlineKeyboardButton(text="📝 使用说明", callback_data="menu:help")],
    ]

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def copy_cmd_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 复制：开始", copy_text=CopyTextButton(text="开始")),
            InlineKeyboardButton(text="📋 复制：总账单", copy_text=CopyTextButton(text="总账单")),
        ],
        [
            InlineKeyboardButton(text="📋 复制：设置汇率190", copy_text=CopyTextButton(text="设置汇率190")),
            InlineKeyboardButton(text="📋 复制：设置费率7", copy_text=CopyTextButton(text="设置费率7")),
        ],
        [
            InlineKeyboardButton(text="📋 复制：地址查询", copy_text=CopyTextButton(text="地址查询")),
            InlineKeyboardButton(text="📋 复制：撤销", copy_text=CopyTextButton(text="撤销")),
        ],
        [
            InlineKeyboardButton(text="📋 复制：群发广播", copy_text=CopyTextButton(text="群发广播")),
            InlineKeyboardButton(text="📋 复制：使用说明", copy_text=CopyTextButton(text="使用说明")),
        ],
    ])

def begin_copy_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📋 开始", callback_data="copy:开始"),
            InlineKeyboardButton(text="📋 关闭记账", callback_data="copy:关闭记账"),
        ],
        [
            InlineKeyboardButton(text="📋 设置汇率190", callback_data="copy:设置汇率190"),
            InlineKeyboardButton(text="📋 设置费率7", callback_data="copy:设置费率7"),
        ],
        [
            InlineKeyboardButton(text="📋 +1000", callback_data="copy:+1000"),
            InlineKeyboardButton(text="📋 -1000", callback_data="copy:-1000"),
        ],
        [
            InlineKeyboardButton(text="📋 下发5000", callback_data="copy:下发5000"),
            InlineKeyboardButton(text="📋 P+2000", callback_data="copy:P+2000"),
        ],
        [
            InlineKeyboardButton(text="📋 总账单", callback_data="copy:总账单"),
            InlineKeyboardButton(text="📋 撤销", callback_data="copy:撤销"),
        ],
    ])

def manage_panel_kb(user_id):
    rows = []

    if can_manage_admins(user_id):
        rows.append([
            InlineKeyboardButton(text="➕ 添加管理员", callback_data="manage:add_admin"),
            InlineKeyboardButton(text="➖ 删除管理员", callback_data="manage:del_admin"),
        ])

    if can_use_manage_panel(user_id):
        rows.append([
            InlineKeyboardButton(text="📋 管理员列表", callback_data="manage:list_admin"),
        ])
        rows.append([
            InlineKeyboardButton(text="🧾 待支付订单", callback_data="order:list_pending"),
            InlineKeyboardButton(text="📦 订单历史", callback_data="order:history:all"),
        ])

    if can_manage_codes(user_id):
        rows.append([
            InlineKeyboardButton(text="🔑 创建续费码", callback_data="manage:create_code"),
            InlineKeyboardButton(text="🗑 回收续费码", callback_data="manage:revoke_code"),
        ])

    if not rows:
        rows = [[InlineKeyboardButton(text="❌ 无权限", callback_data="noop")]]

    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_tools_panel_kb() -> InlineKeyboardMarkup:
    """管理员查询工具面板（内联按钮）。"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 查用户名", callback_data="tool:username"),
            InlineKeyboardButton(text="📱 查手机号", callback_data="tool:phone"),
        ],
        [
            InlineKeyboardButton(text="🆔 查身份证", callback_data="tool:idcard"),
            InlineKeyboardButton(text="🌐 查IP", callback_data="tool:ip"),
        ],
        [
            InlineKeyboardButton(text="🔍 查QQ", callback_data="tool:qq"),
            InlineKeyboardButton(text="📧 查微信", callback_data="tool:wechat"),
        ],
        [
            InlineKeyboardButton(text="📧 查邮", callback_data="tool:email"),
            InlineKeyboardButton(text="📡 本机IP", callback_data="tool:myip"),
        ],
        [
            InlineKeyboardButton(text="🏦 银行卡", callback_data="tool:bankcard"),
            InlineKeyboardButton(text="✅ 四要素", callback_data="tool:bankverify"),
        ],
        [
            InlineKeyboardButton(text="📚 Breaches", callback_data="tool:breaches"),
            InlineKeyboardButton(text="📋 Dataclasses", callback_data="tool:dataclasses"),
        ],
        [
            InlineKeyboardButton(text="❓ 使用说明", callback_data="tool:help"),
        ],
    ])


def tools_reply_kb() -> ReplyKeyboardMarkup:
    """左侧快捷键盘（显示菜单后）。"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="查用户名"), KeyboardButton(text="查手机号")],
            [KeyboardButton(text="查身份证"), KeyboardButton(text="查IP")],
            [KeyboardButton(text="查邮"), KeyboardButton(text="本机 IP")],
            [KeyboardButton(text="银行卡查询"), KeyboardButton(text="显示菜单")],
            [KeyboardButton(text="隐藏菜单")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def tools_panel_help_text() -> str:
    return (
        "🧭 <b>管理员查询面板说明</b>\n\n"
        "<b>快捷命令</b>\n"
        "• <code>查用户名 jack</code> — 多平台公开账号（Sherlock）\n"
        "• <code>查手机号 138xxxx</code> — 归属地/运营商\n"
        "• <code>查身份证 号码</code> — 地区码/生日/性别解析\n"
        "• <code>查QQ 123456</code> / <code>查微信 wxid</code> — 说明与付费入口\n"
        "• <code>查IP</code> 或 <code>查IP 8.8.8.8</code> — IP 归属\n"
        "• <code>本机 IP</code> — Bot 出口公网 IP（ipwho）\n"
        "• <code>查邮 email</code> — HIBP 邮箱泄露自查\n"
        "• <code>查卡 卡号</code> — 银行卡开户行\n"
        "• <code>四要素核验</code> — 银行卡四要素（需 APPCODE）\n\n"
        "• 打开面板：<code>显示菜单</code> / <code>/panel</code> / <code>管理面板</code>\n"
        "• 隐藏键盘：「隐藏菜单」\n"
        "• HIBP 测试：<code>/hibp_ping</code>\n\n"
        "<i>QQ/微信深度信息需合规付费 API；本 Bot 不提供社工库反查。</i>"
    )


def format_ipwho_result(data: dict, note: str = "") -> str:
    ip = data.get("ip", "-")
    country = data.get("country") or ""
    region = data.get("region") or ""
    city = data.get("city") or ""
    loc = " ".join(x for x in [country, region, city] if x).strip() or "-"

    conn = data.get("connection") if isinstance(data.get("connection"), dict) else {}
    isp = data.get("isp") or conn.get("isp") or "-"
    org = data.get("org") or conn.get("org") or isp
    asn = data.get("asn") or conn.get("asn") or "-"

    tz_obj = data.get("timezone")
    if isinstance(tz_obj, dict):
        tz = tz_obj.get("id") or "-"
        local_time = tz_obj.get("current_time") or "-"
    else:
        tz = str(tz_obj or "-")
        local_time = "-"

    lat = data.get("latitude")
    lon = data.get("longitude")
    coord = f"{lat}, {lon}" if lat is not None and lon is not None else "-"
    maps = f"https://maps.google.com/?q={lat},{lon}" if lat is not None and lon is not None else "-"

    head = "🌍 <b>IP 定位结果</b>"
    if note:
        head += f"\n<i>{escape(note)}</i>"

    return (
        f"{head}\n\n"
        f"IP：<code>{escape(str(ip))}</code>\n"
        f"位置：{escape(str(loc))}\n"
        f"运营商/组织：{escape(str(org))}\n"
        f"ASN：<code>{escape(str(asn))}</code>\n"
        f"时区：{escape(str(tz))}\n"
        f"当地时间：{escape(str(local_time))}\n"
        f"坐标：{escape(str(coord))}\n"
        f"地图：{escape(maps)}\n"
        f"来源：ipwho.is"
    )


MY_IP_NOTE = (
    "这是 Bot 所在网络的公网 IP。"
    "若你在自己电脑用 PyCharm 运行 Bot，通常就是你的宽带出口 IP；"
    "若在云服务器运行，则是机房 IP。"
    "Telegram 无法直接读取你手机的 IP。"
)


async def tools_lookup_ip(message: types.Message, ip: str, note: str = ""):
    if HTTP_SESSION is None:
        return await message.reply("服务未就绪")
    try:
        data = await lookup_ip(HTTP_SESSION, ip)
        await message.reply(
            format_ipwho_result(data, note=note),
            parse_mode="HTML",
            disable_web_page_preview=False,
        )
    except Exception as e:
        await message.reply(f"❌ IP 查询失败：{escape(str(e))}", parse_mode="HTML")


async def tools_lookup_my_ip(message: types.Message):
    if HTTP_SESSION is None:
        return await message.reply("服务未就绪")
    await message.reply("⏳ 正在查询本机公网 IP…")
    try:
        data = await lookup_self_ip(HTTP_SESSION)
        await message.reply(
            format_ipwho_result(data, note=MY_IP_NOTE),
            parse_mode="HTML",
            disable_web_page_preview=False,
        )
    except Exception as e:
        await message.reply(f"❌ 查询失败：{escape(str(e))}", parse_mode="HTML")


def is_my_ip_cmd(text: str) -> bool:
    t = (text or "").strip().lower().replace(" ", "")
    return t in (
        "本机ip",
        "查本机ip",
        "本机ip查询",
        "我的ip",
        "myip",
        "本机ip地址",
    )


def detect_ngrok_public_url(force: bool = False) -> str:
    """读取本机 ngrok 控制台 (4040)，仅当隧道指向 8081 时返回 https 地址。"""
    global _ngrok_public_url_cache
    if not force and _ngrok_public_url_cache is not None:
        return _ngrok_public_url_cache

    _ngrok_public_url_cache = ""
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as resp:
            data = json.loads(resp.read().decode())
        for t in data.get("tunnels") or []:
            public = (t.get("public_url") or "").strip()
            addr = (t.get("config") or {}).get("addr") or ""
            if not public.startswith("https://"):
                continue
            if ":8081" in addr or "localhost:8081" in addr or "127.0.0.1:8081" in addr:
                _ngrok_public_url_cache = public.rstrip("/")
                return _ngrok_public_url_cache
            if ":80" in addr:
                print(
                    "⚠️ ngrok 正指向 80 端口（网页在 8081），群链接会失败。"
                    "请关掉 ngrok 后执行: ngrok http 8081"
                )
    except Exception:
        pass
    return _ngrok_public_url_cache


def get_web_base_for_links() -> str:
    """群账单按钮用的网页根地址：优先 ngrok(8081)，其次 PUBLIC_WEB_BASE_URL。"""
    ngrok_url = detect_ngrok_public_url()
    if ngrok_url:
        return ngrok_url
    pub = (PUBLIC_WEB_BASE_URL or "").rstrip("/")
    if pub and not _ngrok_public_url_is_stale_port80(pub):
        return pub
    return (WEB_BASE_URL or "").rstrip("/")


def _ngrok_public_url_is_stale_port80(url: str) -> bool:
    """云端固定域名可能绑在 80；本机未开 80 时不要使用该 PUBLIC 配置。"""
    try:
        import urllib.request

        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as resp:
            data = json.loads(resp.read().decode())
        for t in data.get("tunnels") or []:
            if (t.get("public_url") or "").rstrip("/") == url.rstrip("/"):
                addr = (t.get("config") or {}).get("addr") or ""
                return ":80" in addr and ":8081" not in addr
    except Exception:
        pass
    return False


def web_base_is_local(base: str) -> bool:
    if not base:
        return True
    b = base.lower()
    return any(x in b for x in ("127.0.0.1", "localhost", "0.0.0.0", "[::1]"))


def build_web_group_url(chat_id: int, date: str | None = None) -> str:
    """生成群账单只读链接（不含全站 WEB_TOKEN，无法进管理后台）。"""
    from view_token import make_group_view_token

    today = date or datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    base = get_web_base_for_links()
    view_tok = make_group_view_token(chat_id)
    params = urlencode({"date": today, "token": view_tok})
    return f"{base}/group/{chat_id}?{params}"


def report_kb(chat_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧾 查看本群账单", callback_data=f"bill:view:{chat_id}")],
    ])

def history_groups_kb():
    groups = get_groups()
    if not groups:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="暂无群组（请先把 Bot 加进群）", callback_data="noop")]
        ])

    base = get_web_base_for_links()
    public_ok = bool(base) and not web_base_is_local(base)
    rows = []

    for row in groups:
        chat_id, title = row[0], row[1] if len(row) > 1 else ""
        if public_ok:
            rows.append([
                InlineKeyboardButton(text=f"📂 {title}", url=build_web_group_url(chat_id))
            ])
        else:
            rows.append([
                InlineKeyboardButton(text=f"📂 {title}", callback_data=f"hist:web:{chat_id}")
            ])

    if not public_ok:
        rows.append([
            InlineKeyboardButton(text="ℹ️ 群成员打不开？点这里看说明", callback_data="bill:web_hint")
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

def order_history_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 全部订单", callback_data="order:history:all")],
        [
            InlineKeyboardButton(text="⏳ 待支付", callback_data="order:history:pending"),
            InlineKeyboardButton(text="✅ 已支付", callback_data="order:history:paid"),
        ],
        [InlineKeyboardButton(text="❌ 已拒绝", callback_data="order:history:rejected")],
    ])

def address_result_kb(address, page=1):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📜 链上交易记录", callback_data=f"addr:tx:{address}:{page}"),
        ],
        [
            InlineKeyboardButton(text="🔄 重新查询", callback_data="addr:again"),
            InlineKeyboardButton(text="⬅️ 返回菜单", callback_data="addr:back"),
        ],
    ])

def tx_history_kb(address, page=1):
    buttons = []
    if page > 1:
        buttons.append(
            InlineKeyboardButton(
                text="⬅️ 上一页",
                callback_data=f"addr:tx:{address}:{page-1}"
            )
        )

    buttons.append(
        InlineKeyboardButton(
            text=f"📄 第 {page} 页",
            callback_data="noop"
        )
    )

    buttons.append(
        InlineKeyboardButton(
            text="下一页 ➡️",
            callback_data=f"addr:tx:{address}:{page+1}"
        )
    )

    return InlineKeyboardMarkup(inline_keyboard=[buttons])

# ================= RENT UI =================
RENT_CATEGORIES = {
    "group_admin": {"title": "🤖 Bot quản trị nhóm"},
    "computer": {"title": "💻 Bot máy tính"},
    "translator": {"title": "🌐 Bot dịch thuật"},
}

RENT_PLANS = {
    "1m": {"label": "一个月", "amount": 100},
    "3m": {"label": "三个月", "amount": 230},
    "6m": {"label": "六个月", "amount": 400},
    "1y": {"label": "一年", "amount": 700},
}

def rent_main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Bot quản trị nhóm", callback_data="rent:group_admin")],
        [InlineKeyboardButton(text="💻 Bot máy tính", callback_data="rent:computer")],
        [InlineKeyboardButton(text="🌐 Bot dịch thuật", callback_data="rent:translator")],
    ])


def rent_address_block() -> str:
    addr = get_payment_address()
    if not addr:
        return "⚠️ <b>收款地址暂未配置</b>，请联系管理员。"
    support = f"\n🗣️ 客服：<code>{escape(PAYMENT_SUPPORT)}</code>" if PAYMENT_SUPPORT else ""
    return (
        "🌿 <b>收款地址（TRC20-USDT）</b>\n"
        f"<code>{escape(addr)}</code>"
        f"{support}\n"
        "请转账到以上地址，并务必按订单金额支付。"
    )


def rent_main_text() -> str:
    return (
        "🔑 <b>自助续费</b>\n\n"
        f"{rent_address_block()}\n\n"
        "请选择要租用的机器人类型："
    )


def rent_plan_kb(category_key):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="一个月 (100U)", callback_data=f"rent:plan:{category_key}:1m")],
        [InlineKeyboardButton(text="三个月 (230U)", callback_data=f"rent:plan:{category_key}:3m")],
        [InlineKeyboardButton(text="六个月 (400U)", callback_data=f"rent:plan:{category_key}:6m")],
        [InlineKeyboardButton(text="一年 (700U)", callback_data=f"rent:plan:{category_key}:1y")],
        [InlineKeyboardButton(text="⬅️ 返回", callback_data="rent:main")],
    ])

def rent_payment_text(category_key, plan_key, order_code):
    cat = RENT_CATEGORIES.get(category_key, {})
    plan = RENT_PLANS.get(plan_key, {})

    title = escape(cat.get("title", "套餐"))
    plan_label = escape(plan.get("label", ""))
    amount = plan.get("amount", 0)
    addr = get_payment_address()

    addr_line = (
        f"<code>{escape(addr)}</code>"
        if addr
        else "<i>未配置，请联系管理员</i>"
    )
    support_line = (
        f"\n🗣️ 在线客服：<code>{escape(PAYMENT_SUPPORT)}</code>"
        if PAYMENT_SUPPORT
        else ""
    )

    return (
        f"✅ <b>{title}</b>\n"
        f"📦 套餐：<b>{plan_label}</b>\n"
        f"🧾 订单号：<code>{escape(str(order_code))}</code>\n\n"
        f"🌿 <b>收款地址：TRC20-USDT</b>\n"
        f"├ 💰 订单金额：<b>{amount} U</b>\n"
        f"└➤ {addr_line}\n\n"
        f"请向以上地址转账 <b>{amount} U</b>，付款后等待审核开通。"
        f"{support_line}"
    )


def rent_payment_kb(amount):
    addr = get_payment_address()
    row1 = []
    if addr:
        row1.append(
            InlineKeyboardButton(
                text="📋 复制收款地址",
                copy_text=CopyTextButton(text=addr),
            )
        )
    row1.append(
        InlineKeyboardButton(text=f"📋 复制金额 {amount}U", callback_data=f"copy:{amount}")
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        row1,
        [
            InlineKeyboardButton(text="⬅️ 返回套餐", callback_data="rent:main"),
            InlineKeyboardButton(text="🔄 重新选择", callback_data="rent:back"),
        ],
    ])

# ================= TEXTS =================
def help_text():
    return (
        "📚 记账机器人使用说明\n\n"
        "【基础功能】\n"
        "• 开始记账：开始 / 🔥 开始记账\n"
        "• 停止记账：关闭记账 / 停止记账\n"
        "• 打开发言：上课\n"
        "• 停止发言：下课\n\n"
        "【参数设置】\n"
        "• 设置汇率：设置汇率190\n"
        "• 设置费率：设置费率7\n\n"
        "【记账指令】\n"
        "• +1000 / -1000\n"
        "• +1000/7.8 / -1000/7.8\n"
        "• +7777u / -7777u\n"
        "• 下发5000 / 下发1000R\n"
        "• P+2000 / P-1000\n"
        "• +1000 备注\n\n"
        "【查看功能】\n"
        "• 总账单\n"
        "• 账单\n"
        "• /我\n"
        "• 撤销\n"
        "• 删除账单：（清空本群今日账单）\n"
        "• 回复某人消息发送「添加操作人」可授权本群记账\n"
        "• 私聊「泄露查询 你的邮箱」自查是否在已知泄露中（HIBP）\n"
        "• 上个月总账单\n\n"
        "【试用与续费】\n"
        "• 首次可领取 24 小时免费试用权限\n"
        "• 到期后可输入管理员发放的续费码\n"
        "• 或使用自助续费菜单提交租用订单\n"
    )

def begin_help_text():
    return (
        "🔥 <b>开始记账</b>\n\n"
        "请先将机器人添加到群聊，并授予必要权限。\n\n"
        "<b>常用命令</b>\n"
        "• <code>开始</code>\n"
        "• <code>关闭记账</code>\n"
        "• <code>设置汇率190</code>\n"
        "• <code>设置费率7</code>\n"
        "• <code>+1000</code>\n"
        "• <code>-1000</code>\n"
        "• <code>下发5000</code>\n"
        "• <code>P+2000</code>\n"
        "• <code>总账单</code>\n"
        "• <code>撤销</code>\n"
        "• <code>删除账单：</code>\n"
    )

def address_query_text():
    return (
        "🔍 <b>地址查询</b>\n\n"
        "请直接发送 TRON 地址进行查询。\n\n"
        "<b>示例：</b>\n"
        "<code>Txxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</code>（示例格式）"
    )

def group_feature_text():
    return (
        "👥 <b>分组功能说明</b>\n\n"
        "支持以下用法：\n"
        "• 直接记账：<code>+1000</code>\n"
        "• 指定目标：<code>张三+1000</code>\n"
        "• 回复某人消息后输入：<code>+1000</code>\n"
        "• 下发：<code>下发5000</code>\n"
        "• 寄存：<code>P+2000</code>"
    )

def extract_username_only(text: str):
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("@"):
        text = text[1:].strip()
    if re.fullmatch(r"[A-Za-z0-9_]{4,}", text or ""):
        return text.lower()
    return None


def find_member_by_username(chat_id, username: str):
    username = (username or "").strip().lower()
    if not username:
        return None

    members = get_members(chat_id) or []
    for m in members:
        try:
            if isinstance(m, dict):
                mid = int(m.get("user_id") or 0)
                mun = (m.get("username") or "").strip().lower()
                mname = (m.get("full_name") or "").strip()
            else:
                mid = int(m[1])
                mun = (m[2] or "").strip().lower()
                mname = (m[3] or "").strip()

            if mun == username:
                return {
                    "user_id": mid,
                    "username": mun,
                    "full_name": mname,
                }
        except Exception:
            continue

    return None


@dp.message(lambda m: is_group_message(m), AdminFSM.waiting_add_admin)
async def process_add_admin(message: types.Message, state: FSMContext):
    if should_ignore_message(message):
        return

    if not can_manage_admins(message.from_user.id):
        await message.answer("❌ 无权限")
        await state.clear()
        return

    ensure_group(message)

    username = extract_username_only(message.text or "")
    if not username:
        await message.answer(
            "请发送要添加的操作员用户名。\n\n"
            "格式：@username\n\n"
            "例如：@abc123"
        )
        return

    target = find_member_by_username(message.chat.id, username)
    if not target:
        await message.answer(
            "❌ 未找到该用户。\n\n"
            "请确认：\n"
            "1. 对方已经在群里发过言\n"
            "2. 用户名输入正确\n"
            "3. 格式必须是 @username"
        )
        return

    target_id = int(target["user_id"])
    target_username = target.get("username") or ""
    target_name = target.get("full_name") or ""

    add_admin(target_id, "admin")
    await state.clear()

    await message.answer(
        "✅ 已添加操作员\n"
        f"用户名：@{escape(target_username)}\n"
        f"姓名：{escape(target_name) if target_name else '未设置'}\n"
        f"ID：<code>{target_id}</code>\n\n"
        "现在对方可以使用机器人的操作功能。",
        parse_mode="HTML",
    )


@dp.message(lambda m: is_group_message(m), AdminFSM.waiting_del_admin)
async def process_del_admin(message: types.Message, state: FSMContext):
    if should_ignore_message(message):
        return

    if not can_manage_admins(message.from_user.id):
        await message.answer("❌ 无权限")
        await state.clear()
        return

    ensure_group(message)

    username = extract_username_only(message.text or "")
    if not username:
        await message.answer(
            "请发送要删除的操作员用户名。\n\n"
            "格式：@username\n\n"
            "例如：@abc123"
        )
        return

    target = find_member_by_username(message.chat.id, username)
    if not target:
        await message.answer(
            "❌ 未找到该用户。\n\n"
            "请确认用户名正确，且对方曾在本群发言。"
        )
        return

    target_id = int(target["user_id"])
    remove_admin(target_id)
    await state.clear()

    await message.answer(
        f"✅ 已删除操作员\n用户名：@{escape(target.get('username') or username)}\nID：<code>{target_id}</code>",
        parse_mode="HTML",
    )
    
# ================= REPORT HELPERS =================
def split_target_prefix(text):
    t = (text or "").strip()
    markers = ["下发", "P+", "P-", "+", "-"]
    for mk in markers:
        pos = t.find(mk)
        if pos > 0:
            target = t[:pos].strip()
            body = t[pos:].strip()
            if target:
                return target, body
    return None, t

def format_tx_line(tx):
    (
        tx_id, chat_id, user_id, username, display_name, target_name, kind,
        raw_amount, unit_amount, rate_used, fee_used, note, original_text,
        created_at, undone
    ) = tx

    tm = datetime.fromtimestamp(created_at, BEIJING_TZ).strftime("%H:%M:%S")
    safe_target = escape(target_name) if target_name else ""
    safe_note = escape(note) if note else ""

    if kind == "reserve":
        line = f"{tm} {fmt_num(unit_amount)}U"
        if safe_target:
            line += f" {safe_target}"
        if safe_note:
            line += f" {safe_note}"
        return line.strip()

    if raw_amount is not None:
        if fee_used:
            line = f"{tm} {fmt_num(raw_amount)} / {fmt_num(rate_used)} * ({1 - fee_used/100:.2f}) = {fmt_num(unit_amount)}U"
        else:
            line = f"{tm} {fmt_num(raw_amount)} / {fmt_num(rate_used)} = {fmt_num(unit_amount)}U"
    else:
        line = f"{tm} {fmt_num(unit_amount)}U"

    extra = []
    if safe_target:
        extra.append(safe_target)
    if safe_note:
        extra.append(safe_note)

    if extra:
        line += " " + " ".join(extra)

    return line.strip()

def summarize_transactions(txs):
    income = [t for t in txs if t[6] == "income"]
    payout = [t for t in txs if t[6] == "payout"]
    reserve = [t for t in txs if t[6] == "reserve"]

    total_income_unit = sum((t[8] or 0) for t in income)
    total_payout_unit = sum((t[8] or 0) for t in payout)
    total_reserve_unit = sum((t[8] or 0) for t in reserve)

    due = total_income_unit + total_reserve_unit
    paid = total_payout_unit
    pending = due - paid

    total_raw_income = sum((abs(t[7]) or 0) for t in income if t[7] is not None)

    return {
        "income_count": len(income),
        "payout_count": len(payout),
        "reserve_count": len(reserve),
        "total_income_unit": total_income_unit,
        "total_payout_unit": total_payout_unit,
        "total_reserve_unit": total_reserve_unit,
        "due": due,
        "paid": paid,
        "pending": pending,
        "total_raw_income": total_raw_income,
    }

def report_text(chat_id, start_ts, end_ts, title="账单", user_id=None, display_name=None):
    txs = get_transactions(chat_id, start_ts=start_ts, end_ts=end_ts, user_id=user_id)
    stats = summarize_transactions(txs)

    income_txs = [t for t in txs if t[6] == "income"]
    payout_txs = [t for t in txs if t[6] == "payout"]
    reserve_txs = [t for t in txs if t[6] == "reserve"]

    lines = [f"📘 <b>{escape(title)}</b>"]
    if display_name:
        lines.append(f"👤 用户：{escape(display_name)}")

    lines.append("")
    lines.append(f"🟢 <b>入款（{len(income_txs)}笔）</b>")
    if income_txs:
        for tx in income_txs:
            lines.append(format_tx_line(tx))
    else:
        lines.append("暂无入款")

    lines.append("")
    lines.append(f"🔵 <b>下发（{len(payout_txs)}笔）</b>")
    if payout_txs:
        for tx in payout_txs:
            lines.append(format_tx_line(tx))
    else:
        lines.append("暂无下发")

    if reserve_txs:
        lines.append("")
        lines.append(f"🟣 <b>寄存（{len(reserve_txs)}笔）</b>")
        for tx in reserve_txs:
            lines.append(format_tx_line(tx))

    # Thêm lại phần 分组统计
    if user_id is None:
        lines.append("")
        lines.append("📂 <b>分组统计</b>")
        group_map = {}

        for tx in income_txs:
            key = escape(tx[5] or "未命名")
            group_map.setdefault(key, 0.0)
            group_map[key] += float(tx[8] or 0)

        if group_map:
            for k, v in group_map.items():
                lines.append(f"{k} 入:{fmt_num(v)}U")
        else:
            lines.append("暂无分组数据")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"💰 总入款：{fmt_num(stats['total_raw_income'])} ({fmt_num(stats['total_income_unit'])}U)")
    lines.append(f"📈 汇率：{fmt_num(get_rate(chat_id))}")
    lines.append(f"📉 交易费率：{fmt_num(get_fee(chat_id))}%")
    lines.append("")
    lines.append(f"📦 应下发：{fmt_num(stats['due'])}U")
    lines.append(f"✅ 已下发：{fmt_num(stats['paid'])}U")
    lines.append(f"⏳ 未下发：{fmt_num(stats['pending'])}U")

    return "\n".join(lines)

# ================= TRON API =================
async def trongrid_get(path, params=None):
    headers = {
        "accept": "application/json",
        "user-agent": "Mozilla/5.0",
    }
    if TRONGRID_API_KEY:
        headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY

    url = path if path.startswith("http") else f"{TRONGRID_API_URL}{path}"

    if HTTP_SESSION is None:
        return {}

    async with HTTP_SESSION.get(url, params=params, headers=headers) as resp:
        if resp.status != 200:
            return {}
        return await resp.json()

def _pick_account(payload):
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("data"), list) and payload["data"]:
        return payload["data"][0]
    if payload.get("address"):
        return payload
    if isinstance(payload.get("data"), dict):
        return payload["data"]
    return None


def _raw_to_usdt_amount(raw, decimals=6):
    if raw is None:
        return None
    try:
        decimals = int(decimals or 6)
    except Exception:
        decimals = 6
    try:
        val = float(raw)
        if val > 1_000_000:
            return val / (10 ** decimals)
        return val
    except Exception:
        return None


def _is_usdt_token(sym: str, contract: str) -> bool:
    sym = (sym or "").upper()
    contract = (contract or "").strip()
    return sym == "USDT" or contract in USDT_TRC20_CONTRACTS


def _parse_trc20_usdt(account):
    if not isinstance(account, dict):
        return None

    trc20 = account.get("trc20")
    if isinstance(trc20, list):
        for entry in trc20:
            if isinstance(entry, dict):
                for contract, balance in entry.items():
                    if str(contract) in USDT_TRC20_CONTRACTS:
                        amt = _raw_to_usdt_amount(balance)
                        if amt is not None:
                            return amt

    candidates = [
        "trc20token_balances",
        "trc20",
        "tokenBalances",
        "tokens",
        "withPriceTokens",
        "assetV2",
    ]

    for key in candidates:
        items = account.get(key)
        if not items:
            continue

        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue

            sym = str(
                item.get("tokenAbbr")
                or item.get("symbol")
                or item.get("tokenName")
                or item.get("name")
                or ""
            ).upper()

            contract = str(
                item.get("contract_address")
                or item.get("tokenAddress")
                or item.get("tokenId")
                or item.get("contract")
                or item.get("token_id")
                or ""
            )

            if _is_usdt_token(sym, contract):
                raw = (
                    item.get("balance")
                    or item.get("value")
                    or item.get("amount")
                    or item.get("tokenValue")
                )
                amt = _raw_to_usdt_amount(
                    raw,
                    item.get("precision") or item.get("decimals") or 6,
                )
                if amt is not None:
                    return amt
    return None


def _parse_usdt_from_token_list(items):
    if not items:
        return None
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return None

    for item in items:
        if not isinstance(item, dict):
            continue
        sym = str(
            item.get("tokenAbbr")
            or item.get("symbol")
            or item.get("token_name")
            or item.get("name")
            or ""
        ).upper()
        contract = str(
            item.get("tokenId")
            or item.get("token_id")
            or item.get("contract_address")
            or item.get("tokenAddress")
            or item.get("contract")
            or ""
        )
        if _is_usdt_token(sym, contract):
            amt = _raw_to_usdt_amount(
                item.get("balance") or item.get("value") or item.get("amount"),
                item.get("decimals") or item.get("precision") or 6,
            )
            if amt is not None:
                return amt
    return None


async def fetch_trc20_usdt_balance(address: str):
    """单独拉取 TRC20 USDT 余额（账户接口常不含 token 列表）。"""
    for contract in USDT_TRC20_CONTRACTS:
        data = await trongrid_get(
            f"/v1/accounts/{address}/trc20/balance",
            params={"contract_address": contract},
        )
        if isinstance(data, dict):
            items = data.get("data")
            if isinstance(items, list) and items:
                parsed = _parse_usdt_from_token_list(items)
                if parsed is not None:
                    return parsed
            if data.get("balance") is not None:
                amt = _raw_to_usdt_amount(data.get("balance"))
                if amt is not None:
                    return amt

    for path in (
        f"/v1/accounts/{address}/assets/trc20",
        f"/v1/accounts/{address}/tokens",
    ):
        data = await trongrid_get(path, params={"limit": 200})
        if isinstance(data, dict):
            items = data.get("data")
            parsed = _parse_usdt_from_token_list(items)
            if parsed is not None:
                return parsed

    try:
        url = f"https://apilist.tronscanapi.com/api/account/tokens?address={address}&start=0&limit=50"
        headers = {"accept": "application/json", "user-agent": "Mozilla/5.0"}
        if TRONGRID_API_KEY:
            headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY
        if HTTP_SESSION:
            async with HTTP_SESSION.get(url, headers=headers) as resp:
                if resp.status == 200:
                    payload = await resp.json()
                    items = payload.get("data") or payload.get("tokens") or []
                    parsed = _parse_usdt_from_token_list(items)
                    if parsed is not None:
                        return parsed
    except Exception as e:
        print("tronscan usdt fetch error:", e)

    return None

async def check_tron_address(address: str):
    def _fetch():
        headers = {
            "accept": "application/json",
            "user-agent": "Mozilla/5.0",
        }
        if TRONGRID_API_KEY:
            headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY

        sources = [
            f"https://api.trongrid.io/v1/accounts/{address}",
            f"https://apilist.tronscanapi.com/api/account?address={address}",
        ]

        for url in sources:
            try:
                r = requests.get(
                    url, timeout=12, headers=headers, verify=certifi.where()
                )
                if not r.ok:
                    continue
                payload = r.json()
                acc = _pick_account(payload)
                if acc:
                    source_name = "trongrid" if "trongrid" in url else "tronscan"
                    return {"source": source_name, "account": acc}
            except Exception as e:
                print("wallet api error:", url, e)

        return None

    result = await asyncio.to_thread(_fetch)
    if not result:
        payload = await trongrid_get(f"/v1/accounts/{address}")
        acc = _pick_account(payload)
        if not acc:
            return None
        result = {"source": "trongrid", "account": acc}

    acc = result["account"]

    trx_balance = None
    try:
        if acc.get("balance") is not None:
            trx_balance = float(acc.get("balance")) / 1_000_000
    except Exception as e:
        print("trx_balance parse error:", e)

    usdt_balance = _parse_trc20_usdt(acc)
    if usdt_balance is None:
        usdt_balance = await fetch_trc20_usdt_balance(address)

    tx_count = (
        acc.get("transaction_count")
        or acc.get("txCount")
        or acc.get("transactionsCount")
        or acc.get("totalTransactionCount")
        or acc.get("trxCount")
        or None
    )
    try:
        tx_count = int(tx_count) if tx_count is not None else None
    except Exception:
        tx_count = None

    create_time = (
        acc.get("create_time")
        or acc.get("createTime")
        or acc.get("create_time_ms")
        or acc.get("createTimeMs")
    )
    latest_time = (
        acc.get("latest_opration_time")
        or acc.get("latestOperationTime")
        or acc.get("latest_operation_time")
        or acc.get("latest_tx_time")
    )

    return {
        "source": result["source"],
        "address": address,
        "trx_balance": trx_balance,
        "usdt_balance": usdt_balance,
        "tx_count": tx_count,
        "create_time": create_time,
        "latest_time": latest_time,
        "raw": acc,
    }


async def get_tron_transactions(address, page=1, page_size=10):
    offset = max(0, (page - 1) * page_size)

    tx_data = await trongrid_get(
        f"/v1/accounts/{address}/transactions",
        params={
            "limit": page_size,
            "only_confirmed": "true",
            "order_by": "block_timestamp,desc",
            "offset": offset,
        },
    )

    return tx_data.get("data", []) if tx_data else []


def format_tron_tx_row(tx):
    try:
        ts = tx.get("block_timestamp")
        dt = datetime.fromtimestamp(ts / 1000, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
        txid = tx.get("txID", "-")
        contract = tx.get("raw_data", {}).get("contract", [])
        tx_type = "-"
        if contract:
            tx_type = contract[0].get("type", "-")

        return f"• {dt} | {escape(tx_type)}\n  <code>{escape(txid)}</code>"
    except Exception:
        return "• 无法解析交易"


def format_address_info_text(address, info, sender_name=None, user_send_count=None):
    if not info:
        return (
            f"🔎 <b>地址查询结果</b>\n\n"
            f"📌 地址：<code>{escape(address)}</code>\n"
            "⚠️ 无法获取链上数据，请稍后重试。"
        )

    trx_balance = info.get("trx_balance", 0)
    usdt_balance = info.get("usdt_balance", 0)
    tx_count = info.get("tx_count", 0)
    first_tx = info.get("create_time") or "-"
    last_active = info.get("latest_time") or "-"
    sig_status = "已签名地址" if (tx_count or 0) > 0 else "新钱包 / 未签名地址"

    lines = [
        "🔎 <b>TRON 地址查询</b>",
        "",
    ]

    if sender_name:
        lines.append(f"👤 查询人：<code>{escape(sender_name)}</code>")

    if user_send_count is not None:
        lines.append(f"📌 本群发送次数：<b>{user_send_count}</b> 次")

    lines.extend([
        f"📌 地址：<code>{escape(address)}</code>",
        f"💰 TRX：<b>{fmt_num(trx_balance)}</b>",
        f"💰 USDT：<b>{fmt_num(usdt_balance)}</b>",
        f"📊 交易次数：<b>{tx_count if tx_count is not None else 0}</b>",
        f"🔰 状态：<b>{sig_status}</b>",
        f"📡 数据来源：<b>{escape(str(info.get('source', '-')))}</b>",
        f"⏰ 首次交易：<b>{fmt_ts(first_tx)}</b>",
        f"🌟 最后活跃：<b>{fmt_ts(last_active)}</b>",
    ])

    return "\n".join(lines)

def make_wallet_card_image(
    address,
    sender_name,
    trx_balance=None,
    usdt_balance=None,
    tx_count=None,
    source="trongrid",
    create_time=None,
    latest_time=None,
):
    width, height = 1080, 1350

    top_green = (18, 185, 150)
    top_green2 = (16, 165, 138)
    body_bg = (20, 30, 44)
    panel_bg = (26, 40, 58)
    panel_bg2 = (30, 46, 66)
    white = (245, 248, 250)
    mute = (165, 180, 190)
    gold = (245, 198, 76)
    blue = (120, 185, 255)
    green = (100, 235, 160)
    red = (255, 120, 120)

    img = Image.new("RGB", (width, height), body_bg)
    draw = ImageDraw.Draw(img)

    for y in range(height):
        if y < 330:
            r = int(top_green[0] * (1 - y / 330) + top_green2[0] * (y / 330))
            g = int(top_green[1] * (1 - y / 330) + top_green2[1] * (y / 330))
            b = int(top_green[2] * (1 - y / 330) + top_green2[2] * (y / 330))
            draw.line([(0, y), (width, y)], fill=(r, g, b))
        else:
            draw.line([(0, y), (width, y)], fill=body_bg)

    font_candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKSC-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    def load_font(size):
        for fp in font_candidates:
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
        return ImageFont.load_default()

    font_title = load_font(54)
    font_sub = load_font(28)
    font_mid = load_font(32)

    def box(x1, y1, x2, y2, radius=26, fill=panel_bg, outline=None, width=2):
        draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=fill, outline=outline, width=width)

    def text(x, y, s, font, fill=white):
        draw.text((x, y), str(s), font=font, fill=fill)

    def center_text(y, s, font, fill=white):
        bbox = draw.textbbox((0, 0), str(s), font=font)
        w = bbox[2] - bbox[0]
        x = (width - w) // 2
        draw.text((x, y), str(s), font=font, fill=fill)

    def fmt_time_local(ts):
        if not ts:
            return "N/A"
        try:
            ts = int(ts)
            if ts > 10_000_000_000:
                ts = ts // 1000
            return datetime.fromtimestamp(ts, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "N/A"

    box(40, 35, 1040, 300, radius=36, fill=top_green2, outline=(255, 255, 255, 40), width=2)
    box(90, 198, 990, 250, radius=18, fill=(60, 130, 108), outline=(220, 255, 240), width=2)
    center_text(70, "USDT防篡改验证核对", font_title, fill=white)
    center_text(144, "《请双方谨慎核对地址是否与图中一致，如有误停止付款》", font_sub, fill=(232, 247, 242))
    center_text(209, address, font_mid, fill=white)
    center_text(258, f"查询人: {sender_name}", font_sub, fill=(225, 245, 240))

    box(40, 330, 1040, 1140, radius=34, fill=panel_bg, outline=(42, 70, 90), width=2)
    text(70, 360, "🔎 查询地址：", font_mid, fill=white)
    text(250, 360, address, font_mid, fill=blue)

    box(60, 460, 1020, 1030, radius=28, fill=panel_bg2, outline=(55, 90, 110), width=2)
    tx_status = "已签名地址" if (tx_count or 0) > 0 else "未签名地址"
    tx_status_color = green if (tx_count or 0) > 0 else red

    rows = [
        ("💡 交易次数", str(tx_count if tx_count is not None else "N/A"), white),
        ("⏰ 首次交易", fmt_time_local(create_time), white),
        ("🌟 最后活跃", fmt_time_local(latest_time), white),
        ("🛡 签名状态", tx_status, tx_status_color),
        ("💰 USDT 余额", f"{fmt_num(usdt_balance)} USDT", gold),
        ("💰 TRX 余额", f"{fmt_num(trx_balance)} TRX", gold),
        ("📡 数据来源", str(source), mute),
    ]

    y = 500
    gap = 70
    for label, value, value_color in rows:
        text(85, y, f"{label}：", font_mid, fill=white)
        text(330, y, value, font_mid, fill=value_color)
        y += gap

    bio = BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return BufferedInputFile(bio.read(), filename="usdt_check_cn.png")
    
# ================= USDT RATE =================
async def fetch_usdt_rates():
    urls = [
        "https://open.er-api.com/v6/latest/USD",
        "https://api.exchangerate.host/latest?base=USD&symbols=CNY,VND",
    ]

    if HTTP_SESSION is None:
        return None

    for url in urls:
        try:
            async with HTTP_SESSION.get(url) as resp:
                data = await resp.json()

                if data.get("result") == "success" and "rates" in data:
                    rates = data["rates"]
                    return {
                        "usd_cny": float(rates.get("CNY")) if rates.get("CNY") else None,
                        "usd_vnd": float(rates.get("VND")) if rates.get("VND") else None,
                    }

                rates = data.get("rates", {})
                return {
                    "usd_cny": float(rates.get("CNY")) if rates.get("CNY") else None,
                    "usd_vnd": float(rates.get("VND")) if rates.get("VND") else None,
                }
        except Exception as e:
            print("fetch_usdt_rates error:", e)

    return None

def format_usdt_rate_text(rates):
    now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    cny = rates.get("usd_cny") if rates else None
    vnd = rates.get("usd_vnd") if rates else None

    lines = ["📈 <b>实时U价</b>", ""]

    if cny:
        lines.append(f"🇨🇳 市场价：<code>{cny:.4f}</code> CNY / USDT")
        lines.append(f"• 1 CNY ≈ <code>{1/cny:.4f}</code> USDT")
    else:
        lines.append("🇨🇳 市场价：<i>获取失败</i>")

    if vnd:
        lines.append(f"🇻🇳 市场价：<code>{vnd:,.0f}</code> VND / USDT")
        lines.append(f"• 1 VND ≈ <code>{1/vnd:.8f}</code> USDT")
    else:
        lines.append("🇻🇳 市场价：<i>获取失败</i>")

    lines += ["", f"🕒 更新时间：<code>{now_str}</code>"]
    return "\n".join(lines)

def rate_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 刷新价格", callback_data="rate:refresh")],
        [InlineKeyboardButton(text="📝 使用说明", callback_data="menu:help")],
    ])

async def get_usdt_rates_cached(force=False):
    now = time.time()
    if not force and RATE_CACHE["value"] and (now - RATE_CACHE["ts"] < RATE_CACHE_TTL):
        return RATE_CACHE["value"]

    rates = await fetch_usdt_rates()
    if rates:
        RATE_CACHE["value"] = rates
        RATE_CACHE["ts"] = now
        return rates
    return RATE_CACHE["value"]

async def daily_usdt_update_loop():
    while True:
        try:
            now = datetime.now(BEIJING_TZ)
            today_key = now.strftime("%Y-%m-%d")
            target_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
            last_update_date = get_setting(-1, USDT_DAILY_UPDATE_KEY, "")

            if now >= target_time and last_update_date != today_key:
                rates = await fetch_usdt_rates()
                if rates:
                    RATE_CACHE["value"] = rates
                    RATE_CACHE["ts"] = time.time()
                    set_setting(-1, USDT_DAILY_UPDATE_KEY, today_key)
                    print(f"[USDT] Updated at {now.strftime('%Y-%m-%d %H:%M:%S')} Beijing time")

            if now < target_time:
                sleep_seconds = (target_time - now).total_seconds()
                await asyncio.sleep(min(sleep_seconds, 60))
            else:
                await asyncio.sleep(60)
        except Exception as e:
            print("daily_usdt_update_loop error:", e)
            await asyncio.sleep(60)

# ================= RENEW / EXPIRY =================
def plan_duration_seconds(plan_key):
    if plan_key == "1m":
        return 30 * 24 * 60 * 60
    if plan_key == "3m":
        return 90 * 24 * 60 * 60
    if plan_key == "6m":
        return 180 * 24 * 60 * 60
    if plan_key == "1y":
        return 365 * 24 * 60 * 60
    return 30 * 24 * 60 * 60

def calc_renew_expire_at(user_id, plan_key):
    now_ts = int(time.time())
    duration = plan_duration_seconds(plan_key)

    access_row = get_access_user_by_id(user_id)
    current_exp = None
    if access_row and len(access_row) >= 5:
        current_exp = access_row[4]

    base_ts = now_ts
    if current_exp and int(current_exp) > now_ts:
        base_ts = int(current_exp)

    return base_ts + duration

async def activate_rental_order(order_code, granted_by=None):
    row = get_rental_order(order_code)
    if not row:
        return None, None, "订单不存在"

    (
        order_code, user_id, username, full_name, category_key, category_title,
        plan_key, plan_label, amount, status, created_at, paid_at, expires_at, note
    ) = row

    if status == "paid":
        return row, expires_at, "订单已支付"

    new_expires_at = calc_renew_expire_at(user_id, plan_key)

    mark_rental_order_paid(order_code, expires_at=new_expires_at)
    add_access_user(
        user_id=user_id,
        username=username or "",
        granted_by=granted_by,
        expires_at=new_expires_at,
    )

    return row, new_expires_at, None

async def expiry_warning_loop():
    while True:
        try:
            now_ts = int(time.time())
            rows = get_access_users()

            for row in rows:
                user_id, username, granted_by, granted_at, expires_at = row

                if not expires_at:
                    continue

                expires_at = int(expires_at)
                remain = expires_at - now_ts

                if remain <= 0:
                    notice_key = "expired"
                    if not has_expiry_notice(user_id, notice_key):
                        add_expiry_notice(user_id, notice_key)
                        try:
                            await bot.send_message(
                                user_id,
                                "⏳ 您的使用权限已到期，请尽快续费。",
                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                    [InlineKeyboardButton(text="🔑 立即续费", callback_data="rent:main")]
                                ]),
                            )
                        except Exception as e:
                            print("expired notify failed:", e)
                    continue

                warning_map = [
                    (7 * 24 * 3600, "7d", "7 天"),
                    (3 * 24 * 3600, "3d", "3 天"),
                    (1 * 24 * 3600, "1d", "1 天"),
                    (1 * 3600, "1h", "1 小时"),
                ]

                for threshold, key, label in warning_map:
                    if remain <= threshold and remain > threshold - 3600:
                        notice_key = f"warn_{key}"
                        if not has_expiry_notice(user_id, notice_key):
                            add_expiry_notice(user_id, notice_key)
                            try:
                                expire_str = datetime.fromtimestamp(expires_at, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
                                await bot.send_message(
                                    user_id,
                                    (
                                        f"⚠️ 您的权限将在 <b>{label}</b> 后到期。\n\n"
                                        f"到期时间：<code>{expire_str}</code>\n"
                                        "请及时续费。"
                                    ),
                                    parse_mode="HTML",
                                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                        [InlineKeyboardButton(text="🔑 立即续费", callback_data="rent:main")]
                                    ]),
                                )
                            except Exception as e:
                                print("warn notify failed:", e)
                        break

        except Exception as e:
            print("expiry_warning_loop error:", e)

        await asyncio.sleep(300)

# ================= COMMON CALLBACKS =================
@dp.callback_query(lambda c: c.data == "noop")
async def noop_cb(c: types.CallbackQuery):
    await c.answer()


def _bill_web_hint_text(chat_id: int | None = None) -> str:
    lines = [
        "📌 <b>为什么群成员打不开账单网页？</b>",
        "",
        "当前 <code>WEB_BASE_URL</code> 是 <b>本机地址</b>（127.0.0.1），",
        "只有您在自己电脑的浏览器能打开；群里其他人用手机点链接会失败。",
        "",
        "<b>要让群成员也能打开，请：</b>",
        "1️⃣ PyCharm 同时运行 <b>「运行 Web后台」</b>（web.py，端口 8081）",
        "2️⃣ 安装并运行 ngrok（或其它隧道）：",
        "   <code>ngrok http 8081</code>",
        "3️⃣ 在 .env 增加一行（把地址换成 ngrok 给的 https）：",
        "   <code>PUBLIC_WEB_BASE_URL=https://xxxx.ngrok-free.app</code>",
        "4️⃣ 重启 Bot",
        "",
        "云数据库（Neon）已在后台工作，与网页地址无关。",
    ]
    if chat_id and WEB_BASE_URL and web_base_is_local(WEB_BASE_URL):
        local_url = build_web_group_url(chat_id)
        lines.extend(["", "🖥 <b>仅您本机测试可打开：</b>", f"<code>{local_url}</code>"])
    return "\n".join(lines)


@dp.callback_query(lambda c: c.data == "bill:web_hint")
async def bill_web_hint_cb(c: types.CallbackQuery):
    if not c.message:
        return await c.answer()
    chat_id = c.message.chat.id if c.message.chat else None
    await c.message.answer(_bill_web_hint_text(chat_id), parse_mode="HTML")
    await c.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("hist:web:"))
async def hist_web_group_cb(c: types.CallbackQuery):
    if not c.message or not c.from_user:
        return await c.answer()
    if not can_view_transaction_history(c.from_user.id):
        return await c.answer("无权限", show_alert=True)
    try:
        chat_id = int(c.data.split(":")[2])
    except (IndexError, ValueError):
        return await c.answer("无效群组", show_alert=True)

    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
    local = build_web_group_url(chat_id, date=today)
    pub = get_web_base_for_links()
    lines = [
        f"📂 <b>群组 {chat_id}</b> 账单网页",
        "",
        f"🖥 <b>本机打开：</b>\n<code>{local}</code>",
    ]
    if pub and not web_base_is_local(pub):
        lines.append(f"\n🌐 <b>公网打开：</b>\n<code>{build_web_group_url(chat_id)}</code>")
    else:
        lines.append("\n⚠️ 公网未就绪：请运行 web.py + <code>ngrok http 8081</code>")
    await c.message.answer("\n".join(lines), parse_mode="HTML")
    await c.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("bill:web:"))
async def bill_web_chat_cb(c: types.CallbackQuery):
    if not c.message:
        return await c.answer()
    try:
        chat_id = int(c.data.split(":")[2])
    except (IndexError, ValueError):
        return await c.answer("无效群组", show_alert=True)
    await c.message.answer(_bill_web_hint_text(chat_id), parse_mode="HTML")
    await c.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("bill:view:"))
async def bill_view_in_chat_cb(c: types.CallbackQuery):
    """在群内发送今日账单，不打开网页。"""
    if not c.message or not c.from_user:
        return await c.answer()
    if not is_group_message(c.message):
        return await c.answer("请在群内使用", show_alert=True)
    try:
        chat_id = int(c.data.split(":")[2])
    except (IndexError, ValueError):
        return await c.answer("无效群组", show_alert=True)
    if chat_id != c.message.chat.id:
        chat_id = c.message.chat.id

    start_ts, end_ts = day_range()
    text = report_text(chat_id, start_ts, end_ts, title="今日账单")
    await send_long_text(
        c.message.chat.id,
        text,
        reply_markup=report_kb(chat_id),
    )
    await c.answer()


@dp.callback_query(lambda c: c.data and c.data.startswith("copy:"))
async def copy_cb(c: types.CallbackQuery):
    if not c.message:
        return await c.answer()

    text = c.data.split(":", 1)[1]
    await c.message.answer(f"📋 请复制：\n<code>{text}</code>", parse_mode="HTML")
    await c.answer("已发送可复制文本")

@dp.callback_query(lambda c: c.data == "menu:help")
async def menu_help_cb(c: types.CallbackQuery):
    if not c.message:
        return
    await c.message.answer(help_text(), parse_mode="HTML", reply_markup=menu_kb(c.from_user.id if c.from_user else None))
    await c.answer()

@dp.callback_query(lambda c: c.data == "menu:copy")
async def menu_copy_cb(c: types.CallbackQuery):
    if not c.message:
        return
    await c.answer()

# ================= START / PRIVATE MENU =================
@dp.message(lambda m: is_private(m) and m.text and is_cmd(m, "/start"))
async def start_cmd(m: types.Message):
    custom_text = get_setting(-1, "start_text")
    is_vip = has_bot_access(m.from_user.id)

    activator_name = None
    if is_vip:
        access_row = get_access_user_by_id(m.from_user.id)
        if access_row and len(access_row) >= 3:
            activator_name = await get_activator_name(access_row[2])

    if custom_text:
        text = custom_text
    elif is_vip:
        text = build_vip_welcome_text(
            display_name=m.from_user.full_name or "User",
            username=m.from_user.username or "",
            user_id=m.from_user.id,
            activator_name=activator_name,
        )
    else:
        text = build_normal_welcome_text(
            display_name=m.from_user.full_name or "User",
            username=m.from_user.username or "",
            user_id=m.from_user.id,
        )

    await m.answer(text, reply_markup=menu_kb(m.from_user.id), parse_mode="HTML")
    await m.answer("📋 常用命令复制区：", reply_markup=copy_cmd_kb())
    await m.answer("👇 你也可以从这里开始：", reply_markup=start_inline_kb(m.from_user.id))

    
@dp.message(lambda m: is_private(m) and m.text in ("🔥 开始记账", "开始记账", "开始"))
async def menu_begin(m: types.Message):
   await m.answer(begin_help_text(), parse_mode="HTML")

@dp.message(lambda m: is_private(m) and ((m.text in ("📝 使用说明", "使用说明")) or is_cmd(m, "/help")))
async def menu_help(m: types.Message):
    await m.reply(help_text(), reply_markup=menu_kb(m.from_user.id), parse_mode="HTML")

@dp.message(lambda m: is_private(m) and m.text in ("📋 复制命令", "复制命令"))
async def menu_copy(m: types.Message):
    await m.reply("📋 常用命令复制区：", reply_markup=copy_cmd_kb())

@dp.message(lambda m: is_private(m) and m.text in ("👥 分组功能", "分组功能"))
async def group_feature_menu(m: types.Message):
    await m.answer(group_feature_text(), parse_mode="HTML")

# ================= TRIAL / ACCESS =================
@dp.message(lambda m: is_private(m) and m.text in ("💎 申请试用", "申请试用"))
async def menu_trial(m: types.Message, state: FSMContext):
    if can_manage_codes(m.from_user.id):
        return await m.answer("🛠 管理员快捷面板", reply_markup=manage_panel_kb(m.from_user.id))

    if has_bot_access(m.from_user.id):
        return await m.reply("✅ 您已拥有使用权限。")

    if not has_claimed_free_trial(m.from_user.id):
        expires_at = int(time.time()) + 24 * 60 * 60
        add_access_user(
            user_id=m.from_user.id,
            username=m.from_user.username or "",
            granted_by=None,
            expires_at=expires_at,
        )
        mark_claimed_free_trial(m.from_user.id)
        return await m.reply(
            "✅ 您已获得 24 小时免费试用权限。\n"
            "到期后请输入管理员发放的续费码，或使用自助续费。"
        )

    await state.set_state(TrialFSM.waiting_code)
    await m.reply(
        "⏳ 您的免费试用已用过或已到期。\n\n"
        "请输入管理员发送的续费码继续使用。"
    )

@dp.message(TrialFSM.waiting_code)
async def receive_trial_redeem_code(m: types.Message, state: FSMContext):
    if not m.text:
        return

    code = m.text.strip()
    real_code = (get_trial_code() or "").strip()

    if not real_code:
        return await m.reply("❌ 当前未设置续费码，请联系管理员。")

    if code != real_code:
        return await m.reply("❌ 续费码错误，请重试。")

    add_access_user(
        user_id=m.from_user.id,
        username=m.from_user.username or "",
        granted_by=None,
        expires_at=None,
    )

    await state.clear()
    await m.reply("✅ 续费成功，您已获得长期使用权限。")

# ================= GROUP CONTROL =================
@dp.message(lambda m: is_group_message(m) and (m.text in ("开始", "开始记账", "开启记账", "🔥 开始记账")))
async def start_accounting(m: types.Message):
    ensure_group(m)
    if not can_use_bot_ops(m.from_user.id):
        return await m.reply(deny_text())

    set_chat_setting(m.chat.id, "enabled", "1")
    await m.reply("✅ 记账已开启！")

@dp.message(lambda m: is_group_message(m) and m.text in ("关闭记账", "停止记账"))
async def stop_accounting(m: types.Message):
    ensure_group(m)
    if not can_use_bot_ops(m.from_user.id):
        return await m.reply(deny_text())

    set_chat_setting(m.chat.id, "enabled", "0")
    await m.reply("⛔ 记账已关闭！")

@dp.message(lambda m: is_group_message(m) and m.text in ("上课", "下课"))
async def group_permission_cmd(m: types.Message):
    ensure_group(m)
    if not can_use_bot_ops(m.from_user.id):
        return await m.reply(deny_text())

    try:
        if m.text == "上课":
            await bot.set_chat_permissions(
                m.chat.id,
                permissions=types.ChatPermissions(can_send_messages=True),
            )
            await m.reply("✅ 已开启发言")
        else:
            await bot.set_chat_permissions(
                m.chat.id,
                permissions=types.ChatPermissions(can_send_messages=False),
            )
            await m.reply("✅ 已禁言")
    except Exception as e:
        await m.reply("❌ 机器人没有权限修改群权限")
        print("group_permission_cmd error:", e)

@dp.message(lambda m: is_group_message(m) and bool(re.match(r"^设置汇率\s*-?\d+(\.\d+)?$", (m.text or "").strip())))
async def set_rate_cmd(m: types.Message):
    ensure_group(m)
    if not can_use_bot_ops(m.from_user.id):
        return await m.reply(deny_text())

    num = re.findall(r"-?\d+(?:\.\d+)?", m.text or "")
    if not num:
        return await m.reply("❌ 格式错误")
    set_chat_setting(m.chat.id, "rate", num[0])
    await m.reply(f"✅ 汇率已设置为 {num[0]}")

@dp.message(lambda m: is_group_message(m) and bool(re.match(r"^设置费率\s*-?\d+(\.\d+)?$", (m.text or "").strip())))
async def set_fee_cmd(m: types.Message):
    ensure_group(m)
    if not can_use_bot_ops(m.from_user.id):
        return await m.reply(deny_text())

    num = re.findall(r"-?\d+(?:\.\d+)?", m.text or "")
    if not num:
        return await m.reply("❌ 格式错误")
    set_chat_setting(m.chat.id, "fee", num[0])
    await m.reply(f"✅ 费率已设置为 {num[0]}%")

@dp.message(lambda m: is_group_message(m) and m.text in ("总账单", "今日总账单"))
async def day_report_cmd(m: types.Message):
    ensure_group(m)
    if not is_admin_or_operator(m.chat.id, m.from_user):
        return await m.reply(deny_text())

    start_ts, end_ts = day_range()
    await send_long_text(
        m.chat.id,
        report_text(m.chat.id, start_ts, end_ts, title="今日账单"),
        reply_markup=report_kb(m.chat.id),
    )

@dp.message(lambda m: is_group_message(m) and m.text in ("上个月总账单",))
async def prev_month_report_cmd(m: types.Message):
    ensure_group(m)
    if not is_admin_or_operator(m.chat.id, m.from_user):
        return await m.reply(deny_text())

    start_ts, end_ts = month_range(offset_months=1)
    await send_long_text(
        m.chat.id,
        report_text(m.chat.id, start_ts, end_ts, title="上个月账单"),
        reply_markup=report_kb(m.chat.id),
    )

@dp.message(lambda m: is_group_message(m) and (m.text in ("账单",) or is_cmd(m, "/我")))
async def user_report_cmd(m: types.Message):
    ensure_group(m)

    if is_cmd(m, "/我"):
        user = m.from_user
    elif m.reply_to_message and m.reply_to_message.from_user:
        user = m.reply_to_message.from_user
    else:
        user = m.from_user

    start_ts, end_ts = day_range()
    text = report_text(
        m.chat.id,
        start_ts,
        end_ts,
        title="个人账单",
        user_id=user.id,
        display_name=user.full_name or (user.username or str(user.id)),
    )
    await send_long_text(m.chat.id, text, reply_markup=report_kb(m.chat.id))

@dp.message(lambda m: is_group_message(m) and m.text == "撤销")
async def undo_cmd(m: types.Message):
    ensure_group(m)
    if not is_admin_or_operator(m.chat.id, m.from_user):
        return await m.reply(deny_text())

    tx = get_last_transaction(m.chat.id)
    if not tx:
        return await m.reply("暂无可撤销记录")

    undo_transaction(tx[0])
    start_ts, end_ts = day_range()
    await send_long_text(
        m.chat.id,
        "↩️ 已撤销上一笔记录\n\n" + report_text(m.chat.id, start_ts, end_ts, title="今日账单"),
        reply_markup=report_kb(m.chat.id),
    )


def is_clear_today_bill_cmd(text: str) -> bool:
    t = (text or "").strip()
    return t in ("删除账单", "删除账单：", "删除账单:") or t.startswith("删除账单")


@dp.message(lambda m: is_group_message(m) and is_clear_today_bill_cmd(m.text))
async def clear_today_bill_cmd(m: types.Message):
    ensure_group(m)
    if not is_admin_or_operator(m.chat.id, m.from_user):
        return await m.reply(deny_text())

    start_ts, end_ts = day_range()
    deleted = clear_transactions_for_range(m.chat.id, start_ts, end_ts)
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    await send_long_text(
        m.chat.id,
        f"🗑 <b>已清空今日账单</b>\n"
        f"日期：<code>{today}</code>\n"
        f"删除记录：<b>{deleted}</b> 条\n\n"
        + report_text(m.chat.id, start_ts, end_ts, title="今日账单"),
        reply_markup=report_kb(m.chat.id),
    )


def is_add_operator_cmd(text: str) -> bool:
    t = (text or "").strip()
    return t in (
        "添加操作人",
        "添加操作员",
        "添加操作人：",
        "添加操作员：",
        "添加操作人:",
        "添加操作员:",
    )


def can_manage_group_operators(chat_id, user: types.User | None) -> bool:
    if not user:
        return False
    if can_use_bot_ops(user.id):
        return True
    return is_admin_or_operator(chat_id, user)


@dp.message(
    lambda m: is_group_message(m)
    and m.reply_to_message
    and is_add_operator_cmd(m.text)
)
async def add_group_operator_cmd(m: types.Message):
    ensure_group(m)
    if not can_manage_group_operators(m.chat.id, m.from_user):
        return await m.reply(deny_text())

    target = m.reply_to_message.from_user
    if not target:
        return await m.reply("❌ 请回复要设为操作人的用户消息。")
    if target.is_bot:
        return await m.reply("❌ 不能将机器人设为操作人。")

    save_member(
        m.chat.id,
        target.id,
        target.username or "",
        target.full_name or "",
    )
    add_operator(
        m.chat.id,
        user_id=target.id,
        username=target.username or "",
        role="operator",
    )

    uname = f"@{target.username}" if target.username else "（无用户名）"
    await m.reply(
        "✅ <b>已添加本群操作人</b>\n\n"
        f"用户：{escape(target.full_name or '未设置')}\n"
        f"用户名：{escape(uname)}\n"
        f"ID：<code>{target.id}</code>\n\n"
        "对方现在可以在本群使用记账、查账、撤销、删除账单等操作。",
        parse_mode="HTML",
    )


def is_remove_operator_cmd(text: str) -> bool:
    t = (text or "").strip()
    return t in (
        "删除操作人",
        "删除操作员",
        "移除操作人",
        "移除操作员",
    )


@dp.message(
    lambda m: is_group_message(m)
    and m.reply_to_message
    and is_remove_operator_cmd(m.text)
)
async def remove_group_operator_cmd(m: types.Message):
    ensure_group(m)
    if not can_manage_group_operators(m.chat.id, m.from_user):
        return await m.reply(deny_text())

    target = m.reply_to_message.from_user
    if not target:
        return await m.reply("❌ 请回复要移除的操作人消息。")
    if can_use_bot_ops(target.id):
        return await m.reply("❌ 不能移除 Bot 管理员。")

    remove_operator(m.chat.id, user_id=target.id, username=target.username or None)
    uname = f"@{target.username}" if target.username else "（无用户名）"
    await m.reply(
        "✅ <b>已移除本群操作人</b>\n\n"
        f"用户：{escape(target.full_name or '未设置')}\n"
        f"用户名：{escape(uname)}\n"
        f"ID：<code>{target.id}</code>",
        parse_mode="HTML",
    )


# ================= REALTIME RATE =================
@dp.message(lambda m: m.text in ("实时U价", "📈 实时U价"))
async def menu_rate(m: types.Message):
    if not can_use_bot_ops(m.from_user.id):
        return await m.reply(deny_text())

    rates = await get_usdt_rates_cached()
    await m.answer(format_usdt_rate_text(rates), reply_markup=rate_kb(), parse_mode="HTML")

@dp.callback_query(lambda c: c.data == "rate:refresh")
async def rate_refresh_cb(c: types.CallbackQuery):
    if not c.message:
        return
    rates = await get_usdt_rates_cached(force=True)
    await c.message.answer(format_usdt_rate_text(rates), reply_markup=rate_kb(), parse_mode="HTML")
    await c.answer("✅ 已刷新")

# ================= HIBP (Have I Been Pwned) =================
def _hibp_cooldown_ok(user_id: int) -> bool:
    now = time.time()
    last = _hibp_last_query.get(user_id, 0)
    if now - last < HIBP_COOLDOWN:
        return False
    _hibp_last_query[user_id] = now
    return True


def _hibp_cooldown_left(user_id: int) -> int:
    last = _hibp_last_query.get(user_id, 0)
    left = HIBP_COOLDOWN - (time.time() - last)
    return max(0, int(left) + 1)


def _strip_html(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"<[^>]+>", "", str(s)).strip()


def _extract_hibp_email(text: str) -> str | None:
    t = (text or "").strip()
    for prefix in (
        "泄露查询",
        "查泄露",
        "查邮",
        "/查邮",
        "/hibp",
        "/pwned",
        "hibp",
        "pwned",
    ):
        if t.lower().startswith(prefix.lower()):
            rest = t[len(prefix) :].strip().lstrip("：:").strip()
            if is_valid_email(rest):
                return rest
    m = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", t)
    if m and is_valid_email(m.group(0)):
        return m.group(0)
    return None


def _strip_html_text(text: str) -> str:
    s = re.sub(r"<[^>]+>", " ", text or "")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:3500]


def format_hibp_breach_item(b: dict) -> str:
    name = escape(b.get("Title") or b.get("Name") or "-")
    domain = escape(b.get("Domain") or "-")
    bdate = escape(str(b.get("BreachDate") or "-"))
    pwn = b.get("PwnCount")
    pwn_s = f"{int(pwn):,}" if pwn else "-"
    classes = b.get("DataClasses") or []
    cls = escape(", ".join(classes[:6])) if classes else "-"
    if len(classes) > 6:
        cls += "…"
    return (
        f"• <b>{name}</b>\n"
        f"  域名：{domain} | 日期：{bdate}\n"
        f"  影响约：{pwn_s} 条 | 数据：{cls}"
    )


def format_hibp_breach_detail(b: dict) -> str:
    raw_name = (b.get("Name") or "").strip()
    title = escape(b.get("Title") or raw_name or "-")
    name = escape(raw_name or "-")
    domain = escape(b.get("Domain") or "-")
    bdate = escape(str(b.get("BreachDate") or "-"))
    added = escape(str(b.get("AddedDate") or "-"))
    modified = escape(str(b.get("ModifiedDate") or "-"))
    pwn = b.get("PwnCount")
    pwn_s = f"{int(pwn):,}" if pwn else "-"
    classes = b.get("DataClasses") or []
    if classes:
        cls_lines = "\n".join(f"  • {escape(c)}" for c in classes)
    else:
        cls_lines = "  —"

    raw_desc = _strip_html_text(b.get("Description") or "")
    desc_truncated = len(raw_desc) > 3200
    desc = escape(raw_desc[:3200] + ("…" if desc_truncated else ""))

    flags = []
    if b.get("IsVerified"):
        flags.append("已验证")
    if b.get("IsSensitive"):
        flags.append("敏感")
    if b.get("IsRetired"):
        flags.append("已归档")
    if b.get("IsFabricated"):
        flags.append("虚构")
    if b.get("IsSpamList"):
        flags.append("垃圾列表")
    flag_s = "、".join(flags) if flags else "—"

    page_url = breach_public_page_url(raw_name)
    page_url_esc = escape(page_url)

    lines = [
        f"📋 <b>{title}</b>",
        f"标识：<code>{name}</code>",
        f"域名：{domain}",
        f"泄露日期：{bdate}",
        f"收录时间：{added}",
        f"更新时间：{modified}",
        f"影响记录：约 <b>{pwn_s}</b> 条",
        f"标记：{escape(flag_s)}",
        "",
        f"<b>涉及数据类型（{len(classes)} 项）</b>",
        cls_lines,
    ]
    if raw_desc:
        lines.extend(["", f"<b>说明</b>"])
        if desc_truncated:
            lines.append("<i>（Telegram 字数限制，以下为节选）</i>")
        lines.append(desc)
    elif not raw_desc:
        lines.extend(["", "<b>说明</b>\n<i>（API 未返回正文，请打开下方链接查看官网全文）</i>"])

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "<b>📌 能看什么 / 不能看什么</b>",
        "• 本消息 + 下方链接 = <b>事件说明</b>（何时泄露、涉及哪些类型）",
        "• <b>不能</b>在此查看具体邮箱/密码名单（HIBP 与法律原因均不提供）",
        "• 查<b>自己的邮箱</b>是否中招：私聊发 <code>查邮 你的@邮箱.com</code>",
        "",
        f"🔗 <b>本条泄露官网页</b>（非首页）",
        f'<a href="{page_url}">{page_url_esc}</a>',
        f"<code>{page_url_esc}</code>",
        "",
        "<i>点下方绿色按钮用浏览器打开；国内若打不开，请用代理访问 haveibeenpwned.com。</i>",
        "<i>Bot 内已是 API 能提供的全部字段；官网正文与上一致，不会多出密码表。</i>",
    ])
    return "\n".join(lines)


def breach_detail_inline_kb(breach_name: str) -> InlineKeyboardMarkup:
    page_url = breach_public_page_url(breach_name)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 打开本条泄露详情页", url=page_url)],
        [InlineKeyboardButton(text="« 返回列表", callback_data="hibp:br:back")],
    ])


def breaches_list_inline_kb(items: list) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for i, b in enumerate(items):
        if not isinstance(b, dict):
            continue
        label = (b.get("Title") or b.get("Name") or "?")[:28]
        row.append(
            InlineKeyboardButton(text=f"📁 {label}", callback_data=f"hibp:br:{i}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_hibp_result(email: str, breaches: list, pastes: list | None, page: int = 0) -> str:
    email_safe = escape(email)
    lines = [
        "🔒 <b>Have I Been Pwned 自查</b>",
        f"邮箱：<code>{email_safe}</code>",
        "",
    ]
    if not breaches:
        lines.append("✅ 在 HIBP 已知泄露库中<b>未发现</b>该邮箱。")
    else:
        total = len(breaches)
        start = page * HIBP_PAGE_SIZE
        chunk = breaches[start : start + HIBP_PAGE_SIZE]
        lines.append(f"⚠️ 共发现 <b>{total}</b> 起泄露，显示第 {page + 1} 页：")
        lines.append("")
        for b in chunk:
            if isinstance(b, dict):
                lines.append(format_hibp_breach_item(b))
        if start + HIBP_PAGE_SIZE < total:
            lines.append("")
            lines.append(f"发送「下一页」查看更多（还剩 {total - start - HIBP_PAGE_SIZE} 条）")

    if pastes:
        lines.append("")
        lines.append(f"📋 Paste 记录：<b>{len(pastes)}</b> 条")
        for p in pastes[:5]:
            if not isinstance(p, dict):
                continue
            src = escape(p.get("Source") or "-")
            pid = escape(str(p.get("Id") or "-"))
            lines.append(f"  • {src} (<code>{pid}</code>)")

    lines.extend([
        "",
        "<i>数据来自 haveibeenpwned.com，仅供自查本人邮箱。</i>",
    ])
    return "\n".join(lines)


def hibp_help_text() -> str:
    return (
        "🔒 <b>泄露自查（HIBP 官方 API）</b>\n\n"
        "仅支持在<b>私聊</b>查询<b>您自己的邮箱</b>是否出现在已知公开泄露事件中。\n\n"
        "<b>用法示例</b>\n"
        "• <code>泄露查询 your@email.com</code>\n"
        "• <code>查泄露 your@email.com</code>\n"
        "• <code>/hibp your@email.com</code>\n\n"
        f"⏱ 每位用户 {HIBP_COOLDOWN} 秒查询一次\n"
        "🔗 https://haveibeenpwned.com\n\n"
        "管理员：<code>/hibp_ping</code> 测试 API 连通性"
    )


async def run_hibp_email_check(message: types.Message, email: str, page: int = 0):
    if HTTP_SESSION is None:
        return await message.reply("⚠️ 服务未就绪，请稍后再试。")

    if not _hibp_cooldown_ok(message.from_user.id) and page == 0:
        return await message.reply(f"⏳ 请 {_hibp_cooldown_left(message.from_user.id)} 秒后再查。")

    try:
        _, breaches = await get_breaches_for_account(
            HTTP_SESSION,
            email,
            api_key=HIBP_API_KEY,
            user_agent=HIBP_USER_AGENT,
        )
        pastes = []
        try:
            _, pastes = await get_pastes_for_account(
                HTTP_SESSION,
                email,
                api_key=HIBP_API_KEY,
                user_agent=HIBP_USER_AGENT,
            )
        except HibpError:
            pastes = []
        _hibp_pages[message.from_user.id] = {
            "email": email,
            "breaches": breaches or [],
            "pastes": pastes or [],
            "page": page,
        }
        text = format_hibp_result(email, breaches or [], pastes, page=page)
        await message.reply(text, parse_mode="HTML")
    except HibpError as e:
        if e.status == 401:
            await message.reply(
                "❌ HIBP API Key 无效或未配置。\n"
                "请在 .env 设置 <code>HIBP_API_KEY</code>（见 https://haveibeenpwned.com/API/Key）",
                parse_mode="HTML",
            )
        elif e.status == 429:
            await message.reply("⏳ HIBP 请求过于频繁，请稍后再试。")
        else:
            await message.reply(f"❌ 查询失败：{escape(str(e))}", parse_mode="HTML")
    except Exception as e:
        print("hibp query error:", repr(e))
        traceback.print_exc()
        await message.reply("❌ 查询失败，请稍后再试。")


@dp.message(lambda m: is_private(m) and m.text in ("🔒 泄露自查", "泄露自查", "hibp帮助", "HIBP帮助"))
async def menu_hibp_help(m: types.Message):
    if not can_use_bot_ops(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await m.reply(hibp_help_text(), parse_mode="HTML")


def _is_hibp_email_query(text: str) -> bool:
    t = (text or "").strip().lower()
    if t in ("/hibp_ping", "/hibp_breaches"):
        return False
    if (
        text.strip().startswith("泄露查询")
        or text.strip().startswith("查泄露")
        or text.strip().startswith("查邮")
        or text.strip().lower().startswith("/查邮")
    ):
        return True
    if t.startswith("/pwned") or t.startswith("hibp "):
        return True
    if t.startswith("/hibp") and not t.startswith("/hibp_ping") and not t.startswith("/hibp_breaches"):
        return True
    return False


@dp.message(lambda m: is_private(m) and m.text and _is_hibp_email_query(m.text))
async def hibp_query_cmd(m: types.Message):
    if not can_use_bot_ops(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")

    email = _extract_hibp_email(m.text)
    if not email:
        return await m.reply(hibp_help_text(), parse_mode="HTML")

    if not HIBP_API_KEY:
        return await m.reply(
            "❌ 未配置 <code>HIBP_API_KEY</code>。\n"
            "请到 https://haveibeenpwned.com/API/Key 申请后写入 .env",
            parse_mode="HTML",
        )

    await m.reply("⏳ 正在查询 HIBP…")
    await run_hibp_email_check(m, email)


@dp.message(lambda m: is_private(m) and m.text in ("下一页", "HIBP下一页"))
async def hibp_next_page_cmd(m: types.Message):
    if not can_use_bot_ops(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")

    ctx = _hibp_pages.get(m.from_user.id)
    if not ctx:
        return await m.reply("暂无分页结果，请先查询邮箱。")

    breaches = ctx.get("breaches") or []
    total_pages = max(1, (len(breaches) + HIBP_PAGE_SIZE - 1) // HIBP_PAGE_SIZE)
    current = ctx.get("page", 0)
    nxt = current + 1
    if nxt >= total_pages:
        return await m.reply("已经是最后一页。")

    ctx["page"] = nxt
    text = format_hibp_result(
        ctx["email"],
        breaches,
        ctx.get("pastes"),
        page=nxt,
    )
    await m.reply(text, parse_mode="HTML")


@dp.message(lambda m: is_private(m) and m.text and m.text.strip().lower() == "/hibp_ping")
async def hibp_ping_cmd(m: types.Message):
    if not can_use_bot_ops(m.from_user.id):
        return await m.reply("❌ 仅管理员可用")

    if HTTP_SESSION is None:
        return await m.reply("HTTP 会话未就绪")

    await m.reply("⏳ 正在测试 HIBP API…")
    try:
        result = await hibp_ping(HTTP_SESSION, HIBP_API_KEY, HIBP_USER_AGENT)
        lines = [
            "🔒 <b>HIBP 连通性</b>",
            f"公开 breaches 列表：{'✅' if result.get('breaches_ok') else '❌'} "
            f"（{result.get('breaches_count', 0)} 条）",
        ]
        if result.get("breaches_error"):
            lines.append(f"breaches 错误：{escape(result['breaches_error'])}")
        lines.append(f"邮箱查询测试：{escape(result.get('account_note', '-'))}")
        key_hint = "已配置 Key" if HIBP_API_KEY else "未配置 Key"
        lines.append(f"API Key：{key_hint}")
        lines.append(f"User-Agent：<code>{escape(HIBP_USER_AGENT)}</code>")
        await m.reply("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await m.reply(f"❌ 测试失败：{escape(str(e))}", parse_mode="HTML")


@dp.message(lambda m: is_private(m) and m.text and m.text.strip().lower() == "/hibp_breaches")
async def hibp_list_breaches_cmd(m: types.Message):
    if not can_use_bot_ops(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")

    if HTTP_SESSION is None:
        return await m.reply("服务未就绪")

    await _run_tool_breaches(m)


def can_use_tools_panel(user_id: int) -> bool:
    return can_use_bot_ops(user_id) or can_use_manage_panel(user_id)


async def run_bank_card_lookup(message: types.Message, card_text: str):
    if HTTP_SESSION is None:
        return await message.reply("服务未就绪")
    card_no = extract_card_number(card_text)
    if not card_no:
        return await message.reply(
            "❌ 未识别到有效卡号，请发送 13–19 位银行卡号。\n"
            "例：<code>查卡 6222021234567890123</code>",
            parse_mode="HTML",
        )
    await message.reply("⏳ 正在查询银行卡信息…")
    try:
        data = await lookup_bank_card(HTTP_SESSION, card_no)
        await message.reply(format_bank_card_reply(data), parse_mode="HTML")
    except BankCardError as e:
        await message.reply(f"❌ {escape(str(e))}", parse_mode="HTML")
    except Exception as e:
        print("bank card lookup error:", repr(e))
        await message.reply("❌ 查询失败，请稍后重试。")


async def run_bank_four_verify(message: types.Message, text: str):
    if HTTP_SESSION is None:
        return await message.reply("服务未就绪")
    fields = parse_four_elements(text)
    if not fields:
        return await message.reply(
            "❌ 格式无法识别。请按下面任一方式发送：\n\n"
            "<b>方式1：四行</b>\n"
            "张三\n"
            "110101199001011234\n"
            "6222021234567890123\n"
            "13800138000\n\n"
            "<b>方式2：一行</b>\n"
            "<code>张三,110101199001011234,6222021234567890123,13800138000</code>\n\n"
            "<b>方式3：带标签</b>\n"
            "<code>姓名张三 身份证110101199001011234 卡号6222021234567890123 手机13800138000</code>",
            parse_mode="HTML",
        )
    await message.reply("⏳ 正在四要素核验（银联/通道校验）…")
    try:
        result = await verify_bank_four_elements(
            HTTP_SESSION,
            name=fields["name"],
            cert_id=fields["cert_id"],
            card=fields["card"],
            phone=fields["phone"],
        )
        await message.reply(format_verify_reply(result), parse_mode="HTML")
    except BankVerifyError as e:
        await message.reply(f"❌ {escape(str(e))}", parse_mode="HTML")
    except Exception as e:
        print("bank verify error:", repr(e))
        await message.reply("❌ 核验失败，请稍后重试。")


async def _osint_reply(message: types.Message, text: str):
    await message.reply(text, parse_mode="HTML", disable_web_page_preview=len(text) > 3500)


async def _run_tool_breaches(message: types.Message):
    if HTTP_SESSION is None:
        return await message.reply("服务未就绪")
    try:
        items = await list_all_breaches(
            HTTP_SESSION, HIBP_USER_AGENT, limit=15, api_key=HIBP_API_KEY
        )
        if not items:
            return await message.reply("未获取到泄露事件列表。")

        uid = message.from_user.id if message.from_user else 0
        if uid:
            _hibp_breach_lists[uid] = items

        lines = [
            "📚 <b>HIBP 泄露事件（前 15 条）</b>",
            "",
            "👇 点击下方按钮查看该事件的详细说明",
        ]
        for i, b in enumerate(items):
            if isinstance(b, dict):
                lines.append(
                    f"{i + 1}. <b>{escape(b.get('Title') or b.get('Name') or '-')}</b> "
                    f"({escape(str(b.get('BreachDate') or '-'))})"
                )

        kb = breaches_list_inline_kb(items)
        await message.reply(
            "\n".join(lines),
            reply_markup=kb,
            parse_mode="HTML",
        )
    except HibpError as e:
        await message.reply(f"❌ {escape(str(e))}", parse_mode="HTML")


@dp.callback_query(lambda c: c.data and c.data.startswith("hibp:br:"))
async def hibp_breach_detail_cb(c: types.CallbackQuery):
    if not c.from_user or not c.message:
        return await c.answer()

    if not can_use_tools_panel(c.from_user.id) and not has_bot_access(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    action = c.data.split(":", 2)[2] if c.data else ""

    if action == "back":
        await _run_tool_breaches(c.message)
        return await c.answer()

    try:
        idx = int(action)
    except ValueError:
        return await c.answer("无效选项", show_alert=True)

    items = _hibp_breach_lists.get(c.from_user.id)
    if not items or idx < 0 or idx >= len(items):
        return await c.answer("列表已过期，请重新发送 Breaches", show_alert=True)

    cached = items[idx]
    name = cached.get("Name") if isinstance(cached, dict) else None
    if not name:
        return await c.answer("数据异常", show_alert=True)

    if HTTP_SESSION is None:
        return await c.answer("服务未就绪", show_alert=True)

    try:
        await c.answer("加载中…")
        breach = await get_breach_by_name(
            HTTP_SESSION,
            name,
            api_key=HIBP_API_KEY,
            user_agent=HIBP_USER_AGENT,
        )
    except HibpError as e:
        if isinstance(cached, dict) and cached.get("Description"):
            breach = cached
        else:
            return await c.message.answer(f"❌ {escape(str(e))}", parse_mode="HTML")

    breach_name = breach.get("Name") or name
    await send_long_text(
        c.message.chat.id,
        format_hibp_breach_detail(breach),
        reply_markup=breach_detail_inline_kb(breach_name),
    )


# ================= ADMIN TOOLS PANEL =================
@dp.message(
    lambda m: is_private(m)
    and m.text
    and m.text.strip() in (
        "显示菜单",
        "功能面板",
        "管理员功能面板",
        "管理面板",
        "面板",
        "/panel",
    )
)
async def show_tools_menu_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id):
        return await m.reply(deny_text())
    await state.clear()
    await m.reply(
        "🧭 <b>管理员查询面板</b>\n\n"
        "点击下方按钮，或直接发送命令（发「使用说明」查看）。",
        reply_markup=admin_tools_panel_kb(),
        parse_mode="HTML",
    )
    await m.answer("已显示查询面板。", reply_markup=tools_reply_kb())


@dp.message(lambda m: is_private(m) and m.text in ("隐藏菜单",))
async def hide_tools_menu_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    await m.reply("✅ 已隐藏快捷键盘", reply_markup=ReplyKeyboardRemove())


@dp.message(lambda m: is_private(m) and m.text in ("详细说明", "🧾详细说明"))
async def tools_help_cmd(m: types.Message):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await m.reply(tools_panel_help_text(), parse_mode="HTML")


@dp.message(lambda m: is_private(m) and m.text in ("Breaches", "breaches"))
async def tools_breaches_cmd(m: types.Message):
    if not can_use_tools_panel(m.from_user.id):
        return await m.reply(deny_text())
    await _run_tool_breaches(m)


@dp.message(lambda m: is_private(m) and m.text in ("Dataclasses", "dataclasses"))
async def tools_dataclasses_cmd(m: types.Message):
    if not can_use_tools_panel(m.from_user.id):
        return await m.reply(deny_text())
    await m.reply(
        "📚 <b>HIBP 可查询类型（Dataclasses）</b>\n\n"
        "本 Bot 通过官方 API 仅支持按 <b>邮箱</b> 自查：\n"
        "• Email addresses\n"
        "• 关联 Paste 记录\n\n"
        "用法：<code>查邮 your@email.com</code>\n"
        "不支持：手机号 / QQ / 身份证 / 用户名 等社工库字段。",
        parse_mode="HTML",
    )


@dp.message(
    lambda m: is_private(m)
    and m.text
    and (
        m.text.strip() in ("银行卡查询", "查银行卡", "查卡")
        or (m.text or "").strip().lower().startswith("/查卡")
        or ((m.text or "").strip().startswith("查卡") and extract_card_number(m.text))
    )
)
async def tools_bank_card_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    card_no = extract_card_number(m.text or "")
    if card_no:
        await state.clear()
        return await run_bank_card_lookup(m, m.text or "")
    await state.set_state(ToolFSM.waiting_bank_card)
    await m.reply(
        "🏦 请发送 <b>银行卡号</b>（13–19 位），或一行：\n"
        "<code>查卡 6222021234567890123</code>\n\n"
        "<i>可查询发卡行/开户行、卡类型；姓名与手机号需合规 API 才可能有结果。</i>",
        parse_mode="HTML",
    )


@dp.message(ToolFSM.waiting_bank_card)
async def tools_receive_bank_card(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.clear()
    await run_bank_card_lookup(m, m.text or "")


@dp.message(
    lambda m: is_private(m)
    and m.text
    and m.text.strip() in ("四要素核验", "四要素", "银行卡核验", "银行卡四要素")
)
async def tools_bank_verify_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.set_state(ToolFSM.waiting_bank_verify)
    await m.reply(
        "🏦 <b>银行卡四要素核验</b>\n\n"
        "请一次性发送（四行）：\n"
        "1️⃣ 持卡人姓名\n"
        "2️⃣ 身份证号\n"
        "3️⃣ 银行卡号\n"
        "4️⃣ 绑定手机号\n\n"
        "或一行逗号分隔：\n"
        "<code>姓名,身份证,卡号,手机号</code>\n\n"
        "<i>说明：这是验证四项是否属于同一人，不是只输入卡号查姓名/手机。"
        "需在 .env 配置 BANK_VERIFY_APPCODE（易源/云市场购买）。</i>",
        parse_mode="HTML",
    )


@dp.message(ToolFSM.waiting_bank_verify)
async def tools_receive_bank_verify(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.clear()
    await run_bank_four_verify(m, m.text or "")


@dp.message(lambda m: is_private(m) and m.text in ("查邮",))
async def tools_email_prompt_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.set_state(ToolFSM.waiting_email)
    await m.reply(
        "📧 请发送要自查的 <b>邮箱地址</b>，或一行发送：\n"
        "<code>查邮 your@email.com</code>",
        parse_mode="HTML",
    )


@dp.message(
    lambda m: is_private(m)
    and m.text
    and (
        m.text.strip() in ("本机 IP", "本机IP", "本机ip")
        or is_my_ip_cmd(m.text)
    )
)
async def tools_my_ip_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    if extract_ipv4(m.text or "") and not is_my_ip_cmd(m.text):
        return
    await state.clear()
    await tools_lookup_my_ip(m)


@dp.message(lambda m: is_private(m) and m.text in ("查指定IP", "指定IP"))
async def tools_ip_prompt_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id):
        return await m.reply(deny_text())
    await state.set_state(ToolFSM.waiting_ip)
    await m.reply(
        "🌍 请发送要查询的 <b>公网 IP</b>，例如：\n<code>203.80.164.113</code>\n"
        "或：<code>IP 8.8.8.8</code>\n\n"
        "查本机出口 IP 请直接发：<code>本机 IP</code> 或 <code>查IP</code>",
        parse_mode="HTML",
    )


@dp.message(ToolFSM.waiting_email)
async def tools_receive_email(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    email = _extract_hibp_email(m.text or "") or (m.text or "").strip()
    if not is_valid_email(email):
        return await m.reply("❌ 邮箱格式不正确，请重试。")
    await state.clear()
    if not HIBP_API_KEY:
        return await m.reply("❌ 未配置 HIBP_API_KEY")
    await m.reply("⏳ 正在查询 HIBP…")
    await run_hibp_email_check(m, email)


@dp.message(ToolFSM.waiting_ip)
async def tools_receive_ip(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id):
        return await m.reply(deny_text())
    await state.clear()
    raw = (m.text or "").strip()
    if not raw or is_my_ip_cmd(raw):
        await m.reply("⏳ 正在查询本机出口 IP…")
        return await _osint_reply(m, await lookup_ip_ipapi(None))
    ip = extract_ipv4(raw) or raw
    await m.reply("⏳ 正在查询 IP…")
    await _osint_reply(m, await lookup_ip_ipapi(ip))


@dp.message(lambda m: is_private(m) and m.text and extract_ipv4(m.text))
async def tools_ip_direct_cmd(m: types.Message):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return
    if is_my_ip_cmd(m.text):
        return
    ip = extract_ipv4(m.text)
    if ip:
        await tools_lookup_ip(m, ip)


def _osint_cmd_prefix(text: str, prefixes: tuple) -> bool:
    t = (text or "").strip()
    return any(t.startswith(p) for p in prefixes)


@dp.message(
    lambda m: is_private(m)
    and m.text
    and _osint_cmd_prefix(m.text, ("查用户名 ", "用户名 "))
)
async def osint_username_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.clear()
    uname = extract_username(m.text) or m.text.split(maxsplit=1)[-1]
    await m.reply("⏳ 正在查询用户名…")
    await _osint_reply(m, await lookup_username(uname))


@dp.message(
    lambda m: is_private(m)
    and m.text
    and (_osint_cmd_prefix(m.text, ("查手机号 ", "手机号 ")) or m.text.strip() == "查手机号")
)
async def osint_phone_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    phone = extract_phone(m.text)
    if not phone:
        await state.set_state(ToolFSM.waiting_phone)
        return await m.reply(TOOL_QUERY_PROMPTS["phone"], parse_mode="HTML")
    await state.clear()
    await m.reply("⏳ 正在查询手机号…")
    await _osint_reply(m, await lookup_phone(phone))


@dp.message(
    lambda m: is_private(m)
    and m.text
    and _osint_cmd_prefix(m.text, ("查身份证 ", "身份证 "))
)
async def osint_idcard_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    cid = extract_idcard(m.text)
    if not cid:
        await state.set_state(ToolFSM.waiting_idcard)
        return await m.reply(TOOL_QUERY_PROMPTS["idcard"], parse_mode="HTML")
    await state.clear()
    await _osint_reply(m, await lookup_idcard(cid))


@dp.message(
    lambda m: is_private(m)
    and m.text
    and _osint_cmd_prefix(m.text, ("查QQ ", "QQ "))
)
async def osint_qq_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    qq = extract_qq(m.text)
    if not qq:
        await state.set_state(ToolFSM.waiting_qq)
        return await m.reply(TOOL_QUERY_PROMPTS["qq"], parse_mode="HTML")
    await state.clear()
    await _osint_reply(m, lookup_qq(qq))


@dp.message(
    lambda m: is_private(m)
    and m.text
    and _osint_cmd_prefix(m.text, ("查微信 ", "微信 "))
)
async def osint_wechat_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    wx = extract_wechat(m.text)
    if not wx:
        await state.set_state(ToolFSM.waiting_wechat)
        return await m.reply(TOOL_QUERY_PROMPTS["wechat"], parse_mode="HTML")
    await state.clear()
    await _osint_reply(m, lookup_wechat(wx))


@dp.message(
    lambda m: is_private(m)
    and m.text
    and (
        (m.text or "").strip().upper().startswith("查IP")
        or (m.text or "").strip().lower().startswith("ip ")
    )
    and not is_my_ip_cmd(m.text)
)
async def osint_ip_cmd(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    parts = (m.text or "").strip().split()
    ip = None
    if len(parts) >= 2:
        ip = extract_ipv4(parts[-1]) or (parts[-1] if re.match(r"^[\d.]+$", parts[-1]) else None)
    await state.clear()
    await m.reply("⏳ 正在查询 IP…")
    await _osint_reply(m, await lookup_ip_ipapi(ip))


@dp.message(lambda m: is_private(m) and m.text and m.text.strip() in ("查用户名",))
async def osint_username_prompt(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.set_state(ToolFSM.waiting_username)
    await m.reply(TOOL_QUERY_PROMPTS["username"], parse_mode="HTML")


@dp.message(ToolFSM.waiting_username)
async def osint_receive_username(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.clear()
    uname = extract_username(m.text) or (m.text or "").strip().lstrip("@")
    await m.reply("⏳ 正在查询用户名…")
    await _osint_reply(m, await lookup_username(uname))


@dp.message(ToolFSM.waiting_phone)
async def osint_receive_phone(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.clear()
    await m.reply("⏳ 正在查询手机号…")
    await _osint_reply(m, await lookup_phone(m.text or ""))


@dp.message(ToolFSM.waiting_idcard)
async def osint_receive_idcard(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.clear()
    await _osint_reply(m, await lookup_idcard(m.text or ""))


@dp.message(ToolFSM.waiting_qq)
async def osint_receive_qq(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.clear()
    await _osint_reply(m, lookup_qq(m.text or ""))


@dp.message(ToolFSM.waiting_wechat)
async def osint_receive_wechat(m: types.Message, state: FSMContext):
    if not can_use_tools_panel(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.clear()
    await _osint_reply(m, lookup_wechat(m.text or ""))


@dp.callback_query(lambda c: c.data and c.data.startswith("tool:"))
async def tools_panel_cb(c: types.CallbackQuery, state: FSMContext):
    if not c.from_user or not c.message:
        return
    if not can_use_tools_panel(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    action = c.data.split(":", 1)[1]

    if action == "help":
        await c.message.answer(tools_panel_help_text(), parse_mode="HTML")
        return await c.answer()

    if action == "email":
        await state.set_state(ToolFSM.waiting_email)
        await c.message.answer(
            "📧 请发送要自查的邮箱，或 <code>查邮 xxx@email.com</code>",
            parse_mode="HTML",
        )
        return await c.answer()

    if action == "bankcard":
        await state.set_state(ToolFSM.waiting_bank_card)
        await c.message.answer(
            "🏦 请发送银行卡号（13–19 位），或 <code>查卡 卡号</code>",
            parse_mode="HTML",
        )
        return await c.answer()

    if action == "bankverify":
        await state.set_state(ToolFSM.waiting_bank_verify)
        await c.message.answer(
            "🏦 请发送四要素（四行：姓名、身份证、卡号、手机号）\n"
            "详见「四要素核验」说明",
            parse_mode="HTML",
        )
        return await c.answer()

    if action == "myip":
        await state.clear()
        await tools_lookup_my_ip(c.message)
        return await c.answer()

    if action == "ip":
        await state.set_state(ToolFSM.waiting_ip)
        await c.message.answer(TOOL_QUERY_PROMPTS["ip"], parse_mode="HTML")
        return await c.answer()

    if action == "username":
        await state.set_state(ToolFSM.waiting_username)
        await c.message.answer(TOOL_QUERY_PROMPTS["username"], parse_mode="HTML")
        return await c.answer()

    if action == "phone":
        await state.set_state(ToolFSM.waiting_phone)
        await c.message.answer(TOOL_QUERY_PROMPTS["phone"], parse_mode="HTML")
        return await c.answer()

    if action == "idcard":
        await state.set_state(ToolFSM.waiting_idcard)
        await c.message.answer(TOOL_QUERY_PROMPTS["idcard"], parse_mode="HTML")
        return await c.answer()

    if action == "qq":
        await state.set_state(ToolFSM.waiting_qq)
        await c.message.answer(TOOL_QUERY_PROMPTS["qq"], parse_mode="HTML")
        return await c.answer()

    if action == "wechat":
        await state.set_state(ToolFSM.waiting_wechat)
        await c.message.answer(TOOL_QUERY_PROMPTS["wechat"], parse_mode="HTML")
        return await c.answer()

    if action == "breaches":
        await _run_tool_breaches(c.message)
        return await c.answer()

    if action == "dataclasses":
        await c.message.answer(
            "📚 HIBP 仅支持邮箱自查。发送 <code>查邮 your@email.com</code>",
            parse_mode="HTML",
        )
        return await c.answer()

    await c.answer()


# ================= ADDRESS QUERY =================
@dp.message(lambda m: is_private(m) and m.text in ("地址查询", "🔍 地址查询", "📍 地址查询"))
async def menu_address_query(m: types.Message, state: FSMContext):
    if not can_use_bot_ops(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")
    await state.set_state(AddressQueryFSM.waiting_address)
    await m.reply(address_query_text(), parse_mode="HTML")

@dp.message(AddressQueryFSM.waiting_address)
async def receive_address_query(m: types.Message, state: FSMContext):
    if not can_use_bot_ops(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")

    addr = (m.text or "").strip()
    if not is_tron_address(addr):
        return await m.reply(
            "❌ 地址格式不正确，请重新输入 TRON 地址。\n"
            "示例：<code>Txxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</code>",
            parse_mode="HTML",
        )

    await m.reply("⏳ 正在查询链上数据，请稍候...")

    try:
        info = await check_tron_address(addr)
        text = format_address_info_text(addr, info)
    except Exception as e:
        print("on-chain query error:", e)
        text = f"🔎 查询地址：<code>{addr}</code>\n\n⚠️ 查询失败，请稍后再试。"

    await state.clear()
    await m.reply(text, parse_mode="HTML", reply_markup=address_result_kb(addr, page=1))

@dp.callback_query(lambda c: c.data == "addr:again")
async def addr_again_cb(c: types.CallbackQuery, state: FSMContext):
    if not c.message:
        return
    await state.set_state(AddressQueryFSM.waiting_address)
    await c.message.answer(address_query_text(), parse_mode="HTML")
    await c.answer()

@dp.callback_query(lambda c: c.data == "addr:back")
async def addr_back_cb(c: types.CallbackQuery, state: FSMContext):
    if not c.message:
        return
    await state.clear()
    await c.message.answer("✅ 已返回主菜单")
    await c.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith("addr:tx:"))
async def addr_tx_cb(c: types.CallbackQuery):
    if not c.message:
        return

    parts = c.data.split(":")
    address = parts[2]
    page = int(parts[3]) if len(parts) >= 4 and parts[3].isdigit() else 1

    await c.message.answer("⏳ 正在加载交易记录，请稍候...")

    try:
        txs = await get_tron_transactions(address, page=page, page_size=10)
        if not txs:
            await c.message.answer(f"🔎 查询地址：<code>{address}</code>\n📄 当前页无交易记录", parse_mode="HTML")
            return await c.answer()
    
        text = f"🔎 查询地址：<code>{address}</code>\n🗂 当前页码：第 {page} 页\n\n📄 交易记录：\n"
        for tx in txs:
            text += format_tron_tx_row(tx) + "\n\n"

        await c.message.answer(text, parse_mode="HTML", reply_markup=tx_history_kb(address, page))
    except Exception as e:
        print("addr tx cb error:", e)
        await c.message.answer("⚠️ 交易记录加载失败，请稍后再试。")

    await c.answer()

# ================= WALLET UI =================
def address_result_kb(address, page=1):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📜 链上交易记录", callback_data=f"addr:tx:{address}:{page}"),
        ],
        [
            InlineKeyboardButton(text="🔄 重新查询", callback_data="addr:again"),
            InlineKeyboardButton(text="⬅️ 返回菜单", callback_data="addr:back"),
        ],
    ])

def tx_history_kb(address, page=1):
    buttons = []
    if page > 1:
        buttons.append(
            InlineKeyboardButton(
                text="⬅️ 上一页",
                callback_data=f"addr:tx:{address}:{page-1}"
            )
        )

    buttons.append(
        InlineKeyboardButton(
            text=f"📄 第 {page} 页",
            callback_data="noop"
        )
    )

    buttons.append(
        InlineKeyboardButton(
            text="下一页 ➡️",
            callback_data=f"addr:tx:{address}:{page+1}"
        )
    )

    return InlineKeyboardMarkup(inline_keyboard=[buttons])

# ================= WALLET HELPERS =================
def get_user_wallet_send_count(user_id, chat_id=None):
    try:
        with get_db() as (_conn, cur):
            if chat_id is None:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM wallet_checks
                    WHERE user_id = %s
                    """,
                    (int(user_id),)
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM wallet_checks
                    WHERE user_id = %s AND chat_id = %s
                    """,
                    (int(user_id), int(chat_id))
                )

            row = cur.fetchone()
            return int(row[0] or 0) if row else 0
    except Exception as e:
        print("get_user_wallet_send_count error:", e)
        return 0

def wallet_risk_analysis(info):
    warnings = []
    score = 0

    tx_count = info.get("tx_count") or 0
    trx_balance = float(info.get("trx_balance") or 0)
    usdt_balance = float(info.get("usdt_balance") or 0)
    latest_time = info.get("latest_time")

    now_ts = int(time.time())

    if tx_count == 0:
        warnings.append(("🆕 新钱包", "该地址暂无交易记录，可能为新钱包地址，请结合实际用途继续核对。"))
    elif tx_count < 3:
        warnings.append(("⚠️ 注意", "该地址交易次数较少，建议进一步核实。"))
        score += 1

    if trx_balance < 1:
        warnings.append(("⚠️ 注意", "TRX余额较低，可能影响链上转账或能量消耗。"))
        score += 1

    if usdt_balance <= 0:
        warnings.append(("ℹ️ 提示", "当前USDT余额为0，请确认该地址用途是否正常。"))

    if latest_time:
        try:
            lt = int(latest_time)
            if lt > 10_000_000_000:
                lt = lt // 1000

            idle_days = (now_ts - lt) // 86400

            if idle_days >= 90:
                warnings.append(("🚨 高风险", f"该地址已超过 {idle_days} 天未活跃，请谨慎核对。"))
                score += 2
            elif idle_days >= 30:
                warnings.append(("⚠️ 注意", f"该地址已 {idle_days} 天未活跃。"))
                score += 1
        except Exception:
            pass

    if score >= 3:
        level = "🚨 高风险地址"
    elif score >= 1:
        level = "⚠️ 需谨慎核对"
    else:
        level = "✅ 基本正常"

    return level, warnings

def build_wallet_warning_html(info):
    level, warnings = wallet_risk_analysis(info)

    if not warnings:
        return "\n\n✅ <b>地址状态正常，未发现明显异常。</b>"

    lines = [f"\n\n🛡 <b>风险评估</b>", f"• <b>{escape(level)}</b>"]
    for tag, msg in warnings:
        lines.append(f"• <b>{escape(tag)}</b>：{escape(msg)}")
    return "\n".join(lines)

# ================= TRON API =================
async def trongrid_get(path, params=None):
    headers = {
        "accept": "application/json",
        "user-agent": "Mozilla/5.0",
    }
    if TRONGRID_API_KEY:
        headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY

    url = path if path.startswith("http") else f"{TRONGRID_API_URL}{path}"

    if HTTP_SESSION is None:
        return {}

    async with HTTP_SESSION.get(url, params=params, headers=headers) as resp:
        if resp.status != 200:
            return {}
        return await resp.json()

def _pick_account(payload):
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("data"), list) and payload["data"]:
        return payload["data"][0]
    if payload.get("address"):
        return payload
    if isinstance(payload.get("data"), dict):
        return payload["data"]
    return None


def _raw_to_usdt_amount(raw, decimals=6):
    if raw is None:
        return None
    try:
        decimals = int(decimals or 6)
    except Exception:
        decimals = 6
    try:
        val = float(raw)
        if val > 1_000_000:
            return val / (10 ** decimals)
        return val
    except Exception:
        return None


def _is_usdt_token(sym: str, contract: str) -> bool:
    sym = (sym or "").upper()
    contract = (contract or "").strip()
    return sym == "USDT" or contract in USDT_TRC20_CONTRACTS


def _parse_trc20_usdt(account):
    if not isinstance(account, dict):
        return None

    trc20 = account.get("trc20")
    if isinstance(trc20, list):
        for entry in trc20:
            if isinstance(entry, dict):
                for contract, balance in entry.items():
                    if str(contract) in USDT_TRC20_CONTRACTS:
                        amt = _raw_to_usdt_amount(balance)
                        if amt is not None:
                            return amt

    candidates = [
        "trc20token_balances",
        "trc20",
        "tokenBalances",
        "tokens",
        "withPriceTokens",
        "assetV2",
    ]

    for key in candidates:
        items = account.get(key)
        if not items:
            continue

        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue

            sym = str(
                item.get("tokenAbbr")
                or item.get("symbol")
                or item.get("tokenName")
                or item.get("name")
                or ""
            ).upper()

            contract = str(
                item.get("contract_address")
                or item.get("tokenAddress")
                or item.get("tokenId")
                or item.get("contract")
                or item.get("token_id")
                or ""
            )

            if _is_usdt_token(sym, contract):
                raw = (
                    item.get("balance")
                    or item.get("value")
                    or item.get("amount")
                    or item.get("tokenValue")
                )
                amt = _raw_to_usdt_amount(
                    raw,
                    item.get("precision") or item.get("decimals") or 6,
                )
                if amt is not None:
                    return amt
    return None


def _parse_usdt_from_token_list(items):
    if not items:
        return None
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return None

    for item in items:
        if not isinstance(item, dict):
            continue
        sym = str(
            item.get("tokenAbbr")
            or item.get("symbol")
            or item.get("token_name")
            or item.get("name")
            or ""
        ).upper()
        contract = str(
            item.get("tokenId")
            or item.get("token_id")
            or item.get("contract_address")
            or item.get("tokenAddress")
            or item.get("contract")
            or ""
        )
        if _is_usdt_token(sym, contract):
            amt = _raw_to_usdt_amount(
                item.get("balance") or item.get("value") or item.get("amount"),
                item.get("decimals") or item.get("precision") or 6,
            )
            if amt is not None:
                return amt
    return None


async def fetch_trc20_usdt_balance(address: str):
    """单独拉取 TRC20 USDT 余额（账户接口常不含 token 列表）。"""
    for contract in USDT_TRC20_CONTRACTS:
        data = await trongrid_get(
            f"/v1/accounts/{address}/trc20/balance",
            params={"contract_address": contract},
        )
        if isinstance(data, dict):
            items = data.get("data")
            if isinstance(items, list) and items:
                parsed = _parse_usdt_from_token_list(items)
                if parsed is not None:
                    return parsed
            if data.get("balance") is not None:
                amt = _raw_to_usdt_amount(data.get("balance"))
                if amt is not None:
                    return amt

    for path in (
        f"/v1/accounts/{address}/assets/trc20",
        f"/v1/accounts/{address}/tokens",
    ):
        data = await trongrid_get(path, params={"limit": 200})
        if isinstance(data, dict):
            items = data.get("data")
            parsed = _parse_usdt_from_token_list(items)
            if parsed is not None:
                return parsed

    try:
        url = f"https://apilist.tronscanapi.com/api/account/tokens?address={address}&start=0&limit=50"
        headers = {"accept": "application/json", "user-agent": "Mozilla/5.0"}
        if TRONGRID_API_KEY:
            headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY
        if HTTP_SESSION:
            async with HTTP_SESSION.get(url, headers=headers) as resp:
                if resp.status == 200:
                    payload = await resp.json()
                    items = payload.get("data") or payload.get("tokens") or []
                    parsed = _parse_usdt_from_token_list(items)
                    if parsed is not None:
                        return parsed
    except Exception as e:
        print("tronscan usdt fetch error:", e)

    return None

async def check_tron_address(address: str):
    def _fetch():
        headers = {
            "accept": "application/json",
            "user-agent": "Mozilla/5.0",
        }
        if TRONGRID_API_KEY:
            headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY

        sources = [
            f"https://api.trongrid.io/v1/accounts/{address}",
            f"https://apilist.tronscanapi.com/api/account?address={address}",
        ]

        for url in sources:
            try:
                r = requests.get(
                    url, timeout=12, headers=headers, verify=certifi.where()
                )
                if not r.ok:
                    continue
                payload = r.json()
                acc = _pick_account(payload)
                if acc:
                    source_name = "trongrid" if "trongrid" in url else "tronscan"
                    return {"source": source_name, "account": acc}
            except Exception as e:
                print("wallet api error:", url, e)
        return None

    result = await asyncio.to_thread(_fetch)
    if not result:
        payload = await trongrid_get(f"/v1/accounts/{address}")
        acc = _pick_account(payload)
        if not acc:
            return None
        result = {"source": "trongrid", "account": acc}

    acc = result["account"]

    trx_balance = None
    try:
        if acc.get("balance") is not None:
            trx_balance = float(acc.get("balance")) / 1_000_000
    except Exception as e:
        print("trx_balance parse error:", e)

    usdt_balance = _parse_trc20_usdt(acc)
    if usdt_balance is None:
        usdt_balance = await fetch_trc20_usdt_balance(address)

    tx_count = (
        acc.get("transaction_count")
        or acc.get("txCount")
        or acc.get("transactionsCount")
        or acc.get("totalTransactionCount")
        or acc.get("trxCount")
        or None
    )
    try:
        tx_count = int(tx_count) if tx_count is not None else None
    except Exception:
        tx_count = None

    create_time = (
        acc.get("create_time")
        or acc.get("createTime")
        or acc.get("create_time_ms")
        or acc.get("createTimeMs")
    )
    latest_time = (
        acc.get("latest_opration_time")
        or acc.get("latestOperationTime")
        or acc.get("latest_operation_time")
        or acc.get("latest_tx_time")
    )

    return {
        "source": result["source"],
        "address": address,
        "trx_balance": trx_balance,
        "usdt_balance": usdt_balance,
        "tx_count": tx_count,
        "create_time": create_time,
        "latest_time": latest_time,
        "raw": acc,
    }

async def get_tron_transactions(address, page=1, page_size=10):
    offset = max(0, (page - 1) * page_size)
    tx_data = await trongrid_get(
        f"/v1/accounts/{address}/transactions",
        params={
            "limit": page_size,
            "only_confirmed": "true",
            "order_by": "block_timestamp,desc",
            "offset": offset,
        },
    )
    return tx_data.get("data", []) if tx_data else []

def format_tron_tx_row(tx):
    try:
        ts = tx.get("block_timestamp")
        dt = datetime.fromtimestamp(ts / 1000, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S") if ts else "-"
        txid = tx.get("txID", "-")
        contract = tx.get("raw_data", {}).get("contract", [])
        tx_type = "-"
        if contract:
            tx_type = contract[0].get("type", "-")
        return f"• {dt} | {escape(tx_type)}\n  <code>{escape(txid)}</code>"
    except Exception:
        return "• 无法解析交易"

def format_address_info_text(address, info, sender_name=None, user_send_count=None):
    if not info:
        return (
            f"🔎 <b>地址查询结果</b>\n\n"
            f"📌 地址：<code>{escape(address)}</code>\n"
            "⚠️ 无法获取链上数据，请稍后重试。"
        )

    trx_balance = info.get("trx_balance", 0)
    usdt_balance = info.get("usdt_balance", 0)
    tx_count = info.get("tx_count", 0)
    first_tx = info.get("create_time") or "-"
    last_active = info.get("latest_time") or "-"
    sig_status = "已签名地址" if (tx_count or 0) > 0 else "新钱包 / 未签名地址"

    lines = [
        "🔎 <b>TRON 地址查询</b>",
        "",
    ]

    if sender_name:
        lines.append(f"👤 查询人：<code>{escape(sender_name)}</code>")

    if user_send_count is not None:
        lines.append(f"📌 本群发送次数：<b>{user_send_count}</b> 次")

    lines.extend([
        f"📌 地址：<code>{escape(address)}</code>",
        f"💰 TRX：<b>{fmt_num(trx_balance)}</b>",
        f"💰 USDT：<b>{fmt_num(usdt_balance)}</b>",
        f"📊 交易次数：<b>{tx_count if tx_count is not None else 0}</b>",
        f"🔰 状态：<b>{sig_status}</b>",
        f"📡 数据来源：<b>{escape(str(info.get('source', '-')))}</b>",
        f"⏰ 首次交易：<b>{fmt_ts(first_tx)}</b>",
        f"🌟 最后活跃：<b>{fmt_ts(last_active)}</b>",
    ])

    return "\n".join(lines)

def make_wallet_card_image(
    address,
    sender_name,
    user_send_count=0,
    trx_balance=None,
    usdt_balance=None,
    tx_count=None,
    source="trongrid",
    create_time=None,
    latest_time=None,
):
    width, height = 1080, 1350

    top_green = (18, 185, 150)
    top_green2 = (16, 165, 138)
    body_bg = (20, 30, 44)
    panel_bg = (26, 40, 58)
    panel_bg2 = (30, 46, 66)

    white = (245, 248, 250)
    mute = (165, 180, 190)
    gold = (245, 198, 76)
    blue = (120, 185, 255)
    green = (100, 235, 160)
    red = (255, 120, 120)
    yellow = (255, 210, 90)

    img = Image.new("RGB", (width, height), body_bg)
    draw = ImageDraw.Draw(img)

    # nền chuyển màu
    for y in range(height):
        if y < 330:
            r = int(top_green[0] * (1 - y / 330) + top_green2[0] * (y / 330))
            g = int(top_green[1] * (1 - y / 330) + top_green2[1] * (y / 330))
            b = int(top_green[2] * (1 - y / 330) + top_green2[2] * (y / 330))
            draw.line([(0, y), (width, y)], fill=(r, g, b))
        else:
            draw.line([(0, y), (width, y)], fill=body_bg)

    font_candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKSC-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "arial.ttf",
    ]

    def load_font(size):
        for fp in font_candidates:
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
        return ImageFont.load_default()

    font_title = load_font(54)
    font_sub = load_font(28)
    font_mid = load_font(32)

    def box(x1, y1, x2, y2, radius=26, fill=panel_bg, outline=None, width=2):
        draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=fill, outline=outline, width=width)

    def text(x, y, s, font, fill=white):
        draw.text((x, y), str(s), font=font, fill=fill)

    def center_text(y, s, font, fill=white):
        bbox = draw.textbbox((0, 0), str(s), font=font)
        w = bbox[2] - bbox[0]
        x = (width - w) // 2
        draw.text((x, y), str(s), font=font, fill=fill)

    def right_badge(x2, y1, label, fill_bg=(48, 78, 118), fill_text=white, pad_x=16, pad_y=10, radius=18):
        bbox = draw.textbbox((0, 0), str(label), font=font_sub)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x1 = x2 - tw - pad_x * 2
        y2 = y1 + th + pad_y * 2
        draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=fill_bg)
        draw.text((x1 + pad_x, y1 + pad_y - 1), str(label), font=font_sub, fill=fill_text)

    def fmt_time_local(ts):
        if not ts:
            return "N/A"
        try:
            ts = int(ts)
            if ts > 10_000_000_000:
                ts = ts // 1000
            return datetime.fromtimestamp(ts, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return "N/A"

    # phân tích risk
    risk_level, _warnings = wallet_risk_analysis({
        "tx_count": tx_count,
        "trx_balance": trx_balance,
        "usdt_balance": usdt_balance,
        "latest_time": latest_time,
    })

    risk_color = green
    if "高风险" in risk_level:
        risk_color = red
    elif "谨慎" in risk_level:
        risk_color = yellow

    tx_status = "已签名地址" if (tx_count or 0) > 0 else "新钱包 / 未签名地址"
    tx_status_color = green if (tx_count or 0) > 0 else yellow

    # header
    box(40, 35, 1040, 300, radius=36, fill=top_green2, outline=(255, 255, 255, 40), width=2)
    box(90, 198, 990, 250, radius=18, fill=(60, 130, 108), outline=(220, 255, 240), width=2)

    center_text(70, "USDT防篡改验证核对", font_title, fill=white)
    center_text(144, "《请双方谨慎核对地址是否与图中一致，如有误请立即停止付款》", font_sub, fill=(232, 247, 242))
    center_text(209, address, font_mid, fill=white)
    center_text(258, f"查询人: {sender_name}", font_sub, fill=(225, 245, 240))

    right_badge(
        1000,
        52,
        f"{sender_name} · 第 {user_send_count} 次",
        fill_bg=(40, 72, 108),
        fill_text=white,
    )

    # thân chính
    box(40, 330, 1040, 1140, radius=34, fill=panel_bg, outline=(42, 70, 90), width=2)
    text(70, 360, "🔎 查询地址：", font_mid, fill=white)
    text(250, 360, address, font_mid, fill=blue)

    box(60, 460, 1020, 1030, radius=28, fill=panel_bg2, outline=(55, 90, 110), width=2)

    rows = [
        ("🛡 风险等级", risk_level, risk_color),
        ("💡 交易次数", str(tx_count if tx_count is not None else "N/A"), white),
        ("⏰ 首次交易", fmt_time_local(create_time), white),
        ("🌟 最后活跃", fmt_time_local(latest_time), white),
        ("🔰 签名状态", tx_status, tx_status_color),
        ("💰 USDT 余额", f"{fmt_num(usdt_balance)} USDT", gold),
        ("💰 TRX 余额", f"{fmt_num(trx_balance)} TRX", gold),
        ("📡 数据来源", str(source), mute),
    ]

    y = 500
    gap = 64
    for label, value, value_color in rows:
        text(85, y, f"{label}：", font_mid, fill=white)
        text(330, y, value, font_mid, fill=value_color)
        y += gap

    bio = BytesIO()
    img.save(bio, "PNG")
    bio.seek(0)
    return BufferedInputFile(bio.read(), filename="usdt_check_cn.png")

# ================= ADDRESS QUERY =================
@dp.message(lambda m: is_private(m) and m.text in ("地址查询", "🔍 地址查询", "📍 地址查询"))
async def menu_address_query(m: types.Message, state: FSMContext):
    if not m.from_user:
        return

    if not can_use_bot_ops(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")

    await state.set_state(AddressQueryFSM.waiting_address)
    await m.reply(
        "🔍 <b>地址查询</b>\n\n请直接发送 TRON 地址进行查询。\n\n示例：\n<code>Txxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</code>",
        parse_mode="HTML",
    )

@dp.message(AddressQueryFSM.waiting_address)
async def receive_address_query(m: types.Message, state: FSMContext):
    if not m.from_user:
        return

    if not can_use_bot_ops(m.from_user.id) and not has_bot_access(m.from_user.id):
        return await m.reply("❌ 无权限")

    addr = (m.text or "").strip()
    if not is_tron_address(addr):
        return await m.reply(
            "❌ 地址格式不正确，请重新输入 TRON 地址。\n示例：<code>Txxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</code>",
            parse_mode="HTML",
        )

    wait_msg = await m.reply("⏳ 正在查询链上数据，请稍候...")

    try:
        info = await check_tron_address(addr)
        sender_name = m.from_user.full_name or (m.from_user.username or str(m.from_user.id))

        try:
            add_wallet_check(
                chat_id=m.chat.id,
                user_id=m.from_user.id,
                username=m.from_user.username or "",
                full_name=m.from_user.full_name or "",
                address=addr,
                trx_balance=info.get("trx_balance") if info else None,
                usdt_balance=info.get("usdt_balance") if info else None,
                tx_count=info.get("tx_count") if info else None,
            )
        except Exception as e:
            print("private add_wallet_check error:", e)

        user_send_count = get_user_wallet_send_count(m.from_user.id, None)

        text_html = format_address_info_text(
            addr,
            info,
            sender_name=sender_name,
            user_send_count=user_send_count,
        )

        if info:
            text_html += build_wallet_warning_html(info)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Tronscan", url=f"https://tronscan.org/#/address/{addr}")],
            [InlineKeyboardButton(text="📄 最近链上交易", callback_data=f"addr:tx:{addr}:1")],
            [InlineKeyboardButton(text="🔄 重新查询", callback_data="addr:again")],
            [InlineKeyboardButton(text="⬅️ 返回菜单", callback_data="addr:back")],
        ])

        try:
            photo = make_wallet_card_image(
                address=addr,
                sender_name=sender_name,
                user_send_count=user_send_count,
                trx_balance=info.get("trx_balance") if info else None,
                usdt_balance=info.get("usdt_balance") if info else None,
                tx_count=info.get("tx_count") if info else None,
                source=info.get("source") if info else "unknown",
                create_time=info.get("create_time") if info else None,
                latest_time=info.get("latest_time") if info else None,
            )
            await m.answer_photo(photo=photo, caption=text_html, reply_markup=kb, parse_mode="HTML")
        except Exception as e:
            print("private send wallet photo error:", e)
            await m.reply(text_html, parse_mode="HTML", reply_markup=kb)

    except Exception as e:
        print("on-chain query error:", e)
        await m.reply(
            f"🔎 查询地址：<code>{escape(addr)}</code>\n\n⚠️ 查询失败，请稍后再试。",
            parse_mode="HTML",
        )

    await state.clear()

    try:
        await wait_msg.delete()
    except Exception:
        pass


@dp.callback_query(lambda c: c.data == "addr:again")
async def addr_again_cb(c: types.CallbackQuery, state: FSMContext):
    if not c.message:
        return
    await state.set_state(AddressQueryFSM.waiting_address)
    await c.message.answer("请重新发送 TRON 地址。")
    await c.answer()

@dp.callback_query(lambda c: c.data == "addr:back")
async def addr_back_cb(c: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if c.message:
        await c.message.answer("✅ 已返回主菜单")
    await c.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith("addr:tx:"))
async def addr_tx_cb(c: types.CallbackQuery):
    if not c.message or not c.data:
        return

    parts = c.data.split(":")
    if len(parts) < 4:
        return await c.answer("数据错误", show_alert=True)

    address = parts[2].strip()
    if not is_tron_address(address):
        return await c.answer("地址错误", show_alert=True)

    try:
        page = int(parts[3])
        if page < 1:
            page = 1
    except Exception:
        page = 1

    await c.message.answer("⏳ 正在加载交易记录，请稍候...")

    try:
        txs = await get_tron_transactions(address, page=page, page_size=10)
        if not txs:
            await c.message.answer(
                f"🔎 查询地址：<code>{escape(address)}</code>\n📄 当前页无交易记录",
                parse_mode="HTML"
            )
            return await c.answer()

        text = f"🔎 查询地址：<code>{escape(address)}</code>\n🗂 当前页码：第 {page} 页\n\n📄 交易记录：\n"
        for tx in txs:
            text += format_tron_tx_row(tx) + "\n\n"

        await c.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=tx_history_kb(address, page)
        )
    except Exception as e:
        print("addr tx cb error:", e)
        await c.message.answer("⚠️ 交易记录加载失败，请稍后再试。")

    await c.answer()


# ================= WALLET AUTO CHECK IN GROUP =================
@dp.message(lambda m: is_group_message(m) and m.text and extract_tron_address(m.text) is not None)
async def tron_address_check_handler(m: types.Message):
    if should_ignore_message(m):
        return

    ensure_group(m)

    address = extract_tron_address(m.text)
    if not address:
        return

    status_msg = await m.reply("⏳ 正在查询地址，请稍候...")

    try:
        info = await check_tron_address(address)
        if not info:
            try:
                return await status_msg.edit_text("❌ 未能获取钱包数据，请稍后再试。")
            except Exception:
                return

        tx_count = info.get("tx_count")
        trx_balance = info.get("trx_balance")
        usdt_balance = info.get("usdt_balance")

        try:
            add_wallet_check(
                chat_id=m.chat.id,
                user_id=m.from_user.id,
                username=m.from_user.username or "",
                full_name=m.from_user.full_name or "",
                address=address,
                trx_balance=trx_balance,
                usdt_balance=usdt_balance,
                tx_count=tx_count,
            )
        except Exception as e:
            print("add_wallet_check error:", e)

        sender_name = m.from_user.full_name or (m.from_user.username or "Unknown")
        user_send_count = get_user_wallet_send_count(m.from_user.id, m.chat.id)

        caption = format_address_info_text(
            address,
            info,
            sender_name=sender_name,
            user_send_count=user_send_count,
        )
        caption += build_wallet_warning_html(info)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Tronscan", url=f"https://tronscan.org/#/address/{address}")],
            [InlineKeyboardButton(text="📄 最近链上交易", callback_data=f"addr:tx:{address}:1")],
        ])

        try:
            photo = make_wallet_card_image(
                address=address,
                sender_name=sender_name,
                user_send_count=user_send_count,
                trx_balance=trx_balance,
                usdt_balance=usdt_balance,
                tx_count=tx_count,
                source=info.get("source"),
                create_time=info.get("create_time"),
                latest_time=info.get("latest_time"),
            )
            await m.answer_photo(
                photo=photo,
                caption=caption,
                reply_markup=kb,
                parse_mode="HTML",
            )
        except Exception as e:
            print("send wallet photo error:", e)
            await m.reply(
                caption,
                reply_markup=kb,
                parse_mode="HTML",
            )

    except Exception as e:
        print("tron_address_check_handler error:", e)
        try:
            await status_msg.edit_text("❌ 查询地址时发生错误。")
        except Exception:
            pass

    try:
        await status_msg.delete()
    except Exception:
        pass


# ================= WALLET CHECK LOGS =================
def build_wallet_logs_text(rows, page, total):
    text_lines = [
        "📄 <b>钱包查询记录</b>",
        f"📍 当前页码：第 <b>{page + 1}</b> 页",
        f"📊 总记录数：<b>{total}</b>",
        "",
    ]

    buttons = []

    for row in rows:
        _id, chat_id, user_id, username, full_name, address, trx_balance, usdt_balance, tx_count, created_at = row
        sender = full_name or username or str(user_id)
        tm = fmt_ts(created_at)
        status_text = "新钱包 / 未签名地址" if tx_count in (None, 0) else "已签名地址"

        text_lines.append(
            f"🕒 {tm}\n"
            f"👥 群组：<code>{chat_id}</code>\n"
            f"👤 用户：<code>{user_id}</code> {escape(sender)}\n"
            f"🏷 用户名：@{escape(username or '-')}\n"
            f"📌 地址：<code>{escape(address)}</code>\n"
            f"💰 TRX：<b>{fmt_num(trx_balance)}</b> | USDT：<b>{fmt_num(usdt_balance)}</b>\n"
            f"📊 交易次数：<b>{tx_count if tx_count is not None else 'N/A'}</b>\n"
            f"🔰 状态：<b>{status_text}</b>\n"
            f"{'—' * 26}"
        )

        buttons.append([
            InlineKeyboardButton(
                text=f"🔗 {address[:10]}...",
                url=f"https://tronscan.org/#/address/{address}",
            )
        ])

    return "\n\n".join(text_lines), buttons


@dp.message(lambda m: m.text == "交易记录")
async def wallet_logs_menu(m: types.Message):
    if not m.from_user:
        return
    if not can_use_manage_panel(m.from_user.id):
        return await m.reply("❌ 无权限")

    rows = get_wallet_checks_page(limit=10, offset=0)
    if not rows:
        return await m.reply("暂无历史记录。")

    total = count_wallet_checks()
    text, buttons = build_wallet_logs_text(rows, page=0, total=total)

    if total > 10:
        buttons.append([InlineKeyboardButton(text="下一页 ➡️", callback_data="wallet:recent:1")])

    await m.reply(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("wallet:recent:"))
async def wallet_logs_cb(c: types.CallbackQuery):
    if not c.message or not c.from_user:
        return

    if not can_use_manage_panel(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    try:
        page = int(c.data.split(":")[-1])
    except Exception:
        page = 0

    limit = 10
    offset = page * limit

    rows = get_wallet_checks_page(limit=limit, offset=offset)
    if not rows:
        await c.message.edit_text("暂无历史记录。")
        return await c.answer()

    total = count_wallet_checks()
    has_prev = page > 0
    has_next = offset + limit < total

    text, buttons = build_wallet_logs_text(rows, page=page, total=total)

    nav = []
    if has_prev:
        nav.append(InlineKeyboardButton(text="⬅️ 上一页", callback_data=f"wallet:recent:{page - 1}"))
    if has_next:
        nav.append(InlineKeyboardButton(text="下一页 ➡️", callback_data=f"wallet:recent:{page + 1}"))
    if nav:
        buttons.append(nav)

    await c.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await c.answer()

# ================= MANAGE PANEL =================
@dp.message(lambda m: m.text in ("管理面板", "管理员快捷面板", "续费管理面板", "🛠 管理面板"))
async def manage_panel_cmd(m: types.Message):
    if not can_use_manage_panel(m.from_user.id):
        return await m.reply(deny_text())

    await m.reply(
        "🛠 <b>管理面板</b>\n\n"
        "• ➕/➖ 添加/删除 Bot 管理员（私聊发用户 ID）\n"
        "• 📋 管理员列表 · 订单管理\n"
        "• 📜 交易历史 / 群组记录：主菜单点「📜 交易历史」\n"
        "查询工具请发「显示菜单」。",
        reply_markup=manage_panel_kb(m.from_user.id),
        parse_mode="HTML",
    )
    if can_use_tools_panel(m.from_user.id):
        await m.answer(
            "🧭 <b>管理员功能面板</b>",
            reply_markup=admin_tools_panel_kb(),
            parse_mode="HTML",
        )

@dp.callback_query(lambda c: c.data == "manage:list_admin")
async def manage_list_admin_cb(c: types.CallbackQuery):
    if not c.from_user or not can_use_manage_panel(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    rows = get_all_admins()
    lines = ["📋 <b>管理员列表</b>", ""]

    if BOT_OWNER_ID:
        lines.append(f"• <code>{BOT_OWNER_ID}</code> — owner")
    if SUPER_ADMIN_ID and SUPER_ADMIN_ID != BOT_OWNER_ID:
        lines.append(f"• <code>{SUPER_ADMIN_ID}</code> — super(env)")

    for uid, role in rows:
        if uid in (BOT_OWNER_ID, SUPER_ADMIN_ID):
            continue
        lines.append(f"• <code>{uid}</code> — {role}")

    await c.message.answer("\n".join(lines), parse_mode="HTML")
    await c.answer()

@dp.callback_query(lambda c: c.data == "manage:add_admin")
async def manage_add_admin_cb(c: types.CallbackQuery, state: FSMContext):
    if not c.from_user or not can_manage_admins(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    await state.set_state(AdminFSM.waiting_add_admin)
    await c.message.answer("➕ <b>添加管理员</b>\n\n请回复目标用户消息，或直接发送用户ID。", parse_mode="HTML")
    await c.answer()

@dp.message(lambda m: is_private(m), AdminFSM.waiting_add_admin)
async def receive_add_admin(m: types.Message, state: FSMContext):
    if not can_manage_admins(m.from_user.id):
        return await m.reply(deny_text())

    uid = None
    if m.reply_to_message and m.reply_to_message.from_user:
        uid = m.reply_to_message.from_user.id
    elif m.text and m.text.strip().isdigit():
        uid = int(m.text.strip())

    if not uid:
        return await m.reply("❌ 格式错误，请回复某人消息或发送用户ID。")

    add_admin(uid, "admin")
    await state.clear()
    await m.reply(f"✅ 已添加管理员：<code>{uid}</code>", parse_mode="HTML")

@dp.callback_query(lambda c: c.data == "manage:del_admin")
async def manage_del_admin_cb(c: types.CallbackQuery, state: FSMContext):
    if not c.from_user or not can_manage_admins(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    await state.set_state(AdminFSM.waiting_del_admin)
    await c.message.answer("➖ <b>删除管理员</b>\n\n请回复目标用户消息，或直接发送用户ID。", parse_mode="HTML")
    await c.answer()

@dp.message(lambda m: is_private(m), AdminFSM.waiting_del_admin)
async def receive_del_admin(m: types.Message, state: FSMContext):
    if not can_manage_admins(m.from_user.id):
        return await m.reply(deny_text())

    uid = None
    if m.reply_to_message and m.reply_to_message.from_user:
        uid = m.reply_to_message.from_user.id
    elif m.text and m.text.strip().isdigit():
        uid = int(m.text.strip())

    if not uid:
        return await m.reply("❌ 格式错误，请回复某人消息或发送用户ID。")

    remove_admin(uid)
    await state.clear()
    await m.reply(f"✅ 已删除管理员：<code>{uid}</code>", parse_mode="HTML")

@dp.callback_query(lambda c: c.data == "manage:create_code")
async def manage_create_code_cb(c: types.CallbackQuery, state: FSMContext):
    if not c.from_user or not can_manage_codes(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    await state.set_state(AdminFSM.waiting_trial_code)
    await c.message.answer("🔑 <b>创建续费码</b>\n\n请发送新的续费码，例如：<code>ABC123</code>", parse_mode="HTML")
    await c.answer()

@dp.message(AdminFSM.waiting_trial_code)
async def receive_manage_trial_code(m: types.Message, state: FSMContext):
    if not can_manage_codes(m.from_user.id):
        return await m.reply(deny_text())

    code = (m.text or "").strip()
    if not code:
        return await m.reply("❌ 请输入有效续费码。")

    set_trial_code(code)
    await state.clear()
    await m.reply(f"✅ 已设置续费码：<code>{code}</code>", parse_mode="HTML")

@dp.callback_query(lambda c: c.data == "manage:revoke_code")
async def manage_revoke_code_cb(c: types.CallbackQuery):
    if not c.from_user or not can_manage_codes(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    set_trial_code("")
    await c.message.answer("🗑 <b>续费码已回收</b>", parse_mode="HTML")
    await c.answer()

# ================= PAYMENT ADDRESS =================
@dp.message(lambda m: m.text and m.text.strip().startswith("设置收款地址"))
async def cmd_set_payment_address(m: types.Message):
    if not m.from_user or not (is_bot_owner(m.from_user.id) or is_super_admin(m.from_user.id)):
        return await m.reply("❌ 仅 owner / 超级管理员可设置收款地址。")

    parts = (m.text or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        current = get_payment_address() or "（未设置）"
        return await m.reply(
            "用法：\n<code>设置收款地址 Txxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</code>\n\n"
            f"当前地址：\n<code>{escape(current)}</code>",
            parse_mode="HTML",
        )

    addr = parts[1].strip()
    if not is_tron_address(addr):
        return await m.reply("❌ TRON 地址格式不正确，请检查后重试。")

    set_setting(-1, "payment_address", addr)
    await m.reply(
        f"✅ 自助续费收款地址已更新：\n<code>{escape(addr)}</code>\n\n"
        "用户打开「自助续费」即可看到此地址。",
        parse_mode="HTML",
    )


# ================= RENT MENU =================
@dp.message(lambda m: m.text in ("🔑 自助续费", "自助续费", "续费/租用"))
async def menu_rent(m: types.Message):
    await m.answer(rent_main_text(), reply_markup=rent_main_kb(), parse_mode="HTML")

@dp.callback_query(lambda c: c.data == "rent:main")
async def rent_main_cb(c: types.CallbackQuery):
    if not c.message:
        return
    await c.message.answer(rent_main_text(), reply_markup=rent_main_kb(), parse_mode="HTML")
    await c.answer()

@dp.callback_query(lambda c: c.data == "rent:back")
async def rent_back_cb(c: types.CallbackQuery):
    if not c.message:
        return
    await c.message.answer(rent_main_text(), reply_markup=rent_main_kb(), parse_mode="HTML")
    await c.answer()

@dp.callback_query(lambda c: c.data in ("rent:group_admin", "rent:computer", "rent:translator"))
async def rent_category_cb(c: types.CallbackQuery):
    if not c.message:
        return
    category_key = c.data.split(":")[1]
    title = RENT_CATEGORIES.get(category_key, {}).get("title", "套餐")
    await c.message.answer(
        f"📦 <b>{title}</b>\n\n{rent_address_block()}\n\n请选择租用时长：",
        reply_markup=rent_plan_kb(category_key),
        parse_mode="HTML",
    )
    await c.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith("rent:plan:"))
async def rent_plan_cb(c: types.CallbackQuery):
    if not c.message or not c.from_user:
        return

    if not get_payment_address():
        return await c.answer("收款地址未配置，请联系管理员", show_alert=True)

    _, _, category_key, plan_key = c.data.split(":", 3)
    cat = RENT_CATEGORIES.get(category_key)
    plan = RENT_PLANS.get(plan_key)

    if not cat or not plan:
        return await c.answer("套餐不存在", show_alert=True)

    category_title = cat["title"]
    plan_label = plan["label"]
    amount = plan["amount"]

    order_code = create_rental_order(
        user_id=c.from_user.id,
        username=c.from_user.username or "",
        full_name=c.from_user.full_name or "",
        category_key=category_key,
        category_title=category_title,
        plan_key=plan_key,
        plan_label=plan_label,
        amount=amount,
        note="rent_order",
    )

    text = rent_payment_text(category_key, plan_key, order_code)
    await c.message.answer(text, reply_markup=rent_payment_kb(amount), parse_mode="HTML")
    await c.answer("✅ 已生成订单")

# ================= ORDER MANAGEMENT =================
@dp.callback_query(lambda c: c.data == "order:list_pending")
async def order_list_pending_cb(c: types.CallbackQuery):
    if not c.message or not c.from_user:
        return

    if not can_use_manage_panel(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    rows = get_pending_rental_orders(limit=10)
    if not rows:
        await c.message.answer("暂无待支付订单")
        return await c.answer()

    buttons = []
    for order_code, user_id, username, full_name, category_title, plan_label, amount, created_at in rows:
        buttons.append([
            InlineKeyboardButton(
                text=f"🧾 {order_code} | {plan_label} | {amount}U",
                callback_data=f"order:view:{order_code}",
            )
        ])

    await c.message.answer("🧾 <b>待支付订单</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await c.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith("order:view:"))
async def view_order_cb(c: types.CallbackQuery):
    if not c.message or not c.from_user:
        return

    if not can_use_manage_panel(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    order_code = c.data.split(":", 2)[2]
    row = get_rental_order(order_code)
    if not row:
        return await c.answer("订单不存在", show_alert=True)

    (
        order_code, user_id, username, full_name, category_key, category_title,
        plan_key, plan_label, amount, status, created_at, paid_at, expires_at, note
    ) = row

    created_str = datetime.fromtimestamp(created_at, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    paid_str = "-" if not paid_at else datetime.fromtimestamp(paid_at, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    expire_str = "-" if not expires_at else datetime.fromtimestamp(expires_at, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

    text = (
        f"🧾 <b>订单详情</b>\n\n"
        f"订单号：<code>{order_code}</code>\n"
        f"用户：<code>{user_id}</code> @{username or '-'}\n"
        f"姓名：{full_name or '-'}\n"
        f"类型：{category_title}\n"
        f"套餐：{plan_label}\n"
        f"金额：<b>{amount} U</b>\n"
        f"状态：<b>{status}</b>\n"
        f"创建时间：{created_str}\n"
        f"支付时间：{paid_str}\n"
        f"到期时间：{expire_str}\n"
    )

    rows = []
    if status == "pending":
        rows.append([
            InlineKeyboardButton(text="✅ 确认已付款", callback_data=f"order:approve:{order_code}"),
            InlineKeyboardButton(text="❌ 拒绝", callback_data=f"order:reject:{order_code}"),
        ])
    rows.append([InlineKeyboardButton(text="⬅️ 返回订单列表", callback_data="order:list_pending")])

    await c.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
    await c.answer()

@dp.callback_query(lambda c: c.data and c.data.startswith("order:approve:"))
async def order_approve_cb(c: types.CallbackQuery):
    if not c.message or not c.from_user:
        return

    if not can_use_manage_panel(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    order_code = c.data.split(":", 2)[2]
    row = get_rental_order(order_code)
    if not row:
        return await c.answer("订单不存在", show_alert=True)

    (
        order_code, user_id, username, full_name, category_key, category_title,
        plan_key, plan_label, amount, status, created_at, paid_at, expires_at, note
    ) = row

    if status == "paid":
        return await c.answer("订单已支付", show_alert=True)

    row2, new_expires_at, err = await activate_rental_order(order_code, granted_by=c.from_user.id)
    if err:
        return await c.answer(err, show_alert=True)

    expire_str = datetime.fromtimestamp(new_expires_at, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

    try:
        await bot.send_message(
            user_id,
            (
                "✅ <b>续费/租用成功</b>\n\n"
                f"订单号：<code>{order_code}</code>\n"
                f"类型：{category_title}\n"
                f"套餐：{plan_label}\n"
                f"到期时间：<b>{expire_str}</b>\n\n"
                "权限已自动开通/续期。"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        print("notify paid user failed:", e)

    await c.message.answer(
        (
            f"✅ <b>已确认付款</b>\n\n"
            f"订单号：<code>{order_code}</code>\n"
            f"用户：<code>{user_id}</code>\n"
            f"到期时间：<b>{expire_str}</b>\n"
            "权限已开通/已续期。"
        ),
        parse_mode="HTML",
    )
    await c.answer("✅ 已开通/续期")

@dp.callback_query(lambda c: c.data and c.data.startswith("order:reject:"))
async def order_reject_cb(c: types.CallbackQuery):
    if not c.message or not c.from_user:
        return

    if not can_use_manage_panel(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    order_code = c.data.split(":", 2)[2]
    row = get_rental_order(order_code)
    if not row:
        return await c.answer("订单不存在", show_alert=True)

    (
        order_code, user_id, username, full_name, category_key, category_title,
        plan_key, plan_label, amount, status, created_at, paid_at, expires_at, note
    ) = row

    if status == "paid":
        return await c.answer("订单已支付", show_alert=True)

    mark_rental_order_rejected(order_code)

    await c.message.answer(
        (
            f"❌ <b>订单已拒绝</b>\n\n"
            f"订单号：<code>{order_code}</code>\n"
            f"用户：<code>{user_id}</code>\n"
            f"套餐：{plan_label}\n"
            f"金额：<b>{amount} U</b>\n"
            f"状态：<b>rejected</b>"
        ),
        parse_mode="HTML",
    )

    try:
        await bot.send_message(
            user_id,
            (
                "❌ <b>您的订单未通过</b>\n\n"
                f"订单号：<code>{order_code}</code>\n"
                f"套餐：{plan_label}\n"
                "如有疑问，请联系管理员。"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        print("notify reject user failed:", e)

    await c.answer("✅ 已拒绝")

@dp.message(lambda m: m.text in ("订单历史", "租用历史", "历史订单"))
async def order_history_cmd(m: types.Message):
    if not can_use_manage_panel(m.from_user.id):
        return await m.reply("❌ 无权限")
    await m.reply("🧾 <b>订单历史</b>\n\n请选择查看类型：", reply_markup=order_history_kb(), parse_mode="HTML")

@dp.callback_query(lambda c: c.data and c.data.startswith("order:history:"))
async def order_history_cb(c: types.CallbackQuery):
    if not c.message or not c.from_user:
        return

    if not can_use_manage_panel(c.from_user.id):
        return await c.answer("无权限", show_alert=True)

    status = c.data.split(":")[2]
    if status == "all":
        rows = get_rental_orders_by_status(None, limit=20)
        title = "📦 全部订单"
    else:
        rows = get_rental_orders_by_status(status, limit=20)
        title = f"📦 {status}"

    if not rows:
        await c.message.answer(f"{title}\n\n暂无记录")
        return await c.answer()

    text = f"{title}\n\n"
    for row in rows:
        order_code, user_id, username, full_name, category_title, plan_label, amount, st, created_at, paid_at, expires_at = row
        created_str = datetime.fromtimestamp(created_at, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        paid_str = "-" if not paid_at else datetime.fromtimestamp(paid_at, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        expire_str = "-" if not expires_at else datetime.fromtimestamp(expires_at, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

        text += (
            f"• <code>{order_code}</code>\n"
            f"  {category_title} | {plan_label} | {amount}U | {st}\n"
            f"  用户：<code>{user_id}</code> @{username or '-'}\n"
            f"  创建：{created_str}\n"
            f"  支付：{paid_str}\n"
            f"  到期：{expire_str}\n\n"
        )

    await send_long_text(c.message.chat.id, text, parse_mode="HTML")
    await c.answer()

# ================= BROADCAST =================
@dp.message(lambda m: m.text in ("📣 群发广播", "群发广播"))
async def menu_broadcast(m: types.Message, state: FSMContext):
    if is_private(m):
        if get_user_role(m.from_user.id) not in ("owner", "super"):
            return await m.answer("❌ 只有超级管理员可在私聊里全局群发。")
        scope = "all"
        target_chat_id = -1
    else:
        ensure_group(m)
        if not can_use_manage_panel(m.from_user.id):
            return await m.reply("❌ 无权限")
        scope = "current"
        target_chat_id = m.chat.id

    await state.set_state(BroadcastFSM.waiting_content)
    await state.update_data(scope=scope, target_chat_id=target_chat_id, creator_id=m.from_user.id)
    await m.reply("📢 请发送要广播的内容。")

@dp.message(BroadcastFSM.waiting_content)
async def broadcast_receive_content(m: types.Message, state: FSMContext):
    data = await state.get_data()
    creator_id = data.get("creator_id")

    if creator_id and m.from_user and m.from_user.id != creator_id:
        return

    scope = data.get("scope", "current")
    target_chat_id = data.get("target_chat_id", m.chat.id)

    await state.update_data(
        source_chat_id=m.chat.id,
        source_message_id=m.message_id,
        scope=scope,
        target_chat_id=target_chat_id,
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="确认群发(普通)", callback_data="bc:copy"),
            InlineKeyboardButton(text="确认群发(转发)", callback_data="bc:fwd"),
        ],
        [InlineKeyboardButton(text="取消群发", callback_data="bc:cancel")],
    ])

    await m.reply("请确认广播方式：", reply_markup=kb)
    await state.set_state(BroadcastFSM.waiting_confirm)

@dp.callback_query(lambda c: c.data and c.data.startswith("bc:"))
async def broadcast_callback(c: types.CallbackQuery, state: FSMContext):
    if not c.from_user:
        return

    data = await state.get_data()
    creator_id = data.get("creator_id")

    if creator_id and c.from_user.id != creator_id:
        return await c.answer("❌ 无权限", show_alert=True)

    scope = data.get("scope", "current")
    source_chat_id = data.get("source_chat_id")
    source_message_id = data.get("source_message_id")

    if c.data == "bc:cancel":
        await state.clear()
        if c.message:
            await c.message.edit_text("✅ 已取消群发")
        return await c.answer()

    if c.data not in ("bc:copy", "bc:fwd"):
        return await c.answer()

    if scope == "all":
        targets = [g[0] for g in get_groups()]
    else:
        target_chat_id = data.get("target_chat_id")
        targets = [target_chat_id] if target_chat_id is not None else []

    if not source_chat_id or not source_message_id:
        await state.clear()
        if c.message:
            await c.message.edit_text("❌ 广播内容已失效，请重新发送。")
        return await c.answer()

    ok = 0
    fail = 0
    for chat_id in targets:
        try:
            if c.data == "bc:copy":
                await bot.copy_message(chat_id=chat_id, from_chat_id=source_chat_id, message_id=source_message_id)
            else:
                await bot.forward_message(chat_id=chat_id, from_chat_id=source_chat_id, message_id=source_message_id)
            ok += 1
        except Exception as e:
            fail += 1
            print("broadcast error:", e)

    await state.clear()
    if c.message:
        await c.message.edit_text(f"✅ 群发完成\n成功：{ok}\n失败：{fail}")
    await c.answer()

# ================= TRANSACTION HISTORY WEB =================
@dp.message(lambda m: m.text in ("交易历史", "📜 交易历史", "群组记录", "📋 群组记录"))
async def menu_history(m: types.Message):
    if not can_view_transaction_history(m.from_user.id):
        return await m.reply(deny_text())

    await m.reply(
        "📜 <b>交易历史</b>\n\n请选择一个群组，点击后将打开网页历史记录。",
        reply_markup=history_groups_kb(),
        parse_mode="HTML",
    )

@dp.callback_query(lambda c: c.data == "report:full")
async def report_full_cb(c: types.CallbackQuery):
    if not c.message or not c.from_user:
        return
    if not is_group_message(c.message):
        return
    if not is_admin_or_operator(c.message.chat.id, c.from_user):
        return await c.answer("无权限", show_alert=True)

    start_ts, end_ts = day_range()
    await c.message.reply(
        report_text(c.message.chat.id, start_ts, end_ts, title="今日账单"),
        reply_markup=report_kb(c.message.chat.id),
    )
    await c.answer()

# ================= LEDGER HANDLER =================
@dp.message()
async def ledger_handler(m: types.Message):
    if should_ignore_message(m):
        return
    if not is_group_message(m):
        return
    if not m.text:
        return
    if m.text.startswith("/"):
        return

    ensure_group(m)

    if not get_enabled(m.chat.id):
        return

    txt = m.text.strip()

    if txt in ("+0", "-0", "0"):
        start_ts, end_ts = day_range()
        await send_long_text(
            m.chat.id,
            report_text(m.chat.id, start_ts, end_ts, title="今日账单"),
            reply_markup=report_kb(m.chat.id),
        )
        return

    if txt.startswith("P+") or txt.startswith("P-"):
        if not is_admin_or_operator(m.chat.id, m.from_user):
            return await m.reply(deny_text())

        parsed = parse_amount_expr(txt[1:], m.chat.id, default_direct_unit=True)
        if not parsed:
            return await m.reply("❌ 格式错误")

        target = None
        if m.reply_to_message and m.reply_to_message.from_user:
            target = m.reply_to_message.from_user.full_name

        add_transaction(
            chat_id=m.chat.id,
            user_id=m.from_user.id,
            username=m.from_user.username or "",
            display_name=m.from_user.full_name or "",
            target_name=target,
            kind="reserve",
            raw_amount=parsed["raw_amount"],
            unit_amount=parsed["unit_amount"],
            rate_used=parsed["rate_used"],
            fee_used=parsed["fee_used"],
            note="寄存",
            original_text=txt,
        )

        start_ts, end_ts = day_range()
        await send_long_text(
            m.chat.id,
            report_text(m.chat.id, start_ts, end_ts, title="今日账单"),
            reply_markup=report_kb(m.chat.id),
        )
        return

    if txt.startswith("下发"):
        if not is_admin_or_operator(m.chat.id, m.from_user):
            return await m.reply(deny_text())

        body = txt[len("下发"):].strip()
        if not body:
            return await m.reply("格式：下发5000 / 下发1000R / 下发1000/7.8")

        has_conversion = ("R" in body) or ("r" in body) or ("/" in body) or ("*" in body)
        expr = body.replace("R", "").replace("r", "")
        parsed = parse_amount_expr(expr, m.chat.id, default_direct_unit=not has_conversion)
        if not parsed:
            return await m.reply("❌ 下发格式错误")

        target = None
        if m.reply_to_message and m.reply_to_message.from_user:
            target = m.reply_to_message.from_user.full_name

        add_transaction(
            chat_id=m.chat.id,
            user_id=m.from_user.id,
            username=m.from_user.username or "",
            display_name=m.from_user.full_name or "",
            target_name=target,
            kind="payout",
            raw_amount=parsed["raw_amount"],
            unit_amount=parsed["unit_amount"],
            rate_used=parsed["rate_used"],
            fee_used=parsed["fee_used"],
            note="下发",
            original_text=txt,
        )

        start_ts, end_ts = day_range()
        await send_long_text(
            m.chat.id,
            report_text(m.chat.id, start_ts, end_ts, title="今日账单"),
            reply_markup=report_kb(m.chat.id),
        )
        return

    target_name, body = split_target_prefix(txt)

    if not body or body[0] not in ("+", "-"):
        return

    if not is_admin_or_operator(m.chat.id, m.from_user):
        return await m.reply(deny_text())

    note = ""
    if " " in body:
        first_part, note = body.split(" ", 1)
        amount_expr = first_part.strip()
        note = note.strip()
    else:
        amount_expr = body.strip()

    parsed = parse_amount_expr(amount_expr, m.chat.id, default_direct_unit=False)
    if not parsed:
        return await m.reply("❌ 记账格式错误")

    kind = "income" if amount_expr.startswith("+") else "payout"

    if not target_name:
        if m.reply_to_message and m.reply_to_message.from_user:
            target_name = m.reply_to_message.from_user.full_name
        else:
            target_name = ""

    add_transaction(
        chat_id=m.chat.id,
        user_id=m.from_user.id,
        username=m.from_user.username or "",
        display_name=m.from_user.full_name or "",
        target_name=target_name,
        kind=kind,
        raw_amount=parsed["raw_amount"],
        unit_amount=parsed["unit_amount"],
        rate_used=parsed["rate_used"],
        fee_used=parsed["fee_used"],
        note=note,
        original_text=txt,
    )

    start_ts, end_ts = day_range()
    await send_long_text(
        m.chat.id,
        report_text(m.chat.id, start_ts, end_ts, title="今日账单"),
        reply_markup=report_kb(m.chat.id),
    )

# ================= USER / BOT JOIN =================
@dp.message(lambda m: bool(m.new_chat_members))
async def new_members(m: types.Message):
    ensure_group(m)
    for u in m.new_chat_members or []:
        if u.is_bot:
            continue
        save_member(
            m.chat.id,
            u.id,
            u.username or "",
            u.full_name or "",
        )

    if not WELCOME_ENABLED:
        return

    try:
        names = ", ".join(u.full_name for u in m.new_chat_members if not u.is_bot)
        if names:
            await m.reply(WELCOME_TEXT.format(name=names))
    except Exception as e:
        print("new_members error:", e)

@dp.my_chat_member()
async def on_bot_member_update(e: types.ChatMemberUpdated):
    try:
        if e.new_chat_member.status in ("member", "administrator") and e.old_chat_member.status == "left":
            save_group(e.chat.id, e.chat.title or "Unnamed group")
            await bot.send_message(e.chat.id, "✅ 记账机器人已加入本群。")
    except Exception as ex:
        print("on_bot_member_update error:", ex)

# ================= WEBHOOK / HEALTH =================
@app.post("/webhook")
async def webhook(req: Request):
    if TELEGRAM_SECRET_TOKEN:
        secret = req.headers.get("x-telegram-bot-api-secret-token", "")
        if secret != TELEGRAM_SECRET_TOKEN:
            print("webhook secret mismatch")
            raise HTTPException(status_code=401, detail="Unauthorized")

    data = await req.json()
    update = types.Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}


# ================= WEB AUTH HELPERS =================
def get_web_admin_name():
    return WEB_ADMIN_NAME or "BOT 888"

def is_web_logged_in(request: Request):
    q_token = (request.query_params.get("token") or "").strip()
    if q_token and q_token == WEB_TOKEN:
        return True
    session = request.cookies.get("god_session", "")
    return session == WEB_TOKEN

def guard(request: Request):
    if not is_web_logged_in(request):
        next_path = request.url.path
        if request.url.query:
            next_path += "?" + request.url.query
        return RedirectResponse(url=f"/login?next={next_path}", status_code=302)
    return None

def simple_page(title: str, subtitle: str, body: str = ""):
    return f"""
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>
body {{
    margin: 0;
    font-family: Inter, Arial, sans-serif;
    background: linear-gradient(135deg, #060913 0%, #0a1020 45%, #070b17 100%);
    color: #eaf2ff;
    padding: 30px;
}}
.wrap {{
    max-width: 1380px;
    margin: 0 auto;
}}
.top {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 24px;
    flex-wrap: wrap;
    gap: 12px;
}}
.title {{
    font-size: 36px;
    font-weight: 900;
}}
.sub {{
    color: #8da2c0;
    margin-top: 8px;
}}
.back {{
    color: white;
    text-decoration: none;
    padding: 12px 16px;
    border-radius: 14px;
    background: rgba(255,255,255,.05);
    border: 1px solid rgba(255,255,255,.08);
}}
.quick-links {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 18px;
}}
.quick-links a {{
    text-decoration: none;
    color: white;
    padding: 10px 14px;
    border-radius: 12px;
    background: rgba(255,255,255,.05);
    border: 1px solid rgba(255,255,255,.08);
}}
.card {{
    background: rgba(17,25,40,.72);
    border: 1px solid rgba(255,255,255,.08);
    border-radius: 24px;
    padding: 24px;
    margin-bottom: 20px;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    margin-top: 18px;
}}
th, td {{
    padding: 14px 12px;
    border-bottom: 1px solid rgba(255,255,255,.08);
    text-align: left;
    vertical-align: top;
}}
th {{
    color: #8da2c0;
    font-size: 13px;
    text-transform: uppercase;
}}
.badge {{
    display: inline-block;
    padding: 6px 12px;
    border-radius: 999px;
    background: rgba(34,227,142,.12);
    border: 1px solid rgba(34,227,142,.2);
    color: #98f3c5;
    font-size: 12px;
    font-weight: 700;
}}
.badge.red {{
    background: rgba(255,93,115,.12);
    border-color: rgba(255,93,115,.20);
    color: #ff9baa;
}}
.badge.yellow {{
    background: rgba(255,204,51,.12);
    border-color: rgba(255,204,51,.20);
    color: #ffe38b;
}}
.mono {{
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    word-break: break-all;
}}
.grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 14px;
}}
.stat {{
    background: rgba(255,255,255,.03);
    border: 1px solid rgba(255,255,255,.06);
    border-radius: 18px;
    padding: 16px;
}}
.stat-label {{
    color: #8da2c0;
    font-size: 12px;
    margin-bottom: 8px;
}}
.stat-value {{
    font-size: 28px;
    font-weight: 800;
}}
pre {{
    white-space: pre-wrap;
    word-break: break-word;
    color: #eaf2ff;
}}
@media (max-width: 980px) {{
    .grid {{
        grid-template-columns: 1fr 1fr;
    }}
}}
@media (max-width: 640px) {{
    .grid {{
        grid-template-columns: 1fr;
    }}
}}
</style>
</head>
<body>
<div class="wrap">
    <div class="top">
        <div>
            <div class="title">{escape(title)}</div>
            <div class="sub">{escape(subtitle)}</div>
        </div>
        <a class="back" href="/dashboard">← Về Dashboard</a>
    </div>

    <div class="quick-links">
        <a href="/dashboard">🏠 Dashboard</a>
        <a href="/groups">👥 Groups</a>
        <a href="/transactions">💸 Transactions</a>
        <a href="/users">👑 Users</a>
        <a href="/orders">📦 Orders</a>
        <a href="/admins">🛡 Admins</a>
        <a href="/wallet-checks">🔎 Wallet Logs</a>
        <a href="/wallet-summary">📊 Wallet Summary</a>
        <a href="/background">⚙️ Background</a>
    </div>

    {body}
</div>
</body>
</html>
"""

# ================= BACKGROUND TASK STATUS =================
def _task_status_label(task: asyncio.Task) -> tuple[str, str, str]:
    """返回 (status, badge_class, detail)。"""
    if not task.done():
        return "running", "", ""
    if task.cancelled():
        return "cancelled", "yellow", "任务已取消"
    exc = task.exception()
    if exc is not None:
        return "failed", "red", str(exc)
    return "stopped", "yellow", "任务已结束"


def background_tasks_snapshot() -> dict:
    tasks = []
    for task in list(BACKGROUND_TASKS):
        name = task.get_name() or "unnamed"
        status, badge, detail = _task_status_label(task)
        tasks.append({
            "name": name,
            "description": TASK_DESCRIPTIONS.get(name, "—"),
            "status": status,
            "badge": badge,
            "detail": detail,
        })

    snap = {
        "time": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "env": ENV_MODE,
        "use_polling": USE_POLLING,
        "bot_username": BOT_USERNAME or "—",
        "bot_mode": "polling" if USE_POLLING else ("webhook" if BOT_BASE_URL.startswith("https://") else "none"),
        "auto_pay_interval": AUTO_PAY_INTERVAL,
        "payment_address_set": bool(get_payment_address()),
        "http_session_open": bool(HTTP_SESSION and not HTTP_SESSION.closed),
        "tasks": tasks,
    }
    try:
        set_setting(-1, BACKGROUND_STATUS_KEY, json.dumps(snap, ensure_ascii=False))
    except Exception as e:
        print("persist background snapshot error:", repr(e))
    return snap


async def background_status_persist_loop():
    while True:
        try:
            background_tasks_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print("background_status_persist_loop error:", repr(e))
        await asyncio.sleep(15)


# ================= DASHBOARD DATA =================
def dashboard_stats():
    try:
        stats = {
            "vip_users": 0,
            "groups": 0,
            "today_tx": 0,
            "today_amount": 0.0,
            "pending_orders": 0,
            "all_orders": 0,
            "wallet_checks": 0,
            "wallet_users": 0,
        }

        with get_db() as (_conn, cur):
            try:
                cur.execute("SELECT COUNT(*) FROM access_users")
                stats["vip_users"] = int(cur.fetchone()[0] or 0)
            except Exception as e:
                print("dashboard vip_users error:", e)

            try:
                cur.execute("SELECT COUNT(*) FROM groups")
                stats["groups"] = int(cur.fetchone()[0] or 0)
            except Exception as e:
                print("dashboard groups error:", e)

            try:
                start_ts, end_ts = day_range()
                cur.execute(
                    '''
                    SELECT COUNT(*), COALESCE(SUM(unit_amount), 0)
                    FROM transactions
                    WHERE created_at >= %s
                      AND created_at <= %s
                      AND COALESCE(undone, FALSE) = FALSE
                    ''',
                    (start_ts, end_ts)
                )
                row = cur.fetchone() or (0, 0)
                stats["today_tx"] = int(row[0] or 0)
                stats["today_amount"] = float(row[1] or 0)
            except Exception as e:
                print("dashboard today error:", e)

            try:
                cur.execute("SELECT COUNT(*) FROM rental_orders WHERE status = 'pending'")
                stats["pending_orders"] = int(cur.fetchone()[0] or 0)
            except Exception as e:
                print("dashboard pending_orders error:", e)

            try:
                cur.execute("SELECT COUNT(*) FROM rental_orders")
                stats["all_orders"] = int(cur.fetchone()[0] or 0)
            except Exception as e:
                print("dashboard all_orders error:", e)

            try:
                cur.execute("SELECT COUNT(*) FROM wallet_checks")
                stats["wallet_checks"] = int(cur.fetchone()[0] or 0)
            except Exception as e:
                print("dashboard wallet_checks error:", e)

            try:
                cur.execute("SELECT COUNT(DISTINCT user_id) FROM wallet_checks")
                stats["wallet_users"] = int(cur.fetchone()[0] or 0)
            except Exception as e:
                print("dashboard wallet_users error:", e)

        return stats

    except Exception as e:
        print("dashboard_stats error:", e)
        return {
            "vip_users": 0,
            "groups": 0,
            "today_tx": 0,
            "today_amount": 0,
            "pending_orders": 0,
            "all_orders": 0,
            "wallet_checks": 0,
            "wallet_users": 0,
        }

def dashboard_chart():
    try:
        labels = []
        values = []

        with get_db() as (_conn, cur):
            for i in range(6, -1, -1):
                d = datetime.now(BEIJING_TZ) - timedelta(days=i)
                start = d.replace(hour=0, minute=0, second=0, microsecond=0)
                end = start + timedelta(days=1)

                cur.execute(
                    '''
                    SELECT COALESCE(SUM(unit_amount), 0)
                    FROM transactions
                    WHERE created_at >= %s
                      AND created_at < %s
                      AND COALESCE(undone, FALSE) = FALSE
                    ''',
                    (int(start.timestamp()), int(end.timestamp()))
                )

                amount = cur.fetchone()[0] or 0
                labels.append(d.strftime("%m-%d"))
                values.append(float(amount))

        return labels, values

    except Exception as e:
        print("dashboard_chart error:", e)
        return [], []

# ================= PREMIUM LOGIN =================
def premium_login_html(error_msg=""):
    error_block = f'<div class="error-box">{escape(error_msg)}</div>' if error_msg else ""

    return f"""
<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GOD Login</title>
<style>
:root {{
    --bg: #070b17;
    --panel: rgba(17, 25, 40, 0.78);
    --line: rgba(255,255,255,0.08);
    --text: #eaf2ff;
    --muted: #8da2c0;
}}
* {{
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}}
body {{
    min-height: 100vh;
    font-family: Inter, Arial, sans-serif;
    color: var(--text);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    background:
        radial-gradient(circle at top left, rgba(58,184,255,.18), transparent 28%),
        radial-gradient(circle at top right, rgba(139,92,246,.16), transparent 24%),
        radial-gradient(circle at bottom center, rgba(34,227,142,.10), transparent 28%),
        linear-gradient(135deg, #060913 0%, #0a1020 45%, #070b17 100%);
}}
.wrap {{
    width: 100%;
    max-width: 1120px;
    display: grid;
    grid-template-columns: 1.15fr 0.85fr;
    gap: 26px;
}}
.hero {{
    padding: 42px;
    border-radius: 30px;
    background: rgba(12, 19, 34, 0.78);
    backdrop-filter: blur(18px);
    border: 1px solid var(--line);
    box-shadow: 0 25px 70px rgba(0,0,0,.42);
}}
.hero-badge {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 9px 14px;
    border-radius: 999px;
    background: rgba(58,184,255,.12);
    border: 1px solid rgba(58,184,255,.25);
    color: #9addff;
    font-size: 13px;
    margin-bottom: 22px;
}}
.hero-title {{
    font-size: clamp(34px, 6vw, 62px);
    font-weight: 900;
    line-height: 1.02;
    letter-spacing: -.03em;
    margin-bottom: 16px;
    background: linear-gradient(90deg, #8fe8ff 0%, #3ab8ff 30%, #a78bfa 65%, #22e38e 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}}
.hero-sub {{
    color: #8da2c0;
    font-size: 15px;
    line-height: 1.7;
    max-width: 560px;
    margin-bottom: 28px;
}}
.feature {{
    margin-bottom: 14px;
    background: rgba(255,255,255,.03);
    border: 1px solid rgba(255,255,255,.06);
    border-radius: 18px;
    padding: 16px;
}}
.feature-title {{
    font-size: 15px;
    font-weight: 700;
    margin-bottom: 5px;
}}
.feature-text {{
    font-size: 13px;
    color: #8da2c0;
    line-height: 1.6;
}}
.login-card {{
    padding: 34px;
    border-radius: 30px;
    background: rgba(14, 22, 38, 0.86);
    backdrop-filter: blur(18px);
    border: 1px solid var(--line);
    box-shadow: 0 25px 70px rgba(0,0,0,.45);
    display: flex;
    flex-direction: column;
    justify-content: center;
}}
.login-title {{
    font-size: 34px;
    font-weight: 900;
    margin-bottom: 8px;
}}
.login-sub {{
    color: #8da2c0;
    font-size: 14px;
    line-height: 1.6;
    margin-bottom: 24px;
}}
.label {{
    display: block;
    font-size: 13px;
    color: #9bb0cc;
    margin-bottom: 10px;
    text-transform: uppercase;
    letter-spacing: .10em;
}}
.input {{
    width: 100%;
    padding: 16px 18px;
    border-radius: 18px;
    border: 1px solid rgba(255,255,255,.08);
    background: rgba(8, 13, 25, 0.88);
    color: white;
    font-size: 15px;
    outline: none;
    margin-bottom: 18px;
}}
.btn {{
    width: 100%;
    border: none;
    padding: 16px 18px;
    border-radius: 18px;
    background: linear-gradient(90deg, #38bdf8 0%, #3b82f6 38%, #22c55e 100%);
    color: white;
    font-size: 16px;
    font-weight: 800;
    cursor: pointer;
}}
.error-box {{
    margin-bottom: 16px;
    padding: 14px 16px;
    border-radius: 16px;
    background: rgba(255,93,115,.10);
    border: 1px solid rgba(255,93,115,.20);
    color: #ff9baa;
    font-size: 14px;
}}
.note {{
    margin-top: 16px;
    color: #6f87a8;
    font-size: 13px;
    line-height: 1.6;
    text-align: center;
}}
.footer-badge {{
    margin-top: 18px;
    display: flex;
    justify-content: center;
    gap: 10px;
    flex-wrap: wrap;
}}
.footer-pill {{
    padding: 8px 12px;
    border-radius: 999px;
    font-size: 12px;
    color: #b9cae2;
    background: rgba(255,255,255,.04);
    border: 1px solid rgba(255,255,255,.06);
}}
@media (max-width: 980px) {{
    .wrap {{
        grid-template-columns: 1fr;
    }}
}}
</style>
</head>
<body>
    <div class="wrap">
        <div class="hero">
            <div class="hero-badge">⚡ PREMIUM CONTROL ACCESS</div>
            <div class="hero-title">GOD BOT<br>LOGIN PANEL</div>
            <div class="hero-sub">
                Đăng nhập để truy cập hệ thống dashboard premium, theo dõi bot Telegram,
                giao dịch trong ngày, trạng thái đơn hàng và toàn bộ thông tin vận hành.
            </div>

            <div class="feature">
                <div class="feature-title">📈 Real-time Dashboard</div>
                <div class="feature-text">Xem thống kê bot, volume 7 ngày, user VIP, nhóm và đơn hàng.</div>
            </div>
            <div class="feature">
                <div class="feature-title">🛡️ Secure Access</div>
                <div class="feature-text">Chỉ admin có mật khẩu mới vào được khu vực quản trị.</div>
            </div>
            <div class="feature">
                <div class="feature-title">🚀 Premium Interface</div>
                <div class="feature-text">Thiết kế dark glass đồng bộ hoàn toàn với GOD BOT Dashboard.</div>
            </div>
        </div>

        <div class="login-card">
            <div class="login-title">🔐 Đăng nhập</div>
            <div class="login-sub">Nhập mật khẩu quản trị để tiếp tục vào dashboard.</div>

            {error_block}

            <form method="post" action="/login">
                <label class="label">Admin Password</label>
                <input class="input" type="password" name="password" placeholder="Nhập mật khẩu web..." required>
                <button class="btn" type="submit">VÀO DASHBOARD</button>
            </form>

            <div class="note">
                Mật khẩu đăng nhập là giá trị <b>WEB_TOKEN</b> trong file <b>.env</b>.
            </div>

            <div class="footer-badge">
                <div class="footer-pill">Cloudflare SSL</div>
                <div class="footer-pill">FastAPI</div>
                <div class="footer-pill">Telegram Webhook</div>
            </div>
        </div>
    </div>
</body>
</html>
"""

# ================= ROOT / LOGIN =================
@app.get("/", response_class=HTMLResponse)
def home():
    return RedirectResponse(url="/login", status_code=302)

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_web_logged_in(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    return premium_login_html()

@app.post("/login")
async def login_submit(password: str = Form(...)):
    if password != WEB_TOKEN:
        return HTMLResponse(premium_login_html("❌ Sai mật khẩu đăng nhập"), status_code=401)

    resp = RedirectResponse(url="/dashboard", status_code=303)
    resp.set_cookie(
        key="god_session",
        value=WEB_TOKEN,
        httponly=True,
        samesite="lax",
        secure=IS_PRODUCTION,
        max_age=7 * 24 * 3600,
    )
    return resp

@app.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("god_session")
    return resp

# ================= DASHBOARD =================
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    auth = guard(request)
    if auth:
        return auth

    stats = dashboard_stats()
    labels, values = dashboard_chart()

    safe_bot_username = escape(BOT_USERNAME or "-")
    safe_webhook = escape(f"{BOT_BASE_URL}/webhook" if BOT_BASE_URL else "Not configured")
    now_text = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    admin_name = escape(get_web_admin_name())

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GOD BOT Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root {{
    --text: #eaf2ff;
    --muted: #8da2c0;
    --blue: #3ab8ff;
    --green: #22e38e;
    --yellow: #ffcc33;
    --red: #ff5d73;
    --purple: #b38cff;
    --shadow: 0 20px 50px rgba(0,0,0,.35);
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: Inter, Arial, sans-serif;
    color: var(--text);
    background:
        radial-gradient(circle at top left, rgba(58,184,255,.18), transparent 30%),
        radial-gradient(circle at top right, rgba(139,92,246,.14), transparent 25%),
        radial-gradient(circle at bottom center, rgba(34,227,142,.12), transparent 30%),
        linear-gradient(135deg, #060913 0%, #0a1020 45%, #070b17 100%);
    padding: 28px;
}}
.container {{ max-width: 1480px; margin: 0 auto; }}
.hero {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 20px;
    margin-bottom: 24px;
    flex-wrap: wrap;
}}
.badge {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    width: fit-content;
    padding: 8px 14px;
    border-radius: 999px;
    background: rgba(58,184,255,.12);
    border: 1px solid rgba(58,184,255,.25);
    color: #8ed9ff;
    font-size: 13px;
    margin-bottom: 10px;
}}
.title {{
    font-size: clamp(32px, 5vw, 64px);
    font-weight: 900;
    letter-spacing: -.03em;
    line-height: 1;
    background: linear-gradient(90deg, #8fe8ff 0%, #3ab8ff 30%, #a78bfa 65%, #22e38e 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}}
.subtitle {{
    color: var(--muted);
    font-size: 15px;
    margin-top: 10px;
}}
.hero-right {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    justify-content: flex-end;
    align-items: flex-start;
}}
.pill {{
    padding: 12px 18px;
    border-radius: 999px;
    background: rgba(17, 25, 40, 0.78);
    border: 1px solid rgba(255,255,255,.08);
    box-shadow: var(--shadow);
    color: var(--text);
    font-size: 14px;
}}
.pill.online {{
    color: #9ff3c8;
}}
.pill a {{
    color: white;
    text-decoration: none;
}}
.welcome-box {{
    width: 100%;
    max-width: 420px;
    background: rgba(17, 25, 40, 0.78);
    border: 1px solid rgba(255,255,255,.08);
    box-shadow: var(--shadow);
    border-radius: 24px;
    padding: 18px 20px;
}}
.welcome-line-1 {{
    font-size: 20px;
    font-weight: 800;
    color: #7ee7ff;
    margin-bottom: 6px;
}}
.welcome-line-2 {{
    font-size: 15px;
    color: #b4c6df;
    margin-bottom: 8px;
}}
.welcome-line-3 {{
    font-size: 14px;
    color: #8ef0b9;
}}
.quick-nav {{
    display: flex;
    gap: 12px;
    flex-wrap: wrap;
    margin: 18px 0 26px;
}}
.quick-btn {{
    text-decoration: none;
    color: white;
    padding: 12px 18px;
    border-radius: 14px;
    background: rgba(255,255,255,.05);
    border: 1px solid rgba(255,255,255,.08);
    transition: .2s ease;
    font-weight: 700;
}}
.quick-btn:hover {{
    transform: translateY(-2px);
    background: rgba(58,184,255,.12);
    border-color: rgba(58,184,255,.3);
}}
.grid {{
    display: grid;
    grid-template-columns: repeat(12, 1fr);
    gap: 18px;
}}
.card {{
    background: rgba(17, 25, 40, 0.72);
    border: 1px solid rgba(255,255,255,.08);
    border-radius: 24px;
    box-shadow: var(--shadow);
}}
.card-link {{
    text-decoration: none;
    color: inherit;
    display: block;
}}
.stat {{
    grid-column: span 3;
    padding: 22px;
    min-height: 150px;
    cursor: pointer;
    transition: .2s ease;
}}
.stat:hover {{
    transform: translateY(-4px);
    border-color: rgba(58,184,255,.28);
}}
.stat-top {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 22px;
}}
.icon {{
    width: 48px;
    height: 48px;
    border-radius: 16px;
    display: grid;
    place-items: center;
    font-size: 22px;
    background: rgba(255,255,255,.06);
    border: 1px solid rgba(255,255,255,.08);
}}
.stat-label {{
    color: var(--muted);
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: .12em;
}}
.stat-value {{
    font-size: 42px;
    font-weight: 800;
    line-height: 1;
    margin-bottom: 12px;
}}
.stat-sub {{
    color: var(--muted);
    font-size: 13px;
}}
.chart-card {{
    grid-column: span 8;
    padding: 24px;
    min-height: 420px;
}}
.side-card {{
    grid-column: span 4;
    padding: 24px;
}}
.section-title {{
    font-size: 20px;
    font-weight: 700;
    margin-bottom: 8px;
}}
.section-sub {{
    color: var(--muted);
    font-size: 14px;
    margin-bottom: 20px;
}}
.kv {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 18px;
    padding: 16px 0;
    border-bottom: 1px solid rgba(255,255,255,.06);
}}
.kv:last-child {{ border-bottom: none; }}
.kv-key {{
    color: var(--muted);
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: .08em;
}}
.kv-val {{
    text-align: right;
    font-size: 14px;
    color: var(--text);
    word-break: break-all;
    max-width: 70%;
}}
.footer {{
    margin-top: 22px;
    text-align: center;
    color: var(--muted);
    font-size: 13px;
}}
@media (max-width: 1180px) {{
    .stat {{ grid-column: span 6; }}
    .chart-card {{ grid-column: span 12; }}
    .side-card {{ grid-column: span 12; }}
}}
@media (max-width: 720px) {{
    .stat {{ grid-column: span 12; }}
}}
</style>
</head>
<body>
<div class="container">
    <div class="hero">
        <div>
            <div class="badge">⚡ PREMIUM CONTROL PANEL</div>
            <div class="title">GOD BOT DASHBOARD</div>
            <div class="subtitle">Real-time Telegram bot analytics • dark premium interface • admin control panel</div>
        </div>

        <div class="hero-right">
            <div class="pill">🕒 {now_text}</div>
            <div class="pill online">● ONLINE</div>
            <div class="pill"><a href="/logout">🚪 LOGOUT</a></div>

            <div class="welcome-box">
                <div class="welcome-line-1">Welcome Admin</div>
                <div class="welcome-line-2">Xin chào Owner</div>
                <div class="welcome-line-3">Logged in as {admin_name}</div>
            </div>
        </div>
    </div>

    <div class="quick-nav">
        <a class="quick-btn" href="/dashboard">🏠 Dashboard</a>
        <a class="quick-btn" href="/groups">👥 Groups</a>
        <a class="quick-btn" href="/transactions">💸 Transactions</a>
        <a class="quick-btn" href="/bots">🤖 Bots</a>
        <a class="quick-btn" href="/admins">🛡 Admins</a>
        <a class="quick-btn" href="/orders">📦 Orders</a>
        <a class="quick-btn" href="/users">👑 Users</a>
        <a class="quick-btn" href="/wallet-checks">🔎 Wallet Logs</a>
        <a class="quick-btn" href="/wallet-summary">📊 Wallet Summary</a>
        <a class="quick-btn" href="/background">⚙️ Background</a>
    </div>

    <div class="grid">
        <a class="card card-link" href="/users">
            <div class="stat">
                <div class="stat-top"><div><div class="stat-label">VIP USERS</div></div><div class="icon">👑</div></div>
                <div class="stat-value" style="color:#22e38e;">{stats["vip_users"]}</div>
                <div class="stat-sub">Premium access accounts</div>
            </div>
        </a>

        <a class="card card-link" href="/groups">
            <div class="stat">
                <div class="stat-top"><div><div class="stat-label">GROUPS</div></div><div class="icon">👥</div></div>
                <div class="stat-value" style="color:#3ab8ff;">{stats["groups"]}</div>
                <div class="stat-sub">Connected Telegram groups</div>
            </div>
        </a>

        <a class="card card-link" href="/transactions">
            <div class="stat">
                <div class="stat-top"><div><div class="stat-label">TODAY TX</div></div><div class="icon">📊</div></div>
                <div class="stat-value" style="color:#ffcc33;">{stats["today_tx"]}</div>
                <div class="stat-sub">Transactions recorded today</div>
            </div>
        </a>

        <a class="card card-link" href="/transactions">
            <div class="stat">
                <div class="stat-top"><div><div class="stat-label">TODAY U</div></div><div class="icon">💸</div></div>
                <div class="stat-value" style="color:#22e38e;">{float(stats["today_amount"]):.2f}</div>
                <div class="stat-sub">Total volume today</div>
            </div>
        </a>

        <a class="card card-link" href="/orders">
            <div class="stat">
                <div class="stat-top"><div><div class="stat-label">PENDING ORDERS</div></div><div class="icon">⏳</div></div>
                <div class="stat-value" style="color:#ff5d73;">{stats["pending_orders"]}</div>
                <div class="stat-sub">Orders waiting for approval</div>
            </div>
        </a>

        <a class="card card-link" href="/orders">
            <div class="stat">
                <div class="stat-top"><div><div class="stat-label">ALL ORDERS</div></div><div class="icon">📦</div></div>
                <div class="stat-value" style="color:#b38cff;">{stats["all_orders"]}</div>
                <div class="stat-sub">Rental / renew history</div>
            </div>
        </a>

        <a class="card card-link" href="/wallet-checks">
            <div class="stat">
                <div class="stat-top"><div><div class="stat-label">WALLET CHECKS</div></div><div class="icon">🔎</div></div>
                <div class="stat-value" style="color:#3ab8ff;">{stats["wallet_checks"]}</div>
                <div class="stat-sub">Wallet query logs</div>
            </div>
        </a>

        <a class="card card-link" href="/wallet-summary">
            <div class="stat">
                <div class="stat-top"><div><div class="stat-label">WALLET USERS</div></div><div class="icon">👤</div></div>
                <div class="stat-value" style="color:#22e38e;">{stats["wallet_users"]}</div>
                <div class="stat-sub">Users sent wallet addresses</div>
            </div>
        </a>

        <div class="card chart-card">
            <div class="section-title">📈 7 Day Volume</div>
            <div class="section-sub">Transaction volume trend for the last 7 days</div>
            <canvas id="myChart" height="120"></canvas>
        </div>

        <div class="card side-card">
            <div class="section-title">🛰 System Overview</div>
            <div class="section-sub">Core runtime information and public endpoints</div>

            <div class="kv"><div class="kv-key">Bot Username</div><div class="kv-val">@{safe_bot_username}</div></div>
            <div class="kv"><div class="kv-key">Webhook</div><div class="kv-val">{safe_webhook}</div></div>
            <div class="kv"><div class="kv-key">Payment Wallet</div><div class="kv-val">{escape(PAYMENT_ADDRESS)}</div></div>
            <div class="kv"><div class="kv-key">Logged In As</div><div class="kv-val">{admin_name}</div></div>
        </div>
    </div>

    <div class="footer">GOD MODE • Auto refresh every 20 seconds</div>
</div>

<script>
const labels = {json.dumps(labels, ensure_ascii=False)};
const values = {json.dumps(values)};

const ctx = document.getElementById('myChart').getContext('2d');
const gradient = ctx.createLinearGradient(0, 0, 0, 320);
gradient.addColorStop(0, 'rgba(58,184,255,0.38)');
gradient.addColorStop(1, 'rgba(58,184,255,0.02)');

new Chart(ctx, {{
    type: 'line',
    data: {{
        labels: labels,
        datasets: [{{
            label: '7 Day Volume',
            data: values,
            borderColor: '#43c6ff',
            backgroundColor: gradient,
            fill: true,
            borderWidth: 3,
            tension: 0.38
        }}]
    }},
    options: {{
        responsive: true,
        maintainAspectRatio: false,
        plugins: {{
            legend: {{
                labels: {{
                    color: '#d8e6ff'
                }}
            }}
        }},
        scales: {{
            x: {{
                ticks: {{ color: '#9fb3d1' }},
                grid: {{ color: 'rgba(255,255,255,0.05)' }}
            }},
            y: {{
                ticks: {{ color: '#9fb3d1' }},
                grid: {{ color: 'rgba(255,255,255,0.05)' }}
            }}
        }}
    }}
}});
setTimeout(() => location.reload(), 20000);
</script>
</body>
</html>
"""

# ================= WEB PAGES =================
@app.get("/bots", response_class=HTMLResponse)
async def bots_page(request: Request):
    auth = guard(request)
    if auth:
        return auth

    body = f"""
    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Bot</th>
                    <th>Username</th>
                    <th>Status</th>
                    <th>Webhook</th>
                </tr>
            </thead>
            <tbody>
                <tr>
                    <td>God Bot</td>
                    <td>@{escape(BOT_USERNAME or "-")}</td>
                    <td><span class="badge">ONLINE</span></td>
                    <td class="mono">{escape(f"{BOT_BASE_URL}/webhook" if BOT_BASE_URL else "Not configured")}</td>
                </tr>
            </tbody>
        </table>
    </div>
    """
    return HTMLResponse(simple_page("🤖 Bot Management", "Quản lý bot đang hoạt động", body))

@app.get("/admins", response_class=HTMLResponse)
async def admins_page(request: Request):
    auth = guard(request)
    if auth:
        return auth

    rows = get_all_admins()
    html_rows = []

    if BOT_OWNER_ID:
        html_rows.append(f"<tr><td>{BOT_OWNER_ID}</td><td>owner</td><td><span class='badge'>ACTIVE</span></td></tr>")
    if SUPER_ADMIN_ID and SUPER_ADMIN_ID != BOT_OWNER_ID:
        html_rows.append(f"<tr><td>{SUPER_ADMIN_ID}</td><td>super(env)</td><td><span class='badge'>ACTIVE</span></td></tr>")

    for uid, role in rows:
        if uid in (BOT_OWNER_ID, SUPER_ADMIN_ID):
            continue
        html_rows.append(
            f"<tr><td>{uid}</td><td>{escape(role)}</td><td><span class='badge'>ACTIVE</span></td></tr>"
        )

    body = f"""
    <div class="card">
        <table>
            <thead><tr><th>User ID</th><th>Role</th><th>Status</th></tr></thead>
            <tbody>{''.join(html_rows) if html_rows else '<tr><td colspan="3">No admins</td></tr>'}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(simple_page("🛡 Admin Management", "Quản lý admin web", body))

@app.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request):
    auth = guard(request)
    if auth:
        return auth

    rows = get_rental_orders_by_status(None, limit=100)
    trs = []
    for row in rows:
        order_code, user_id, username, full_name, category_title, plan_label, amount, st, created_at, paid_at, expires_at = row
        badge_cls = "badge"
        if st == "rejected":
            badge_cls = "badge red"
        elif st == "pending":
            badge_cls = "badge yellow"

        trs.append(
            f"<tr>"
            f"<td>{escape(order_code)}</td>"
            f"<td>{user_id}</td>"
            f"<td>@{escape(username or '-')}</td>"
            f"<td>{escape(category_title or '-')}</td>"
            f"<td>{escape(plan_label or '-')}</td>"
            f"<td>{fmt_num(amount)}U</td>"
            f"<td><span class='{badge_cls}'>{escape(st)}</span></td>"
            f"<td>{fmt_ts(created_at)}</td>"
            f"</tr>"
        )

    body = f"""
    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Order</th>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>Category</th>
                    <th>Plan</th>
                    <th>Amount</th>
                    <th>Status</th>
                    <th>Created</th>
                </tr>
            </thead>
            <tbody>{''.join(trs) if trs else '<tr><td colspan="8">No orders</td></tr>'}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(simple_page("📦 Orders", "Quản lý đơn hàng / gia hạn", body))

@app.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, page: int = 1, keyword: str = "", status: str = ""):
    auth = guard(request)
    if auth:
        return auth

    limit = 20
    offset = (max(page, 1) - 1) * limit
    rows = get_access_users_page(limit=limit, offset=offset, keyword=keyword or None, status=status or None)
    total = count_access_users_filtered(keyword=keyword or None, status=status or None)
    total_pages = max(1, (total + limit - 1) // limit)

    trs = []
    for user_id, username, granted_by, granted_at, expires_at in rows:
        role = "VIP"
        exp = "Permanent" if expires_at is None else fmt_ts(expires_at)
        trs.append(
            f"<tr>"
            f"<td>{user_id}</td>"
            f"<td>@{escape(username or '-')}</td>"
            f"<td>{granted_by or '-'}</td>"
            f"<td>{fmt_ts(granted_at)}</td>"
            f"<td>{exp}</td>"
            f"<td><span class='badge'>{role}</span></td>"
            f"</tr>"
        )

    body = f"""
    <div class="card">
        <div class="grid">
            <div class="stat"><div class="stat-label">Page</div><div class="stat-value">{page}</div></div>
            <div class="stat"><div class="stat-label">Total Pages</div><div class="stat-value">{total_pages}</div></div>
            <div class="stat"><div class="stat-label">Total Users</div><div class="stat-value">{total}</div></div>
            <div class="stat"><div class="stat-label">Rows</div><div class="stat-value">{len(rows)}</div></div>
        </div>
    </div>

    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>Granted By</th>
                    <th>Granted At</th>
                    <th>Expire At</th>
                    <th>Role</th>
                </tr>
            </thead>
            <tbody>{''.join(trs) if trs else '<tr><td colspan="6">No users</td></tr>'}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(simple_page("👑 Users", "Quản lý user VIP", body))

@app.get("/transactions", response_class=HTMLResponse)
async def transactions_page(request: Request, date: str | None = None):
    auth = guard(request)
    if auth:
        return auth

    try:
        if date:
            dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
            start_ts = int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            end_ts = int((dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) - timedelta(seconds=1)).timestamp())
        else:
            start_ts, end_ts = day_range()
    except Exception:
        start_ts, end_ts = day_range()

    trs = []
    with get_db() as (_conn, cur):
        cur.execute(
            '''
            SELECT id, chat_id, user_id, username, display_name, target_name, kind,
                   raw_amount, unit_amount, rate_used, fee_used, note, original_text,
                   created_at, undone
            FROM transactions
            WHERE created_at >= %s
              AND created_at <= %s
              AND COALESCE(undone, FALSE) = FALSE
            ORDER BY created_at DESC, id DESC
            LIMIT 200
            ''',
            (start_ts, end_ts)
        )
        rows = cur.fetchall()

    for tx in rows:
        tx_id, chat_id, user_id, username, display_name, target_name, kind, raw_amount, unit_amount, rate_used, fee_used, note, original_text, created_at, undone = tx
        trs.append(
            f"<tr>"
            f"<td>{fmt_ts(created_at)}</td>"
            f"<td>{chat_id}</td>"
            f"<td>{escape(kind or '-')}</td>"
            f"<td>{fmt_num(raw_amount)}</td>"
            f"<td>{fmt_num(unit_amount)}U</td>"
            f"<td>@{escape(username or '-')}</td>"
            f"<td>{escape(target_name or '-')}</td>"
            f"<td>{escape(note or '-')}</td>"
            f"</tr>"
        )

    body = f"""
    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Chat ID</th>
                    <th>Type</th>
                    <th>Raw</th>
                    <th>U</th>
                    <th>Username</th>
                    <th>Target</th>
                    <th>Note</th>
                </tr>
            </thead>
            <tbody>{''.join(trs) if trs else '<tr><td colspan="8">No transactions</td></tr>'}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(simple_page("💸 Transaction History", "Lịch sử giao dịch toàn hệ thống", body))

@app.get("/groups", response_class=HTMLResponse)
async def groups_page(request: Request):
    auth = guard(request)
    if auth:
        return auth

    rows = get_groups()
    trs = []
    for row in rows:
        chat_id, title = row[0], row[1] if len(row) > 1 else ""
        trs.append(
            f"<tr>"
            f"<td>{escape(title or '-')}</td>"
            f"<td>{chat_id}</td>"
            f"<td><span class='badge'>ACTIVE</span></td>"
            f"<td><a class='back' href='/group/{chat_id}'>History</a></td>"
            f"</tr>"
        )

    body = f"""
    <div class="card">
        <table>
            <thead><tr><th>Group</th><th>Chat ID</th><th>Status</th><th>Action</th></tr></thead>
            <tbody>{''.join(trs) if trs else '<tr><td colspan="4">No groups</td></tr>'}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(simple_page("👥 Group Management", "Quản lý nhóm Telegram", body))

@app.get("/group/{chat_id}", response_class=HTMLResponse)
async def group_history_page(chat_id: int, request: Request, date: str | None = None):
    auth = guard(request)
    if auth:
        return auth

    try:
        if date:
            dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
            start_ts = int(dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            end_ts = int((dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) - timedelta(seconds=1)).timestamp())
        else:
            start_ts, end_ts = day_range()
    except Exception:
        start_ts, end_ts = day_range()

    txs = get_transactions(chat_id, start_ts=start_ts, end_ts=end_ts)
    stats = summarize_transactions(txs)

    trs = []
    for tx in txs:
        tx_id, chat_id, user_id, username, display_name, target_name, kind, raw_amount, unit_amount, rate_used, fee_used, note, original_text, created_at, undone = tx
        trs.append(
            f"<tr>"
            f"<td>{fmt_ts(created_at)}</td>"
            f"<td>{escape(kind or '-')}</td>"
            f"<td>{fmt_num(raw_amount)}</td>"
            f"<td>{fmt_num(unit_amount)}U</td>"
            f"<td>{fmt_num(rate_used)}</td>"
            f"<td>{fmt_num(fee_used)}%</td>"
            f"<td>{escape(display_name or '-')}</td>"
            f"<td>{escape(target_name or '-')}</td>"
            f"<td>@{escape(username or '-')}</td>"
            f"<td>{escape(note or '-')}</td>"
            f"</tr>"
        )

    body = f"""
    <div class="card">
        <div class="grid">
            <div class="stat"><div class="stat-label">总记录数</div><div class="stat-value">{len(txs)}</div></div>
            <div class="stat"><div class="stat-label">正常入款</div><div class="stat-value">{fmt_num(stats['total_income_unit'])}U</div></div>
            <div class="stat"><div class="stat-label">已下发</div><div class="stat-value">{fmt_num(stats['paid'])}U</div></div>
            <div class="stat"><div class="stat-label">待下发</div><div class="stat-value">{fmt_num(stats['pending'])}U</div></div>
        </div>
    </div>

    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Type</th>
                    <th>Raw</th>
                    <th>U</th>
                    <th>Rate</th>
                    <th>Fee</th>
                    <th>By</th>
                    <th>Target</th>
                    <th>Username</th>
                    <th>Note</th>
                </tr>
            </thead>
            <tbody>{''.join(trs) if trs else '<tr><td colspan="10">No transactions</td></tr>'}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(simple_page(f"📘 Group {chat_id}", "Lịch sử giao dịch nhóm", body))

@app.get("/wallet-checks", response_class=HTMLResponse)
async def wallet_checks_page(request: Request, page: int = 1):
    auth = guard(request)
    if auth:
        return auth

    limit = 30
    offset = (max(page, 1) - 1) * limit
    rows = get_wallet_checks_page(limit=limit, offset=offset)
    total = count_wallet_checks()
    total_pages = max(1, (total + limit - 1) // limit)

    trs = []
    for row in rows:
        _id, chat_id, user_id, username, full_name, address, trx_balance, usdt_balance, tx_count, created_at = row
        sender = full_name or username or str(user_id)
        trs.append(
            f"<tr>"
            f"<td>{fmt_ts(created_at)}</td>"
            f"<td>{chat_id}</td>"
            f"<td>{user_id}</td>"
            f"<td>{escape(sender)}</td>"
            f"<td>@{escape(username or '-')}</td>"
            f"<td class='mono'>{escape(address or '-')}</td>"
            f"<td>{fmt_num(trx_balance)}</td>"
            f"<td>{fmt_num(usdt_balance)}</td>"
            f"<td>{tx_count if tx_count is not None else 'N/A'}</td>"
            f"</tr>"
        )

    body = f"""
    <div class="card">
        <div class="grid">
            <div class="stat"><div class="stat-label">Total Wallet Checks</div><div class="stat-value">{total}</div></div>
            <div class="stat"><div class="stat-label">Current Page Rows</div><div class="stat-value">{len(rows)}</div></div>
            <div class="stat"><div class="stat-label">Page</div><div class="stat-value">{page}</div></div>
            <div class="stat"><div class="stat-label">Total Pages</div><div class="stat-value">{total_pages}</div></div>
        </div>
    </div>

    <div class="card">
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Chat ID</th>
                    <th>User ID</th>
                    <th>Sender</th>
                    <th>Username</th>
                    <th>Address</th>
                    <th>TRX</th>
                    <th>USDT</th>
                    <th>TX Count</th>
                </tr>
            </thead>
            <tbody>{''.join(trs) if trs else '<tr><td colspan="9">No wallet logs</td></tr>'}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(simple_page("🔎 Wallet Check Logs", "Tất cả log user gửi ví", body))

@app.get("/wallet-summary", response_class=HTMLResponse)
async def wallet_summary_page(request: Request):
    auth = guard(request)
    if auth:
        return auth

    total_checks = 0
    distinct_users = 0
    distinct_groups = 0
    user_rows = []
    group_rows = []

    try:
        with get_db() as (_conn, cur):
            cur.execute("SELECT COUNT(*) FROM wallet_checks")
            total_checks = int(cur.fetchone()[0] or 0)

            cur.execute("SELECT COUNT(DISTINCT user_id) FROM wallet_checks")
            distinct_users = int(cur.fetchone()[0] or 0)

            cur.execute("SELECT COUNT(DISTINCT chat_id) FROM wallet_checks")
            distinct_groups = int(cur.fetchone()[0] or 0)

            cur.execute(
                '''
                SELECT
                    user_id,
                    COALESCE(NULLIF(full_name, ''), NULLIF(username, ''), CAST(user_id AS TEXT)) AS sender,
                    username,
                    COUNT(*) AS total_times,
                    MAX(created_at) AS last_time
                FROM wallet_checks
                GROUP BY user_id, sender, username
                ORDER BY total_times DESC, last_time DESC
                LIMIT 50
                '''
            )
            user_rows = cur.fetchall()

            cur.execute(
                '''
                SELECT
                    chat_id,
                    COUNT(*) AS total_times,
                    COUNT(DISTINCT user_id) AS distinct_users_count,
                    MAX(created_at) AS last_time
                FROM wallet_checks
                GROUP BY chat_id
                ORDER BY total_times DESC, last_time DESC
                LIMIT 50
                '''
            )
            group_rows = cur.fetchall()

    except Exception as e:
        print("wallet_summary_page error:", e)

    user_trs = []
    for user_id, sender, username, total_times, last_time in user_rows:
        user_trs.append(
            f"<tr>"
            f"<td>{user_id}</td>"
            f"<td>{escape(sender or '-')}</td>"
            f"<td>@{escape(username or '-')}</td>"
            f"<td><span class='badge'>{total_times} 次</span></td>"
            f"<td>{fmt_ts(last_time)}</td>"
            f"</tr>"
        )

    group_trs = []
    for chat_id, total_times, d_users, last_time in group_rows:
        group_trs.append(
            f"<tr>"
            f"<td>{chat_id}</td>"
            f"<td><span class='badge'>{total_times} 次</span></td>"
            f"<td>{d_users}</td>"
            f"<td>{fmt_ts(last_time)}</td>"
            f"</tr>"
        )

    body = f"""
    <div class="card">
        <div class="grid">
            <div class="stat"><div class="stat-label">Total Wallet Checks</div><div class="stat-value">{total_checks}</div></div>
            <div class="stat"><div class="stat-label">Distinct Users</div><div class="stat-value">{distinct_users}</div></div>
            <div class="stat"><div class="stat-label">Distinct Groups</div><div class="stat-value">{distinct_groups}</div></div>
            <div class="stat"><div class="stat-label">Status</div><div class="stat-value">LIVE</div></div>
        </div>
    </div>

    <div class="card">
        <div style="font-size:22px;font-weight:800;margin-bottom:10px;">👤 User gửi ví bao nhiêu lần</div>
        <table>
            <thead><tr><th>User ID</th><th>Sender</th><th>Username</th><th>Total Times</th><th>Last Time</th></tr></thead>
            <tbody>{''.join(user_trs) if user_trs else '<tr><td colspan="5">No user summary</td></tr>'}</tbody>
        </table>
    </div>

    <div class="card">
        <div style="font-size:22px;font-weight:800;margin-bottom:10px;">👥 Thống kê theo nhóm</div>
        <table>
            <thead><tr><th>Chat ID</th><th>Total Checks</th><th>Distinct Users</th><th>Last Time</th></tr></thead>
            <tbody>{''.join(group_trs) if group_trs else '<tr><td colspan="4">No group summary</td></tr>'}</tbody>
        </table>
    </div>
    """
    return HTMLResponse(simple_page("📊 Wallet Summary", "Thống kê user gửi ví và theo nhóm", body))

# ================= BACKGROUND TASKS PAGE =================
@app.get("/api/background")
async def api_background(request: Request):
    denied = guard(request)
    if denied:
        return denied
    return JSONResponse(background_tasks_snapshot())


@app.get("/background", response_class=HTMLResponse)
async def background_page(request: Request):
    denied = guard(request)
    if denied:
        return denied

    snap = background_tasks_snapshot()
    task_rows = []
    for t in snap["tasks"]:
        badge_cls = t["badge"]
        badge_html = f'<span class="badge {badge_cls}">{escape(t["status"].upper())}</span>' if badge_cls else f'<span class="badge">{escape(t["status"].upper())}</span>'
        detail = f'<div class="mono" style="color:#8da2c0;margin-top:6px;">{escape(t["detail"])}</div>' if t["detail"] else ""
        task_rows.append(
            f"<tr>"
            f"<td class='mono'>{escape(t['name'])}</td>"
            f"<td>{escape(t['description'])}</td>"
            f"<td>{badge_html}{detail}</td>"
            f"</tr>"
        )

    bot_mode = snap["bot_mode"]
    mode_badge = "badge" if bot_mode == "polling" else ("badge yellow" if bot_mode == "none" else "badge")
    pay_badge = "badge" if snap["payment_address_set"] else "badge red"
    http_badge = "badge" if snap["http_session_open"] else "badge red"

    body = f"""
    <div class="card">
        <div style="font-size:22px;font-weight:800;margin-bottom:10px;">⚙️ 后台任务状态</div>
        <div class="sub" style="margin-bottom:14px;">每 10 秒自动刷新 · 最后更新：<span id="bg-time">{escape(snap['time'])}</span></div>
        <div class="grid">
            <div class="stat"><div class="stat-label">环境</div><div class="stat-value" style="font-size:18px;">{escape(snap['env'])}</div></div>
            <div class="stat"><div class="stat-label">Bot 模式</div><div class="stat-value" style="font-size:18px;"><span class="{mode_badge}">{escape(bot_mode)}</span></div></div>
            <div class="stat"><div class="stat-label">Bot 用户名</div><div class="stat-value" style="font-size:18px;">@{escape(snap['bot_username'] or '—')}</div></div>
            <div class="stat"><div class="stat-label">自动收款间隔</div><div class="stat-value" style="font-size:18px;">{snap['auto_pay_interval']}s</div></div>
            <div class="stat"><div class="stat-label">收款地址</div><div class="stat-value" style="font-size:18px;"><span class="{pay_badge}">{'已配置' if snap['payment_address_set'] else '未配置'}</span></div></div>
            <div class="stat"><div class="stat-label">HTTP 会话</div><div class="stat-value" style="font-size:18px;"><span class="{http_badge}">{'正常' if snap['http_session_open'] else '已关闭'}</span></div></div>
        </div>
    </div>

    <div class="card">
        <div style="font-size:22px;font-weight:800;margin-bottom:10px;">📋 任务列表</div>
        <table>
            <thead><tr><th>任务名</th><th>说明</th><th>状态</th></tr></thead>
            <tbody id="bg-task-body">
                {''.join(task_rows) if task_rows else '<tr><td colspan="3">暂无后台任务</td></tr>'}
            </tbody>
        </table>
    </div>

    <script>
    async function refreshBackground() {{
        try {{
            const r = await fetch('/api/background');
            if (!r.ok) return;
            const d = await r.json();
            document.getElementById('bg-time').textContent = d.time || '';
            const tbody = document.getElementById('bg-task-body');
            if (!tbody || !d.tasks) return;
            tbody.innerHTML = d.tasks.map(t => {{
                const cls = t.badge ? 'badge ' + t.badge : 'badge';
                const detail = t.detail ? `<div class="mono" style="color:#8da2c0;margin-top:6px;">${{t.detail}}</div>` : '';
                return `<tr><td class="mono">${{t.name}}</td><td>${{t.description}}</td><td><span class="${{cls}}">${{(t.status||'').toUpperCase()}}</span>${{detail}}</td></tr>`;
            }}).join('') || '<tr><td colspan="3">暂无后台任务</td></tr>';
        }} catch (e) {{ console.warn('background refresh failed', e); }}
    }}
    setInterval(refreshBackground, 10000);
    </script>
    """
    return HTMLResponse(simple_page("⚙️ 后台任务", "查看 Bot 后台循环任务运行状态", body))


# ================= HEALTH =================
@app.get("/healthz")
async def healthz():
    return {
        "ok": True,
        "bot_username": BOT_USERNAME,
        "time": datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    }

# ================= RUN =================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
