import json
import os
import time
from html import escape
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

from fastapi import FastAPI, Query, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from dotenv import load_dotenv
import uvicorn
import requests

from db import (
    init_db,
    get_setting,
    get_groups,
    get_transactions,
    get_dashboard_stats,
    get_access_users_page,
    count_access_users_filtered,
    get_access_user_by_id,
    extend_access_user,
    set_access_user_permanent,
    remove_access_user,
    get_rental_orders_by_status,
    mark_rental_order_rejected,
    get_rental_order,
    approve_rental_order,
    discover_all_chat_ids,
    get_groups_sync_overview,
    get_group_users_merged,
    get_chat_logs,
    count_chat_logs,
    save_group,
    save_member,
    add_admin,
    remove_admin,
    get_all_admins_extended,
    get_admin_record,
    set_admin_permissions,
    ADMIN_PERMISSION_DEFS,
    DEFAULT_ADMIN_PERMISSIONS,
    access_user_source_label,
    get_dm_peers_page,
    count_dm_peers,
    get_dm_logs,
    count_dm_logs,
)
from view_token import verify_group_view_token

BACKGROUND_STATUS_KEY = "background_tasks_status"



# ================= ENV =================
load_dotenv()

WEB_TOKEN = (os.getenv("WEB_TOKEN") or os.getenv("WEB_ADMIN_TOKEN") or "").strip()
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
BOT_OWNER_ID = int(os.getenv("BOT_OWNER_ID", "0") or 0)
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID", "0") or 0)
PORT = int(os.getenv("WEB_PORT", os.getenv("PORT", "8081")))

if not WEB_TOKEN:
    raise RuntimeError(
        "WEB_TOKEN is missing in environment variables. "
        "Set a strong password in .env for web dashboard access."
    )

BEIJING_TZ = ZoneInfo("Asia/Shanghai")


# ================= APP =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Ledger Web", version="1.0.0", lifespan=lifespan)


# ================= AUTH =================
def is_admin_token(token: str | None) -> bool:
    """全站管理后台密码（.env WEB_TOKEN）。"""
    return bool(token) and token == WEB_TOKEN


def check_token(token: str | None) -> bool:
    return is_admin_token(token)


def group_page_access(chat_id: int, token: str | None) -> str:
    """admin=完整后台；view=仅该群账单页；空=未授权。"""
    if is_admin_token(token):
        return "admin"
    if verify_group_view_token(chat_id, token):
        return "view"
    return ""


def require_token(token: str | None):
    if not is_admin_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")


def require_token_or_login(token: str | None, next_path: str = "/dashboard"):
    """HTML 页面：仅 WEB_TOKEN 可进管理后台。"""
    if is_admin_token(token):
        return None
    return render_login_page(next_path=next_path)


def require_group_bill_page(chat_id: int, token: str | None):
    """群账单页：接受 WEB_TOKEN 或该群只读 view token。"""
    access = group_page_access(chat_id, token)
    if access:
        return None, access
    return render_login_page(next_path=f"/group/{chat_id}"), ""


def render_login_page(error: str | None = None, next_path: str = "/dashboard"):
    err = (
        f'<div class="card" style="border-color:#dc2626;margin-bottom:16px;color:#fecaca;">{escape(error)}</div>'
        if error
        else ""
    )
    body = f"""
    <div class="topbar">
        <div>
            <h1>🔐 Web 管理后台登录</h1>
            <div class="muted">端口 {PORT} · 密码为 .env 中的 <code>WEB_TOKEN</code></div>
        </div>
    </div>
    {err}
    <div class="card" style="max-width:480px;">
        <form method="post" action="/login">
            <input type="hidden" name="next" value="{escape(next_path)}">
            <label class="muted" style="display:block;margin-bottom:8px;">管理密码</label>
            <input type="password" name="password" required
                placeholder="输入 WEB_TOKEN"
                style="width:100%;background:#0b1220;color:#e5e7eb;border:1px solid #374151;padding:12px;border-radius:10px;margin-bottom:14px;">
            <button class="btn" type="submit">进入后台</button>
        </form>
        <p class="muted" style="margin-top:14px;">
            也可直接访问：<br>
            <code>http://127.0.0.1:{PORT}/dashboard?token=你的WEB_TOKEN</code>
        </p>
    </div>
    """
    return page_shell("登录", body)


# ================= HELPERS =================
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


def summarize_transactions(txs):
    income = [t for t in txs if t[6] == "income" and not t[14]]
    payout = [t for t in txs if t[6] == "payout" and not t[14]]
    reserve = [t for t in txs if t[6] == "reserve" and not t[14]]

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
        "undone_count": len([t for t in txs if t[14]]),
    }


def parse_web_date(date_str: str | None):
    """
    date_str: YYYY-MM-DD
    Theo Asia/Shanghai
    """
    if not date_str:
        dt = datetime.now(BEIJING_TZ)
    else:
        try:
            parsed = datetime.strptime(date_str, "%Y-%m-%d")
            dt = parsed.replace(tzinfo=BEIJING_TZ)
        except Exception:
            dt = datetime.now(BEIJING_TZ)

    start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1) - timedelta(seconds=1)

    return {
        "date_str": start.strftime("%Y-%m-%d"),
        "start_ts": int(start.timestamp()),
        "end_ts": int(end.timestamp()),
        "start_dt": start,
        "end_dt": end,
    }


def get_group_title_map():
    return {int(row[0]): row[1] for row in get_groups()}


def build_url(path: str, token: str | None = None, **params):
    q = {}
    for k, v in params.items():
        if v is not None:
            q[k] = v
    if token:
        q["token"] = token

    if q:
        return f"{path}?{urlencode(q)}"
    return path


def home_panel_btn(token: str | None = None) -> str:
    return f'<a class="btn secondary" href="{build_url("/dashboard", token=token)}">🏠 返回主面板</a>'


def kind_label(kind: str):
    return {
        "income": "入款",
        "payout": "下发",
        "reserve": "寄存",
    }.get(kind, kind or "-")


def tx_row_class(kind: str, undone: bool):
    if undone:
        return "undone"
    if kind == "income":
        return "income"
    if kind == "payout":
        return "payout"
    if kind == "reserve":
        return "reserve"
    return ""
    
def access_status(expires_at):
    now_ts = int(time.time())
    if expires_at is None:
        return "permanent", "永久"
    if int(expires_at) > now_ts:
        return "active", "有效"
    return "expired", "已过期"


def load_background_snapshot():
    raw = get_setting(-1, BACKGROUND_STATUS_KEY, "") or ""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def page_shell(title: str, body_html: str, token: str | None = None, show_home_nav: bool = False):
    home_nav = ""
    if show_home_nav:
        home_nav = f'<div class="home-nav">{home_panel_btn(token)}</div>'

    html = f"""
    <!doctype html>
    <html lang="zh-CN">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{escape(title)}</title>
        <style>
            :root {{
                --bg: #0f172a;
                --panel: #111827;
                --panel-2: #1f2937;
                --border: #374151;
                --text: #e5e7eb;
                --muted: #9ca3af;
                --blue: #2563eb;
                --blue-2: #60a5fa;
                --green: #16a34a;
                --red: #dc2626;
                --yellow: #d97706;
                --gray: #6b7280;
            }}

            * {{
                box-sizing: border-box;
            }}

            body {{
                margin: 0;
                padding: 20px;
                font-family: Arial, sans-serif;
                background: var(--bg);
                color: var(--text);
            }}

            .container {{
                max-width: 1600px;
                margin: auto;
                background: var(--panel);
                border-radius: 16px;
                padding: 20px;
                box-shadow: 0 10px 30px rgba(0,0,0,.35);
            }}

            h1, h2, h3 {{
                margin: 0 0 10px 0;
            }}

            .muted {{
                color: var(--muted);
                font-size: 14px;
                line-height: 1.6;
            }}

            .home-nav {{
                display: flex;
                justify-content: flex-end;
                margin-bottom: 14px;
                padding-bottom: 14px;
                border-bottom: 1px solid var(--border);
            }}

            .topbar {{
                display: flex;
                justify-content: space-between;
                gap: 12px;
                align-items: center;
                flex-wrap: wrap;
                margin-bottom: 16px;
            }}

            .btn {{
                display: inline-block;
                padding: 10px 14px;
                border-radius: 10px;
                background: var(--blue);
                color: white;
                text-decoration: none;
                border: 0;
                cursor: pointer;
                font-size: 14px;
            }}

            .btn:hover {{
                opacity: .92;
                text-decoration: none;
            }}

            .btn.secondary {{
                background: var(--panel-2);
                border: 1px solid var(--border);
            }}

            .tag {{
                display: inline-block;
                padding: 4px 8px;
                border-radius: 999px;
                background: var(--panel-2);
                color: white;
                font-size: 12px;
                border: 1px solid var(--border);
            }}

            .tag.ok {{
                background: rgba(22,163,74,.15);
                border-color: rgba(22,163,74,.4);
                color: #86efac;
            }}

            .tag.warn {{
                background: rgba(217,119,6,.15);
                border-color: rgba(217,119,6,.4);
                color: #fdba74;
            }}

            .tag.bad {{
                background: rgba(220,38,38,.15);
                border-color: rgba(220,38,38,.4);
                color: #fca5a5;
            }}

            .stats {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 12px;
                margin: 18px 0;
            }}

            .card {{
                background: var(--panel-2);
                border-radius: 12px;
                padding: 14px;
                border: 1px solid var(--border);
            }}

            .card .label {{
                color: var(--muted);
                font-size: 13px;
            }}

            .card .value {{
                font-size: 22px;
                font-weight: 700;
                margin-top: 6px;
                word-break: break-word;
            }}

            .filters {{
                display: flex;
                gap: 10px;
                flex-wrap: wrap;
                align-items: center;
                margin: 14px 0 6px;
            }}

            input[type="date"] {{
                background: #0b1220;
                color: var(--text);
                border: 1px solid var(--border);
                padding: 10px 12px;
                border-radius: 10px;
            }}

            table {{
                width: 100%;
                border-collapse: collapse;
                margin-top: 14px;
                overflow: hidden;
                border-radius: 12px;
            }}

            th, td {{
                border-bottom: 1px solid var(--border);
                padding: 10px 8px;
                font-size: 14px;
                text-align: left;
                vertical-align: top;
                white-space: nowrap;
            }}

            th {{
                background: var(--panel-2);
                color: #f9fafb;
                position: sticky;
                top: 0;
                z-index: 1;
            }}

            tr:hover td {{
                background: rgba(255,255,255,0.03);
            }}

            .table-wrap {{
                overflow-x: auto;
                margin-top: 12px;
            }}

            .row-income td:first-child {{
                border-left: 4px solid #16a34a;
            }}

            .row-payout td:first-child {{
                border-left: 4px solid #dc2626;
            }}

            .row-reserve td:first-child {{
                border-left: 4px solid #d97706;
            }}

            .row-undone td {{
                opacity: .62;
                text-decoration: line-through;
            }}

            a {{
                color: var(--blue-2);
                text-decoration: none;
            }}

            a:hover {{
                text-decoration: underline;
            }}

            code {{
                background: rgba(255,255,255,.06);
                padding: 2px 6px;
                border-radius: 6px;
            }}

            .empty {{
                text-align: center;
                color: var(--muted);
                padding: 20px 0;
            }}

            @media (max-width: 768px) {{
                body {{
                    padding: 10px;
                }}

                .container {{
                    padding: 14px;
                    border-radius: 12px;
                }}

                .stats {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            {home_nav}
            {body_html}
        </div>
    </body>
    </html>
    """
    return HTMLResponse(html)


# ================= RENDER PAGES =================
def render_groups_page(token: str | None = None):
    groups = get_groups()
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    rows = ""
    for row in groups:
        chat_id = row[0]
        title = row[1] if len(row) > 1 else ""
        link = build_url(f"/group/{chat_id}", token=token, date=today)
        members_link = build_url(f"/group/{chat_id}/members", token=token)
        rec_link = build_url(f"/group/{chat_id}/records", token=token)
        user_count = len(get_group_users_merged(chat_id))
        rows += f"""
        <tr>
            <td>{escape(title or 'Unnamed')}</td>
            <td><span class="tag">{chat_id}</span></td>
            <td>{user_count}</td>
            <td>
                <a class="btn" href="{link}">账单</a>
                <a class="btn secondary" href="{members_link}">群成员</a>
                <a class="btn secondary" href="{rec_link}">聊天</a>
            </td>
        </tr>
        """

    if not rows:
        rows = """
        <tr>
            <td colspan="4" class="empty">暂无群组记录</td>
        </tr>
        """

    token_note = (
        '<span class="tag ok">已启用 Token 保护</span>'
        if WEB_TOKEN else
        '<span class="tag warn">未设置 Token，当前网页为公开访问</span>'
    )

    body = f"""
    <div class="topbar">
        <div>
            <h1>📋 群组列表</h1>
            <div class="muted">
                选择一个群组，查看对应日期的交易历史。<br>
                当前时间：{escape(datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"))}（北京时间）
            </div>
        </div>
        <div>{token_note}</div>
    </div>

    <table>
        <thead>
            <tr>
                <th>群组名称</th>
                <th>Chat ID</th>
                <th>已记录用户</th>
                <th>操作</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>
    """
    sync_link = build_url("/groups/sync", token=token)
    body = body.replace(
        "</div>\n    </div>\n\n    <table>",
        f'</div>\n        <div>\n            <a class="btn" href="{sync_link}">🔄 同步群与用户</a>\n        </div>\n    </div>\n\n    <table>',
        1,
    )
    return page_shell("群组列表", body, token=token, show_home_nav=True)


def tg_api(method: str, **params):
    if not BOT_TOKEN:
        return None, "未配置 BOT_TOKEN，无法调用 Telegram"
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            params=params,
            timeout=15,
        )
        data = resp.json()
        if not data.get("ok"):
            return None, data.get("description") or str(data)
        return data.get("result"), None
    except Exception as e:
        return None, str(e)


def sync_groups_from_telegram():
    """从 Telegram 刷新已知群组的名称与人数，并写入 groups 表。"""
    chat_ids = discover_all_chat_ids()
    ok, fail, errors = 0, 0, []
    now_ts = int(time.time())

    for chat_id in chat_ids:
        if int(chat_id) > 0:
            fail += 1
            errors.append(f"{chat_id}: 跳过私聊 ID")
            continue

        title = f"Group {chat_id}"
        member_count = 0

        chat, err = tg_api("getChat", chat_id=chat_id)
        if err:
            fail += 1
            errors.append(f"{chat_id}: {err}")
            continue

        title = chat.get("title") or chat.get("username") or title
        cnt, err2 = tg_api("getChatMemberCount", chat_id=chat_id)
        if not err2 and cnt is not None:
            member_count = int(cnt)

        save_group(chat_id, title, member_count=member_count, synced_at=now_ts)
        ok += 1

    return {
        "total": len(chat_ids),
        "ok": ok,
        "fail": fail,
        "errors": errors[:20],
    }


def sync_group_members_from_telegram(chat_id: int):
    """从 Telegram 拉取群管理员并写入 members 表（Bot 须为群管理员）。"""
    chat_id = int(chat_id)
    saved = 0
    errors = []

    admins, err = tg_api("getChatAdministrators", chat_id=chat_id)
    if err:
        return {"ok": 0, "saved": 0, "errors": [err]}

    for item in admins or []:
        user = item.get("user") or {}
        if user.get("is_bot"):
            continue
        uid = user.get("id")
        if not uid:
            continue
        first = user.get("first_name") or ""
        last = user.get("last_name") or ""
        name = (first + " " + last).strip() or str(uid)
        try:
            save_member(chat_id, uid, user.get("username") or "", name)
            saved += 1
        except Exception as e:
            errors.append(str(e))

    return {"ok": saved, "saved": saved, "errors": errors[:5]}


def render_group_members_page(chat_id: int, token: str | None = None, message: str | None = None):
    groups = get_group_title_map()
    title = groups.get(int(chat_id), f"Group {chat_id}")
    users = get_group_users_merged(chat_id)

    user_rows = ""
    for u in users:
        uname = (u.get("username") or "").strip()
        uname_cell = (
            f'<a href="https://t.me/{escape(uname)}" target="_blank">@{escape(uname)}</a>'
            if uname else '<span class="muted">（无用户名）</span>'
        )
        user_rows += f"""
        <tr>
            <td><code>{u['user_id']}</code></td>
            <td>{uname_cell}</td>
            <td>{escape(u.get('name') or '-')}</td>
            <td>{escape(fmt_ts(u['last_seen']) if u.get('last_seen') else '-')}</td>
            <td><span class="tag">{escape(', '.join(sorted(u.get('sources') or [])))}</span></td>
        </tr>
        """

    if not user_rows:
        user_rows = '<tr><td colspan="5" class="empty">暂无成员记录。请在群内发言、记账，或点「同步管理员」。</td></tr>'

    sync_run = build_url(f"/group/{chat_id}/members/sync", token=token)
    groups_link = build_url("/groups", token=token)
    bill_link = build_url(f"/group/{chat_id}", token=token)
    records_link = build_url(f"/group/{chat_id}/records", token=token)
    msg_block = f'<div class="card" style="margin-bottom:16px;"><b>{escape(message)}</b></div>' if message else ""

    with_username = sum(1 for u in users if (u.get("username") or "").strip())

    body = f"""
    <div class="topbar">
        <div>
            <h1>👥 {escape(title)} · 群成员</h1>
            <div class="muted">
                Chat ID: <code>{chat_id}</code> · 共 {len(users)} 人 · 有用户名 {with_username} 人<br>
                数据来自：群消息、记账、入群记录；「同步管理员」可拉取 Telegram 管理员列表。
            </div>
        </div>
        <div>
            <a class="btn" href="{sync_run}">🔄 同步管理员</a>
            <a class="btn secondary" href="{groups_link}">← 群组列表</a>
            <a class="btn secondary" href="{bill_link}">账单</a>
            <a class="btn secondary" href="{records_link}">聊天记录</a>
        </div>
    </div>
    {msg_block}
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>User ID</th>
                    <th>用户名 (@)</th>
                    <th>显示名</th>
                    <th>最近活跃</th>
                    <th>来源</th>
                </tr>
            </thead>
            <tbody>{user_rows}</tbody>
        </table>
    </div>
    """
    return page_shell(f"{title} - 群成员", body, token=token, show_home_nav=True)


def render_groups_sync_page(token: str | None = None, message: str | None = None):
    rows_data = get_groups_sync_overview()
    rows_html = ""

    for row in rows_data:
        (
            chat_id, name, member_count, synced_at, updated_at,
            db_members, tx_count, chat_count, last_tx, last_chat,
        ) = row
        last_active = max(int(last_tx or 0), int(last_chat or 0), int(updated_at or 0))
        members_link = build_url(f"/group/{chat_id}/members", token=token)
        detail_link = build_url(f"/group/{chat_id}/records", token=token)
        bill_link = build_url(f"/group/{chat_id}", token=token)

        rows_html += f"""
        <tr>
            <td>{escape(name or '未命名')}</td>
            <td><code>{chat_id}</code></td>
            <td>{int(member_count or 0) or '-'}</td>
            <td>{int(db_members or 0)}</td>
            <td>{int(tx_count or 0)}</td>
            <td>{int(chat_count or 0)}</td>
            <td>{escape(fmt_ts(last_active) if last_active else '-')}</td>
            <td>{escape(fmt_ts(synced_at) if synced_at else '未同步')}</td>
            <td>
                <a class="btn" href="{members_link}">群成员</a>
                <a class="btn secondary" href="{detail_link}">聊天</a>
                <a class="btn secondary" href="{bill_link}">账单</a>
            </td>
        </tr>
        """

    if not rows_html:
        rows_html = '<tr><td colspan="9" class="empty">暂无群组。请先把 Bot 拉进群并有人发言/记账。</td></tr>'

    sync_run = build_url("/groups/sync/run", token=token)
    groups_link = build_url("/groups", token=token)
    msg_block = f'<div class="card" style="margin-bottom:16px;"><b>{escape(message)}</b></div>' if message else ""

    body = f"""
    <div class="topbar">
        <div>
            <h1>🔄 群组与用户同步</h1>
            <div class="muted">
                汇总 Bot 所在群（数据库记录 + Telegram 刷新群名/人数）。<br>
                聊天记录：群内文字消息（Bot 在线后新消息会自动入库）；记账记录见「账单」。
            </div>
        </div>
        <div>
            <a class="btn" href="{sync_run}">从 Telegram 同步</a>
            <a class="btn secondary" href="{groups_link}">群组列表</a>
        </div>
    </div>
    {msg_block}
    <div class="stats">
        <div class="card"><div class="label">群数量</div><div class="value">{len(rows_data)}</div></div>
        <div class="card"><div class="label">说明</div><div class="value" style="font-size:14px;">Bot API 无法列出未活跃过的群</div></div>
    </div>
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>群名称</th>
                    <th>Chat ID</th>
                    <th>TG人数</th>
                    <th>已记录成员</th>
                    <th>记账条数</th>
                    <th>聊天条数</th>
                    <th>最近活跃</th>
                    <th>上次同步</th>
                    <th>操作</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """
    return page_shell("群组同步", body, token=token, show_home_nav=True)


def render_group_records_page(chat_id: int, token: str | None = None):
    groups = {int(r[0]): r[1] for r in get_groups()}
    title = groups.get(int(chat_id), f"Group {chat_id}")

    users = get_group_users_merged(chat_id)
    user_rows = ""
    for u in users:
        src = ", ".join(sorted(u["sources"]))
        user_rows += f"""
        <tr>
            <td><code>{u['user_id']}</code></td>
            <td>@{escape(u['username'] or '-')}</td>
            <td>{escape(u['name'] or '-')}</td>
            <td>{escape(fmt_ts(u['last_seen']) if u['last_seen'] else '-')}</td>
            <td><span class="tag">{escape(src)}</span></td>
        </tr>
        """
    if not user_rows:
        user_rows = '<tr><td colspan="5" class="empty">暂无用户记录</td></tr>'

    logs = get_chat_logs(chat_id, limit=100)
    log_rows = ""
    for _id, uid, un, fn, text, ts in logs:
        log_rows += f"""
        <tr>
            <td>{escape(fmt_ts(ts))}</td>
            <td><code>{uid}</code></td>
            <td>@{escape(un or '-')}</td>
            <td>{escape(fn or '-')}</td>
            <td class="mono" style="max-width:420px;word-break:break-all;">{escape(text or '')}</td>
        </tr>
        """
    if not log_rows:
        log_rows = '<tr><td colspan="5" class="empty">暂无聊天文字记录（新群消息会自动采集）</td></tr>'

    total_logs = count_chat_logs(chat_id)
    sync_link = build_url("/groups/sync", token=token)
    bill_link = build_url(f"/group/{chat_id}", token=token)
    members_link = build_url(f"/group/{chat_id}/members", token=token)

    body = f"""
    <div class="topbar">
        <div>
            <h1>👥 {escape(title)}</h1>
            <div class="muted">Chat ID: <code>{chat_id}</code> · 用户 {len(users)} · 聊天记录 {total_logs} 条</div>
        </div>
        <div>
            <a class="btn" href="{members_link}">群成员用户名</a>
            <a class="btn secondary" href="{sync_link}">← 返回同步</a>
            <a class="btn secondary" href="{bill_link}">查看账单</a>
        </div>
    </div>

    <h2>用户 ID 列表（成员 + 记账 + 聊天 合并）</h2>
    <div class="table-wrap">
        <table>
            <thead><tr><th>User ID</th><th>Username</th><th>显示名</th><th>最近活跃</th><th>来源</th></tr></thead>
            <tbody>{user_rows}</tbody>
        </table>
    </div>

    <h2 style="margin-top:24px;">群内聊天记录（最近 100 条文字）</h2>
    <div class="table-wrap">
        <table>
            <thead><tr><th>时间</th><th>User ID</th><th>Username</th><th>姓名</th><th>内容</th></tr></thead>
            <tbody>{log_rows}</tbody>
        </table>
    </div>
    """
    return page_shell(f"{title} - 群记录", body, token=token, show_home_nav=True)


def render_group_history_page(
    chat_id: int,
    date_str: str | None = None,
    token: str | None = None,
    access: str = "admin",
):
    groups = get_group_title_map()
    group_title = groups.get(int(chat_id), f"Group {chat_id}")

    day = parse_web_date(date_str)

    txs = get_transactions(
        chat_id,
        start_ts=day["start_ts"],
        end_ts=day["end_ts"],
        include_undone=True,
    )
    stats = summarize_transactions(txs)

    income_txs = [t for t in txs if t[6] == "income" and not t[14]]
    payout_txs = [t for t in txs if t[6] == "payout" and not t[14]]
    reserve_txs = [t for t in txs if t[6] == "reserve" and not t[14]]

    prev_day = (day["start_dt"] - timedelta(days=1)).strftime("%Y-%m-%d")
    next_day = (day["start_dt"] + timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")

    token_input = f'<input type="hidden" name="token" value="{escape(token)}">' if token else ""

    rows_html = ""
    if txs:
        for tx in txs:
            (
                tx_id, c_id, user_id, username, display_name, target_name, kind,
                raw_amount, unit_amount, rate_used, fee_used, note, original_text,
                created_at, undone
            ) = tx

            tm = datetime.fromtimestamp(created_at, BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
            status = '<span class="tag ok">正常</span>' if not undone else '<span class="tag bad">已撤销</span>'

            row_class = tx_row_class(kind, undone)
            row_class = {
                "income": "row-income",
                "payout": "row-payout",
                "reserve": "row-reserve",
                "undone": "row-undone",
            }.get(row_class, "")

            rows_html += f"""
            <tr class="{row_class}">
                <td>{escape(tm)}</td>
                <td>{escape(kind_label(kind))}</td>
                <td>{escape(fmt_num(raw_amount) if raw_amount is not None else '-')}</td>
                <td>{escape(fmt_num(unit_amount))}U</td>
                <td>{escape(fmt_num(rate_used))}</td>
                <td>{escape(fmt_num(fee_used))}%</td>
                <td>{escape(display_name or '')}</td>
                <td>{escape(target_name or '')}</td>
                <td>{escape(username or '')}</td>
                <td>{escape(note or '')}</td>
                <td>{escape(original_text or '')}</td>
                <td>{status}</td>
            </tr>
            """
    else:
        rows_html = """
        <tr>
            <td colspan="12" class="empty">当天暂无交易记录</td>
        </tr>
        """

    today_link = build_url(f"/group/{chat_id}", token=token, date=today)
    prev_link = build_url(f"/group/{chat_id}", token=token, date=prev_day)
    next_link = build_url(f"/group/{chat_id}", token=token, date=next_day)

    if access == "admin":
        back_btn = f'<a class="btn secondary" href="{build_url("/groups", token=token)}">← 返回群组列表</a>'
        view_note = ""
    else:
        back_btn = ""
        view_note = (
            '<div class="card" style="margin-bottom:16px;border-color:#2563eb;">'
            "🔒 <b>只读查看</b>：此链接仅可浏览本群账单，无法进入管理后台。"
            "</div>"
        )

    body = f"""
    <div class="topbar">
        <div>
            <h1>📘 群组交易历史</h1>
            <div class="muted">
                群组：<b>{escape(group_title)}</b> |
                Chat ID：<span class="tag">{chat_id}</span> |
                日期：<span class="tag">{escape(day["date_str"])}</span>
            </div>
        </div>
        <div>{back_btn}</div>
    </div>
    {view_note}

    <form class="filters" method="get" action="/group/{chat_id}">
        {token_input}
        <label for="date">选择日期：</label>
        <input type="date" id="date" name="date" value="{escape(day["date_str"])}">
        <button class="btn" type="submit">查看</button>
        <a class="btn secondary" href="{today_link}">今天</a>
        <a class="btn secondary" href="{prev_link}">前一天</a>
        <a class="btn secondary" href="{next_link}">后一天</a>
    </form>

    <div class="stats">
        <div class="card">
            <div class="label">总记录数</div>
            <div class="value">{len(txs)}</div>
        </div>
        <div class="card">
            <div class="label">正常入款</div>
            <div class="value">{fmt_num(stats["total_income_unit"])}U</div>
        </div>
        <div class="card">
            <div class="label">正常下发</div>
            <div class="value">{fmt_num(stats["total_payout_unit"])}U</div>
        </div>
        <div class="card">
            <div class="label">正常寄存</div>
            <div class="value">{fmt_num(stats["total_reserve_unit"])}U</div>
        </div>
        <div class="card">
            <div class="label">待下发</div>
            <div class="value">{fmt_num(stats["pending"])}U</div>
        </div>
        <div class="card">
            <div class="label">入款笔数</div>
            <div class="value">{len(income_txs)}</div>
        </div>
        <div class="card">
            <div class="label">下发笔数</div>
            <div class="value">{len(payout_txs)}</div>
        </div>
        <div class="card">
            <div class="label">寄存笔数</div>
            <div class="value">{len(reserve_txs)}</div>
        </div>
        <div class="card">
            <div class="label">撤销记录</div>
            <div class="value">{stats["undone_count"]}</div>
        </div>
        <div class="card">
            <div class="label">原始入款总额</div>
            <div class="value">{fmt_num(stats["total_raw_income"])}</div>
        </div>
        <div class="card">
            <div class="label">应下发</div>
            <div class="value">{fmt_num(stats["due"])}U</div>
        </div>
        <div class="card">
            <div class="label">已下发</div>
            <div class="value">{fmt_num(stats["paid"])}U</div>
        </div>
    </div>

    <div class="muted">
        统计范围：<b>{escape(day["start_dt"].strftime("%Y-%m-%d %H:%M:%S"))}</b>
        至
        <b>{escape(day["end_dt"].strftime("%Y-%m-%d %H:%M:%S"))}</b>
        （北京时间）
    </div>

    <h2 style="margin-top:16px;">交易明细</h2>
    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>时间</th>
                    <th>类型</th>
                    <th>Raw</th>
                    <th>U</th>
                    <th>Rate</th>
                    <th>Fee</th>
                    <th>记录人</th>
                    <th>Target</th>
                    <th>Username</th>
                    <th>备注</th>
                    <th>原文</th>
                    <th>状态</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
    """
    return page_shell(
        f"{group_title} - 交易历史",
        body,
        token=token if access == "admin" else None,
        show_home_nav=(access == "admin"),
    )


def render_dm_chats_page(token: str | None = None, keyword: str | None = None):
    rows = get_dm_peers_page(limit=100, offset=0, keyword=keyword)
    total = count_dm_peers(keyword=keyword)

    table_rows = ""
    for peer_id, full_name, username, msg_count, last_at, in_count, out_count in rows:
        detail = build_url(f"/dm-chats/{peer_id}", token=token)
        table_rows += f"""
        <tr>
            <td><code>{peer_id}</code></td>
            <td>{escape(full_name or '-')}</td>
            <td>@{escape(username or '-')}</td>
            <td>{int(msg_count or 0)}</td>
            <td>{int(in_count or 0)} / {int(out_count or 0)}</td>
            <td>{escape(fmt_ts(last_at))}</td>
            <td><a class="btn" href="{detail}">查看对话</a></td>
        </tr>
        """
    if not table_rows:
        table_rows = (
            '<tr><td colspan="7" class="empty">暂无私聊记录。'
            '重启 Bot 后新私聊会自动采集。</td></tr>'
        )

    token_hidden = f'<input type="hidden" name="token" value="{escape(token)}">' if token else ""
    body = f"""
    <div class="topbar">
        <div>
            <h1>💬 私聊记录</h1>
            <div class="muted">Bot 与用户的私聊 · 共 {total} 人 · 入站/出站</div>
        </div>
    </div>

    <form class="filters" method="get" action="/dm-chats">
        {token_hidden}
        <input type="text" name="keyword" value="{escape(keyword or '')}"
            placeholder="搜索 User ID / 用户名 / 昵称"
            style="background:#0b1220;color:#e5e7eb;border:1px solid #374151;padding:10px 12px;border-radius:10px;">
        <button class="btn" type="submit">查询</button>
    </form>

    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>User ID</th>
                    <th>昵称</th>
                    <th>Username</th>
                    <th>消息数</th>
                    <th>用户/Bot</th>
                    <th>最近消息</th>
                    <th>操作</th>
                </tr>
            </thead>
            <tbody>{table_rows}</tbody>
        </table>
    </div>
    """
    return page_shell("私聊记录", body, token=token, show_home_nav=True)


def render_dm_chat_detail_page(peer_id: int, token: str | None = None):
    logs = get_dm_logs(peer_id, limit=500)
    total = count_dm_logs(peer_id)
    list_link = build_url("/dm-chats", token=token)

    title = f"User {peer_id}"
    if logs:
        for _id, uid, un, fn, direction, text, ts in logs:
            if direction == "in" and fn:
                title = fn
                break

    rows = ""
    for _id, uid, un, fn, direction, text, ts in logs:
        is_out = direction == "out"
        who = "🤖 Bot" if is_out else escape(fn or un or str(uid))
        row_style = "background:rgba(37,99,235,.12);" if is_out else ""
        rows += f"""
        <tr style="{row_style}">
            <td>{escape(fmt_ts(ts))}</td>
            <td>{who}</td>
            <td>{'出站' if is_out else '入站'}</td>
            <td class="mono" style="max-width:560px;white-space:pre-wrap;word-break:break-word;">{escape(text or '')}</td>
        </tr>
        """

    if not rows:
        rows = '<tr><td colspan="4" class="empty">暂无消息</td></tr>'

    body = f"""
    <div class="topbar">
        <div>
            <h1>💬 {escape(title)}</h1>
            <div class="muted">私聊 ID: <code>{peer_id}</code> · 共 {total} 条（显示最近 500 条）</div>
        </div>
        <div>
            <a class="btn secondary" href="{list_link}">← 私聊列表</a>
        </div>
    </div>
    <div class="table-wrap">
        <table>
            <thead>
                <tr><th>时间</th><th>发送方</th><th>方向</th><th>内容</th></tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
    </div>
    """
    return page_shell(f"私聊 {peer_id}", body, token=token, show_home_nav=True)


# ================= ROUTES =================
def render_dashboard_page(token: str | None = None):
    stats = get_dashboard_stats()

    users_link = build_url("/users", token=token)
    orders_link = build_url("/orders", token=token)
    groups_link = build_url("/groups", token=token)
    sync_link = build_url("/groups/sync", token=token)
    background_link = build_url("/background", token=token)
    dm_link = build_url("/dm-chats", token=token)

    body = f"""
    <div class="topbar">
        <div>
            <h1>📊 管理后台</h1>
            <div class="muted">用户、订单、群组统一管理</div>
        </div>
        <div>
            <a class="btn secondary" href="{users_link}">用户管理</a>
            <a class="btn secondary" href="{orders_link}">订单管理</a>
            <a class="btn secondary" href="{groups_link}">群组账单</a>
            <a class="btn secondary" href="{dm_link}">💬 私聊记录</a>
            <a class="btn secondary" href="{sync_link}">🔄 群同步</a>
            <a class="btn secondary" href="{background_link}">⚙️ 后台任务</a>
        </div>
    </div>

    <div class="stats">
        <div class="card">
            <div class="label">总用户</div>
            <div class="value">{stats["total_users"]}</div>
        </div>
        <div class="card">
            <div class="label">有效用户</div>
            <div class="value">{stats["active_users"]}</div>
        </div>
        <div class="card">
            <div class="label">永久用户</div>
            <div class="value">{stats["permanent_users"]}</div>
        </div>
        <div class="card">
            <div class="label">已过期</div>
            <div class="value">{stats["expired_users"]}</div>
        </div>
        <div class="card">
            <div class="label">待支付订单</div>
            <div class="value">{stats["pending_orders"]}</div>
        </div>
    </div>
    """
    return page_shell("管理后台", body)


def users_tab_bar(active_tab: str, token: str | None = None) -> str:
    vip_cls = "" if active_tab == "vip" else " secondary"
    admin_cls = "" if active_tab == "admins" else " secondary"
    vip_link = build_url("/users", token=token, tab="vip")
    admin_link = build_url("/users", token=token, tab="admins")
    return f"""
    <div class="filters" style="margin-bottom:18px;">
        <a class="btn{vip_cls}" href="{vip_link}">👤 VIP 用户</a>
        <a class="btn{admin_cls}" href="{admin_link}">🛡 管理员</a>
    </div>
    """


def _protected_admin_ids():
    ids = set()
    if BOT_OWNER_ID:
        ids.add(int(BOT_OWNER_ID))
    if SUPER_ADMIN_ID:
        ids.add(int(SUPER_ADMIN_ID))
    return ids


def _permission_checkboxes_html(prefix: str, selected: dict, disabled: bool = False) -> str:
    blocks = []
    dis = " disabled" if disabled else ""
    for key, (title, desc) in ADMIN_PERMISSION_DEFS.items():
        checked = " checked" if selected.get(key) else ""
        blocks.append(f"""
        <label style="display:flex;align-items:flex-start;gap:10px;margin:10px 0;cursor:{'default' if disabled else 'pointer'};">
            <input type="checkbox" name="{prefix}{key}" value="1"{checked}{dis}
                style="margin-top:4px;width:18px;height:18px;">
            <span>
                <b>{escape(title)}</b>
                <span class="muted" style="display:block;font-size:13px;">{escape(desc)}</span>
            </span>
        </label>
        """)
    return "".join(blocks)


def render_vip_users_page(
    token: str | None = None,
    keyword: str | None = None,
    status: str | None = None,
    message: str | None = None,
):
    rows = get_access_users_page(limit=100, offset=0, keyword=keyword, status=status)
    total = count_access_users_filtered(keyword=keyword, status=status)

    users_html = ""
    for user_id, username, granted_by, granted_at, expires_at in rows:
        st_key, st_label = access_status(expires_at)
        tag_class = "ok" if st_key in ("active", "permanent") else "bad"
        detail_link = build_url(f"/user/{user_id}", token=token)
        source = access_user_source_label(user_id)

        users_html += f"""
        <tr>
            <td><code>{user_id}</code></td>
            <td>@{escape(username or "-")}</td>
            <td><span class="tag">{escape(source)}</span></td>
            <td>{escape(fmt_ts(granted_at))}</td>
            <td>{escape(fmt_ts(expires_at) if expires_at else "永久")}</td>
            <td><span class="tag {tag_class}">{st_label}</span></td>
            <td><a class="btn" href="{detail_link}">查看 / 续期</a></td>
        </tr>
        """

    if not users_html:
        users_html = '<tr><td colspan="7" class="empty">暂无 VIP 用户（试用申请或购买后会出现）</td></tr>'

    msg_block = f'<div class="card" style="margin-bottom:16px;"><b>{escape(message)}</b></div>' if message else ""
    token_hidden = f'<input type="hidden" name="token" value="{escape(token)}">' if token else ""

    body = f"""
    <div class="topbar">
        <div>
            <h1>👤 用户管理</h1>
            <div class="muted">VIP 用户：自主申请试用 / 购买授权 · 共 {total} 人</div>
        </div>
    </div>
    {users_tab_bar("vip", token)}
    {msg_block}

    <form class="filters" method="get" action="/users">
        <input type="hidden" name="tab" value="vip">
        {token_hidden}
        <input type="text" name="keyword" value="{escape(keyword or '')}" placeholder="搜索 user_id / username"
            style="background:#0b1220;color:#e5e7eb;border:1px solid #374151;padding:10px 12px;border-radius:10px;">
        <select name="status" style="background:#0b1220;color:#e5e7eb;border:1px solid #374151;padding:10px 12px;border-radius:10px;">
            <option value="">全部状态</option>
            <option value="active" {"selected" if status == "active" else ""}>有效</option>
            <option value="expired" {"selected" if status == "expired" else ""}>已过期</option>
            <option value="permanent" {"selected" if status == "permanent" else ""}>永久</option>
        </select>
        <button class="btn" type="submit">查询</button>
    </form>

    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>来源</th>
                    <th>授权时间</th>
                    <th>到期时间</th>
                    <th>状态</th>
                    <th>操作</th>
                </tr>
            </thead>
            <tbody>{users_html}</tbody>
        </table>
    </div>
    """
    return page_shell("用户管理 - VIP", body, token=token, show_home_nav=True)


def render_admins_page(token: str | None = None, message: str | None = None):
    protected = _protected_admin_ids()
    rows_html = ""

    if BOT_OWNER_ID:
        rows_html += f"""
        <tr>
            <td><code>{BOT_OWNER_ID}</code></td>
            <td>最高权限拥有者</td>
            <td><span class="tag ok">owner</span></td>
            <td><span class="tag ok">全部权限</span></td>
            <td class="muted">.env · 不可删改</td>
        </tr>
        """
    if SUPER_ADMIN_ID and SUPER_ADMIN_ID != BOT_OWNER_ID:
        rows_html += f"""
        <tr>
            <td><code>{SUPER_ADMIN_ID}</code></td>
            <td>超级管理员</td>
            <td><span class="tag ok">super</span></td>
            <td><span class="tag ok">全部权限</span></td>
            <td class="muted">.env · 不可删改</td>
        </tr>
        """

    for row in get_all_admins_extended():
        uid = row["user_id"]
        if uid in protected:
            continue
        perm_labels = [
            ADMIN_PERMISSION_DEFS[k][0]
            for k, on in row["permissions"].items() if on
        ]
        perm_text = "、".join(perm_labels) if perm_labels else "（无）"
        edit_link = build_url(f"/users/admins/{uid}", token=token)
        delete_link = build_url(f"/users/admins/{uid}/delete", token=token)
        rows_html += f"""
        <tr>
            <td><code>{uid}</code></td>
            <td>{escape(row.get('note') or '-')}</td>
            <td><span class="tag">{escape(row['role'])}</span></td>
            <td style="white-space:normal;max-width:320px;">{escape(perm_text)}</td>
            <td>
                <a class="btn" href="{edit_link}">权限</a>
                <a class="btn" style="background:#dc2626;" href="{delete_link}"
                   onclick="return confirm('确定删除该管理员？');">删除</a>
            </td>
        </tr>
        """

    if not rows_html:
        rows_html = '<tr><td colspan="5" class="empty">暂无自定义管理员，请在下方添加</td></tr>'

    msg_block = f'<div class="card" style="margin-bottom:16px;"><b>{escape(message)}</b></div>' if message else ""
    token_hidden = f'<input type="hidden" name="token" value="{escape(token)}">' if token else ""
    default_checks = _permission_checkboxes_html("perm_", DEFAULT_ADMIN_PERMISSIONS)

    body = f"""
    <div class="topbar">
        <div>
            <h1>🛡 用户管理 · 管理员</h1>
            <div class="muted">由最高权限拥有者在后台添加；可勾选各项 Bot/后台权限</div>
        </div>
    </div>
    {users_tab_bar("admins", token)}
    {msg_block}

    <div class="card" style="margin-bottom:20px;">
        <h3>➕ 添加管理员</h3>
        <form method="post" action="/users/admins/add" class="filters" style="flex-direction:column;align-items:stretch;">
            {token_hidden}
            <div style="display:flex;gap:10px;flex-wrap:wrap;">
                <input type="number" name="user_id" required placeholder="Telegram User ID"
                    style="flex:1;min-width:160px;background:#0b1220;color:#e5e7eb;border:1px solid #374151;padding:10px 12px;border-radius:10px;">
                <input type="text" name="note" placeholder="备注（可选）"
                    style="flex:1;min-width:160px;background:#0b1220;color:#e5e7eb;border:1px solid #374151;padding:10px 12px;border-radius:10px;">
            </div>
            <div style="margin-top:12px;">
                <div class="muted" style="margin-bottom:8px;">勾选权限：</div>
                {default_checks}
            </div>
            <button class="btn" type="submit" style="margin-top:12px;align-self:flex-start;">添加管理员</button>
        </form>
    </div>

    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>User ID</th>
                    <th>备注</th>
                    <th>角色</th>
                    <th>已开通权限</th>
                    <th>操作</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """
    return page_shell("用户管理 - 管理员", body, token=token, show_home_nav=True)


def render_admin_edit_page(user_id: int, token: str | None = None, message: str | None = None):
    from db import _normalize_permissions

    protected = _protected_admin_ids()
    if user_id in protected:
        return page_shell(
            "不可编辑",
            '<div class="empty">该账号为系统最高权限，不可修改</div>',
            token=token,
            show_home_nav=True,
        )

    row = get_admin_record(user_id)
    if not row:
        return page_shell(
            "不存在",
            f'<div class="empty">管理员 <code>{user_id}</code> 不存在</div>',
            token=token,
            show_home_nav=True,
        )

    _, role, perms_raw, note = row
    perms = _normalize_permissions(perms_raw)
    admins_link = build_url("/users", token=token, tab="admins")
    token_hidden = f'<input type="hidden" name="token" value="{escape(token)}">' if token else ""
    checks = _permission_checkboxes_html("perm_", perms)
    msg_block = f'<div class="card" style="margin-bottom:16px;"><b>{escape(message)}</b></div>' if message else ""

    body = f"""
    <div class="topbar">
        <div>
            <h1>🛡 编辑管理员权限</h1>
            <div class="muted">User ID: <code>{user_id}</code> · 角色: {escape(role or 'admin')}</div>
        </div>
        <div>
            <a class="btn secondary" href="{admins_link}">返回管理员列表</a>
        </div>
    </div>
    {msg_block}
    <div class="card">
        <form method="post" action="/users/admins/{user_id}/save">
            {token_hidden}
            <label class="muted">备注</label>
            <input type="text" name="note" value="{escape(note or '')}"
                style="width:100%;background:#0b1220;color:#e5e7eb;border:1px solid #374151;padding:10px 12px;border-radius:10px;margin:8px 0 16px;">
            <div class="muted" style="margin-bottom:8px;">权限勾选：</div>
            {checks}
            <button class="btn" type="submit" style="margin-top:16px;">保存权限</button>
        </form>
    </div>
    """
    return page_shell(f"管理员 {user_id}", body, token=token, show_home_nav=True)


def render_user_detail_page(user_id: int, token: str | None = None, message: str | None = None):
    row = get_access_user_by_id(user_id)
    if not row:
        return page_shell(
            "用户不存在",
            f'<div class="empty">用户 <code>{user_id}</code> 不存在</div>',
            token=token,
            show_home_nav=True,
        )

    user_id, username, granted_by, granted_at, expires_at = row
    st_key, st_label = access_status(expires_at)
    tag_class = "ok" if st_key in ("active", "permanent") else "bad"

    users_link = build_url("/users", token=token, tab="vip")
    source = access_user_source_label(user_id)

    def action_url(path):
        return build_url(path, token=token)

    body = f"""
    <div class="topbar">
        <div>
            <h1>🧾 VIP 用户详情</h1>
            <div class="muted">来源：{escape(source)} · 续期、设永久、移除权限</div>
        </div>
        <div>
            <a class="btn secondary" href="{users_link}">返回 VIP 列表</a>
        </div>
    </div>

    {f'<div class="card" style="margin-bottom:16px;"><b>{escape(message)}</b></div>' if message else ''}

    <div class="stats">
        <div class="card"><div class="label">User ID</div><div class="value"><code>{user_id}</code></div></div>
        <div class="card"><div class="label">Username</div><div class="value">{escape(username or "-")}</div></div>
        <div class="card"><div class="label">授权时间</div><div class="value">{escape(fmt_ts(granted_at))}</div></div>
        <div class="card"><div class="label">到期时间</div><div class="value">{escape(fmt_ts(expires_at) if expires_at else "永久")}</div></div>
        <div class="card"><div class="label">状态</div><div class="value"><span class="tag {tag_class}">{st_label}</span></div></div>
    </div>

    <div class="card" style="margin-top:16px;">
        <h3>快捷操作</h3>
        <div class="filters">
            <a class="btn" href="{action_url(f'/user/{user_id}/grant/1m')}">续期 1个月</a>
            <a class="btn" href="{action_url(f'/user/{user_id}/grant/3m')}">续期 3个月</a>
            <a class="btn" href="{action_url(f'/user/{user_id}/grant/6m')}">续期 6个月</a>
            <a class="btn" href="{action_url(f'/user/{user_id}/grant/1y')}">续期 1年</a>
            <a class="btn secondary" href="{action_url(f'/user/{user_id}/grant/permanent')}">设为永久</a>
            <a class="btn" style="background:#dc2626;" href="{action_url(f'/user/{user_id}/revoke')}">移除权限</a>
        </div>
    </div>
    """
    return page_shell(f"用户 {user_id}", body, token=token, show_home_nav=True)


def render_background_page(token: str | None = None):
    snap = load_background_snapshot()
    api_link = build_url("/api/background", token=token)

    if not snap:
        body = f"""
        <div class="topbar">
            <div>
                <h1>⚙️ 后台任务</h1>
                <div class="muted">暂无 Bot 进程上报的状态</div>
            </div>
        </div>
        <div class="card">
            <p>请先启动 PyCharm 配置 <b>「运行 Bot」</b>（<code>app.py</code>），Bot 会每 15 秒把任务状态写入数据库。</p>
            <p class="muted">本页由 <code>web.py</code>（端口 {PORT}）提供；后台循环任务运行在 <code>app.py</code> 进程中。</p>
        </div>
        """
        return page_shell("后台任务", body, token=token, show_home_nav=True)

    tasks = snap.get("tasks") or []
    task_rows = ""
    for t in tasks:
        st = escape((t.get("status") or "").upper())
        badge = t.get("badge") or ""
        tag_cls = "ok" if st == "RUNNING" else ("bad" if badge == "red" else "warn")
        detail = escape(t.get("detail") or "")
        detail_html = f'<div class="muted">{detail}</div>' if detail else ""
        task_rows += f"""
        <tr>
            <td><code>{escape(t.get('name') or '-')}</code></td>
            <td>{escape(t.get('description') or '—')}</td>
            <td><span class="tag {tag_cls}">{st}</span>{detail_html}</td>
        </tr>
        """

    if not task_rows:
        task_rows = '<tr><td colspan="3" class="empty">暂无任务数据</td></tr>'

    bot_mode = escape(snap.get("bot_mode") or "—")
    body = f"""
    <div class="topbar">
        <div>
            <h1>⚙️ 后台任务</h1>
            <div class="muted">数据来自 Bot 进程 · 每 15 秒刷新 · 更新时间：<span id="bg-time">{escape(snap.get('time') or '-')}</span></div>
        </div>
    </div>

    <div class="stats">
        <div class="card"><div class="label">环境</div><div class="value">{escape(snap.get('env') or '-')}</div></div>
        <div class="card"><div class="label">Bot 模式</div><div class="value">{bot_mode}</div></div>
        <div class="card"><div class="label">Bot 用户名</div><div class="value">@{escape(snap.get('bot_username') or '—')}</div></div>
        <div class="card"><div class="label">自动收款间隔</div><div class="value">{escape(str(snap.get('auto_pay_interval', '-')))}s</div></div>
        <div class="card"><div class="label">收款地址</div><div class="value">{'已配置' if snap.get('payment_address_set') else '未配置'}</div></div>
        <div class="card"><div class="label">HTTP 会话</div><div class="value">{'正常' if snap.get('http_session_open') else '已关闭'}</div></div>
    </div>

    <div class="table-wrap" style="margin-top:16px;">
        <table>
            <thead><tr><th>任务名</th><th>说明</th><th>状态</th></tr></thead>
            <tbody id="bg-task-body">{task_rows}</tbody>
        </table>
    </div>

    <script>
    async function refreshBackground() {{
        try {{
            const r = await fetch({json.dumps(api_link)});
            if (!r.ok) return;
            const d = await r.json();
            const timeEl = document.getElementById('bg-time');
            if (timeEl) timeEl.textContent = d.time || '-';
            const tbody = document.getElementById('bg-task-body');
            if (!tbody || !d.tasks) return;
            tbody.innerHTML = d.tasks.map(t => {{
                const st = (t.status || '').toUpperCase();
                const cls = st === 'RUNNING' ? 'ok' : (t.badge === 'red' ? 'bad' : 'warn');
                const detail = t.detail ? `<div class="muted">${{t.detail}}</div>` : '';
                return `<tr><td><code>${{t.name || '-'}}</code></td><td>${{t.description || '—'}}</td><td><span class="tag ${{cls}}">${{st}}</span>${{detail}}</td></tr>`;
            }}).join('') || '<tr><td colspan="3" class="empty">暂无任务数据</td></tr>';
        }} catch (e) {{ console.warn('background refresh failed', e); }}
    }}
    setInterval(refreshBackground, 15000);
    </script>
    """
    return page_shell("后台任务", body, token=token, show_home_nav=True)


@app.get("/login", response_class=HTMLResponse)
def login_page(
    next: str = Query(default="/dashboard"),
    token: str | None = Query(default=None),
):
    if check_token(token):
        return RedirectResponse(url=build_url(next, token=token), status_code=302)
    return render_login_page(next_path=next)


@app.post("/login")
def login_submit(password: str = Form(...), next: str = Form("/dashboard")):
    if password != WEB_TOKEN:
        return render_login_page(error="❌ 密码错误，请检查 .env 中的 WEB_TOKEN", next_path=next)
    return RedirectResponse(url=build_url(next, token=WEB_TOKEN), status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(token: str | None = Query(default=None)):
    denied = require_token_or_login(token)
    if denied:
        return denied
    return render_dashboard_page(token=token)


@app.get("/dm-chats", response_class=HTMLResponse)
def dm_chats_page(
    token: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
):
    denied = require_token_or_login(token, "/dm-chats")
    if denied:
        return denied
    return render_dm_chats_page(token=token, keyword=keyword)


@app.get("/dm-chats/{peer_id}", response_class=HTMLResponse)
def dm_chat_detail_page(peer_id: int, token: str | None = Query(default=None)):
    denied = require_token_or_login(token, "/dm-chats")
    if denied:
        return denied
    return render_dm_chat_detail_page(peer_id, token=token)


@app.get("/users", response_class=HTMLResponse)
def users_page(
    token: str | None = Query(default=None),
    tab: str = Query(default="vip"),
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
    msg: str | None = Query(default=None),
):
    denied = require_token_or_login(token, "/users")
    if denied:
        return denied
    if tab == "admins":
        return render_admins_page(token=token, message=msg)
    return render_vip_users_page(token=token, keyword=keyword, status=status, message=msg)


@app.post("/users/admins/add")
async def admins_add(
    request: Request,
    token: str | None = Form(default=None),
    user_id: int = Form(...),
    note: str = Form(default=""),
):
    require_token(token)
    protected = _protected_admin_ids()
    if int(user_id) in protected:
        return RedirectResponse(
            url=build_url("/users", token=token, tab="admins", msg="不能修改系统最高权限账号"),
            status_code=303,
        )
    form = await request.form()
    perms = {k: True for k in ADMIN_PERMISSION_DEFS if form.get(f"perm_{k}")}
    if not any(perms.values()):
        perms = dict(DEFAULT_ADMIN_PERMISSIONS)
    add_admin(int(user_id), "admin", permissions=perms, note=note.strip())
    return RedirectResponse(
        url=build_url("/users", token=token, tab="admins", msg=f"已添加管理员 {user_id}"),
        status_code=303,
    )


@app.get("/users/admins/{user_id}", response_class=HTMLResponse)
def admin_edit_page(
    user_id: int,
    token: str | None = Query(default=None),
    msg: str | None = Query(default=None),
):
    denied = require_token_or_login(token, "/users")
    if denied:
        return denied
    return render_admin_edit_page(user_id, token=token, message=msg)


@app.post("/users/admins/{user_id}/save")
async def admin_save(
    user_id: int,
    request: Request,
    token: str | None = Form(default=None),
    note: str = Form(default=""),
):
    require_token(token)
    if user_id in _protected_admin_ids():
        return RedirectResponse(
            url=build_url("/users", token=token, tab="admins", msg="不可编辑系统账号"),
            status_code=303,
        )
    form = await request.form()
    perms = {k: True for k in ADMIN_PERMISSION_DEFS if form.get(f"perm_{k}")}
    set_admin_permissions(user_id, perms, note=note.strip())
    return RedirectResponse(
        url=build_url(f"/users/admins/{user_id}", token=token, msg="权限已保存"),
        status_code=303,
    )


@app.get("/users/admins/{user_id}/delete")
def admin_delete(user_id: int, token: str | None = Query(default=None)):
    require_token(token)
    if user_id in _protected_admin_ids():
        return RedirectResponse(
            url=build_url("/users", token=token, tab="admins", msg="不能删除系统最高权限账号"),
            status_code=303,
        )
    remove_admin(user_id)
    return RedirectResponse(
        url=build_url("/users", token=token, tab="admins", msg=f"已删除管理员 {user_id}"),
        status_code=303,
    )


@app.get("/user/{user_id}", response_class=HTMLResponse)
def user_detail_page(
    user_id: int,
    token: str | None = Query(default=None),
    msg: str | None = Query(default=None),
):
    denied = require_token_or_login(token, f"/user/{user_id}")
    if denied:
        return denied
    return render_user_detail_page(user_id=user_id, token=token, message=msg)


@app.get("/user/{user_id}/grant/{plan}")
def user_grant_action(
    user_id: int,
    plan: str,
    token: str | None = Query(default=None),
):
    require_token(token)

    if plan == "1m":
        extend_access_user(user_id, 30 * 24 * 3600)
        msg = "已续期 1个月"
    elif plan == "3m":
        extend_access_user(user_id, 90 * 24 * 3600)
        msg = "已续期 3个月"
    elif plan == "6m":
        extend_access_user(user_id, 180 * 24 * 3600)
        msg = "已续期 6个月"
    elif plan == "1y":
        extend_access_user(user_id, 365 * 24 * 3600)
        msg = "已续期 1年"
    elif plan == "permanent":
        set_access_user_permanent(user_id)
        msg = "已设为永久"
    else:
        msg = "未知操作"

    return RedirectResponse(url=build_url(f"/user/{user_id}", token=token, msg=msg), status_code=303)


@app.get("/user/{user_id}/revoke")
def user_revoke_action(
    user_id: int,
    token: str | None = Query(default=None),
):
    require_token(token)
    remove_access_user(user_id)
    return RedirectResponse(url=build_url("/users", token=token, tab="vip"), status_code=303)


def render_orders_page(token: str | None = None, status: str | None = None):
    if status in ("pending", "paid", "rejected"):
        rows = get_rental_orders_by_status(status, limit=100)
        title = f"订单管理 - {status}"
    else:
        rows = get_rental_orders_by_status(None, limit=100)
        title = "订单管理"

    html_rows = ""

    for row in rows:
        try:
            order_code, user_id, username, full_name, category_title, plan_label, amount, st, created_at, paid_at, expires_at = row
        except Exception:
            continue

        approve_link = build_url(f"/order/{order_code}", token=token) + "/approve"
        reject_link = build_url(f"/order/{order_code}", token=token) + "/reject"

        actions = "-"
        if st == "pending":
            actions = (
                f'<a class="btn" href="{build_url(f"/order/{order_code}/approve", token=token)}">确认付款</a> '
                f'<a class="btn" style="background:#dc2626;" href="{build_url(f"/order/{order_code}/reject", token=token)}">拒绝</a>'
            )

        html_rows += f"""
        <tr>
            <td><code>{escape(str(order_code))}</code></td>
            <td><code>{escape(str(user_id))}</code></td>
            <td>{escape(str(username or "-"))}</td>
            <td>{escape(str(category_title or "-"))}</td>
            <td>{escape(str(plan_label or "-"))}</td>
            <td>{escape(fmt_num(amount))}U</td>
            <td>{escape(str(st or "-"))}</td>
            <td>{escape(fmt_ts(created_at))}</td>
            <td>{actions}</td>
        </tr>
        """

    if not html_rows:
        html_rows = '<tr><td colspan="9" class="empty">暂无订单</td></tr>'

    body = f"""
    <div class="topbar">
        <div>
            <h1>📦 {escape(title)}</h1>
        </div>
    </div>

    <div class="filters">
        <a class="btn secondary" href="{build_url('/orders', token=token)}">全部</a>
        <a class="btn secondary" href="{build_url('/orders', token=token, status='pending')}">待支付</a>
        <a class="btn secondary" href="{build_url('/orders', token=token, status='paid')}">已支付</a>
        <a class="btn secondary" href="{build_url('/orders', token=token, status='rejected')}">已拒绝</a>
    </div>

    <div class="table-wrap">
        <table>
            <thead>
                <tr>
                    <th>订单号</th>
                    <th>User ID</th>
                    <th>Username</th>
                    <th>类型</th>
                    <th>套餐</th>
                    <th>金额</th>
                    <th>状态</th>
                    <th>创建时间</th>
                    <th>操作</th>
                </tr>
            </thead>
            <tbody>{html_rows}</tbody>
        </table>
    </div>
    """
    return page_shell(title, body, token=token, show_home_nav=True)
    
@app.get("/orders", response_class=HTMLResponse)
def orders_page(
    token: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    denied = require_token_or_login(token, "/orders")
    if denied:
        return denied
    return render_orders_page(token=token, status=status)

@app.get("/order/{order_code}/approve")
def order_approve_action(order_code: str, token: str | None = Query(default=None)):
    require_token(token)
    approve_rental_order(order_code)
    return RedirectResponse(url=build_url("/orders", token=token, status="pending"), status_code=303)

@app.get("/order/{order_code}/reject")
def order_reject_action(order_code: str, token: str | None = Query(default=None)):
    require_token(token)
    row = get_rental_order(order_code)
    if row:
        _, _, _, _, _, _, _, _, _, status, _, _, _, _ = row
        if status == "pending":
            mark_rental_order_rejected(order_code)
    return RedirectResponse(url=build_url("/orders", token=token, status="pending"), status_code=303)


@app.get("/", include_in_schema=False)
def home(token: str | None = Query(default=None)):
    if check_token(token):
        return RedirectResponse(url=build_url("/dashboard", token=token), status_code=302)
    return render_login_page()



@app.get("/groups", response_class=HTMLResponse)
def groups_page(token: str | None = Query(default=None)):
    denied = require_token_or_login(token, "/groups")
    if denied:
        return denied
    return render_groups_page(token=token)


@app.get("/groups/sync", response_class=HTMLResponse)
def groups_sync_page(
    token: str | None = Query(default=None),
    msg: str | None = Query(default=None),
):
    denied = require_token_or_login(token, "/groups/sync")
    if denied:
        return denied
    return render_groups_sync_page(token=token, message=msg)


@app.get("/groups/sync/run")
def groups_sync_run(token: str | None = Query(default=None)):
    denied = require_token_or_login(token, "/groups/sync")
    if denied:
        return denied
    result = sync_groups_from_telegram()
    message = (
        f"同步完成：共 {result['total']} 个群，成功 {result['ok']}，失败 {result['fail']}"
    )
    if result["errors"]:
        message += "；错误示例：" + " | ".join(result["errors"][:3])
    return RedirectResponse(
        url=build_url("/groups/sync", token=token, msg=message),
        status_code=303,
    )


@app.get("/group/{chat_id}/records", response_class=HTMLResponse)
def group_records_page(chat_id: int, token: str | None = Query(default=None)):
    denied = require_token_or_login(token, f"/group/{chat_id}/records")
    if denied:
        return denied
    return render_group_records_page(chat_id, token=token)


@app.get("/group/{chat_id}/members", response_class=HTMLResponse)
def group_members_page(
    chat_id: int,
    token: str | None = Query(default=None),
    msg: str | None = Query(default=None),
):
    denied = require_token_or_login(token, f"/group/{chat_id}/members")
    if denied:
        return denied
    return render_group_members_page(chat_id, token=token, message=msg)


@app.get("/group/{chat_id}/members/sync")
def group_members_sync(chat_id: int, token: str | None = Query(default=None)):
    denied = require_token_or_login(token, f"/group/{chat_id}/members")
    if denied:
        return denied
    result = sync_group_members_from_telegram(chat_id)
    if result.get("errors") and not result.get("saved"):
        message = "同步失败：" + " | ".join(result["errors"][:3])
    else:
        message = f"已同步 {result.get('saved', 0)} 名管理员到成员库"
        if result.get("errors"):
            message += "（部分失败：" + " | ".join(result["errors"][:2]) + "）"
    return RedirectResponse(
        url=build_url(f"/group/{chat_id}/members", token=token, msg=message),
        status_code=303,
    )


@app.get("/group/{chat_id}", response_class=HTMLResponse)
def group_history(
    chat_id: int,
    date: str | None = Query(default=None),
    token: str | None = Query(default=None),
):
    denied, access = require_group_bill_page(chat_id, token)
    if denied:
        return denied
    return render_group_history_page(chat_id, date_str=date, token=token, access=access)


@app.get("/api/background")
def api_background(token: str | None = Query(default=None)):
    require_token(token)
    snap = load_background_snapshot()
    if not snap:
        return {"ok": False, "error": "no snapshot", "tasks": []}
    return {"ok": True, **snap}


@app.get("/background", response_class=HTMLResponse)
def background_page(token: str | None = Query(default=None)):
    denied = require_token_or_login(token, "/background")
    if denied:
        return denied
    return render_background_page(token=token)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ================= RUN =================
if __name__ == "__main__":
    print(f"Web 后台已启动: http://127.0.0.1:{PORT}/")
    print("浏览器打开上述地址，输入 .env 中的 WEB_TOKEN 即可登录")
    try:
        uvicorn.run("web:app", host="0.0.0.0", port=PORT)
    except OSError as e:
        if getattr(e, "errno", None) == 48:
            print(f"\n❌ 端口 {PORT} 已被占用。请关闭旧的 web.py 进程，或修改 .env 的 WEB_PORT。")
            print(f"   查看占用: lsof -i :{PORT}")
        raise
