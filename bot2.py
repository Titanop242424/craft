#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🔥 FF CARFT LAND FOLLOW BOT — ULTRA FAST EDITION (v6.2)
• All commands defined
• Instant response with aggressive concurrency
• 128 thread executor for maximum parallelism
• Non-blocking notifications
"""

import asyncio
import concurrent.futures
import json
import logging
import re
import warnings
from datetime import datetime, date, timedelta

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToDict
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from bson import ObjectId

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.helpers import escape_markdown
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes,
)

import follow_pb2

# silence harmless Atlas TLS cert warning
warnings.filterwarnings("ignore", message="Parsed a serial number")

# ── Silence Telegram/httpx log spam ──
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# ══════════════════════════════════════════════════════════════
# ⚡ MASSIVE THREAD EXECUTOR — MAXIMUM CONCURRENCY
# ══════════════════════════════════════════════════════════════
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=128)

async def _run(fn, *args):
    """Run a BLOCKING call in a worker thread with maximum speed."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, fn, *args)

# ══════════════════════════════════════════════════════════════
# 🔧 CONFIG — EDIT THESE
# ══════════════════════════════════════════════════════════════
BOT_TOKEN    = "8680371930:AAFgIIrLBQi_YlEBdVIZCfL9k9AWdGf-Yqw"
ADMIN_IDS    = [8888758201]
MONGO_URI    = "mongodb+srv://titanop24:titanop24@cluster0.7lvigzh.mongodb.net/?appName=Cluster0"
DB_NAME      = "ff_carft_land"
BOT_USERNAME = "ffcarftlandbot"

# Game API
KEY         = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
IV          = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])
JWT_API     = "https://ff-jwt-gen-api.lovable.app/api/public/token"
FOLLOW_URL  = "https://client.ind.freefiremobile.com/Follow"

DAILY_COINS = 5
REF_COINS   = 5
MAX_CAP     = 50
FOLLOW_DELAY = 0.5
BATCH_SIZE  = 20

# ══════════════════════════════════════════════════════════════
# 🎨 UI
# ══════════════════════════════════════════════════════════════
THIN = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

def box(title: str) -> str:
    return f"╔{'═'*44}╗\n║{title.center(44)}║\n╚{'═'*44}╝"

def B(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=cb)

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[B("⬅️ Back", "menu_main")]])

def E(t):
    return escape_markdown(str(t), version=1)

async def safe_edit(q, text: str, parse_mode=None, reply_markup=None):
    try:
        await q.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest:
        pass

# ══════════════════════════════════════════════════════════════
# 🗄️ MONGODB — OPTIMIZED
# ══════════════════════════════════════════════════════════════
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

_client = MongoClient(MONGO_URI, maxPoolSize=200, minPoolSize=50)
db      = _client[DB_NAME]
users_coll     = db.users
accounts_coll  = db.accounts
log_coll       = db.follow_logs
settings_coll  = db.settings
referral_logs  = db.referral_logs
channels_coll  = db.channels
admin_coll     = db.admins
error_logs_coll = db.error_logs

# Create all indexes for maximum speed
def create_indexes():
    try:
        accounts_coll.create_index("uid", unique=True)
        accounts_coll.create_index([("status", 1), ("capacity", -1)])
        accounts_coll.create_index("status")
        accounts_coll.create_index("capacity")
        log_coll.create_index("at")
        referral_logs.create_index([("ref_by", 1), ("at", -1)])
        admin_coll.create_index("user_id", unique=True)
        error_logs_coll.create_index([("uid", 1), ("at", -1)])
        users_coll.create_index("_id")
    except Exception as e:
        logger.warning("Index creation warning: %s", e)

create_indexes()

# Cache settings for ultra-fast access
_settings_cache = None
_settings_cache_time = None

def get_settings_cached():
    global _settings_cache, _settings_cache_time
    now = datetime.utcnow()
    if _settings_cache is None or _settings_cache_time is None or (now - _settings_cache_time).seconds > 60:
        s = settings_coll.find_one({"_id": "config"}) or {}
        _settings_cache = {
            "daily_coins": int(s.get("daily_coins", DAILY_COINS)),
            "ref_coins":   int(s.get("ref_coins", REF_COINS)),
        }
        _settings_cache_time = now
    return _settings_cache

def init_admins():
    for admin_id in ADMIN_IDS:
        try:
            admin_coll.update_one(
                {"user_id": admin_id},
                {"$set": {"user_id": admin_id, "added_at": datetime.utcnow()}},
                upsert=True
            )
        except Exception:
            pass
init_admins()

def get_settings() -> dict:
    return get_settings_cached()

def set_setting(key: str, value: int):
    settings_coll.update_one(
        {"_id": "config"},
        {"$set": {key: int(value)}},
        upsert=True,
    )
    global _settings_cache, _settings_cache_time
    _settings_cache = None
    _settings_cache_time = None

def is_admin(user_id: int) -> bool:
    return admin_coll.find_one({"user_id": user_id}) is not None

def get_all_admins() -> list:
    return [a["user_id"] for a in admin_coll.find({}, {"user_id": 1})]

def add_admin(user_id: int) -> bool:
    try:
        admin_coll.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "added_at": datetime.utcnow()}},
            upsert=True
        )
        return True
    except Exception:
        return False

def remove_admin(user_id: int) -> bool:
    result = admin_coll.delete_one({"user_id": user_id})
    return result.deleted_count > 0

def get_user_sync(uid: int, username: str = "", first_name: str = ""):
    daily = get_settings()["daily_coins"]
    today = date.today().isoformat()
    user = users_coll.find_one({"_id": uid})
    if not user:
        user = {
            "_id": uid, "username": username, "first_name": first_name,
            "daily_coins": daily, "referral_coins": 0,
            "total_earned": 0, "total_spent": 0,
            "last_daily_reset": today, "banned": False,
            "referred_by": None, "referred_users": [],
            "created_at": datetime.utcnow(),
        }
        users_coll.insert_one(user)
        return user
    if user.get("last_daily_reset") != today:
        users_coll.update_one(
            {"_id": uid},
            {"$set": {"daily_coins": daily, "last_daily_reset": today}})
        user = users_coll.find_one({"_id": uid})
    return user

async def get_user(uid: int, username: str = "", first_name: str = ""):
    return await _run(get_user_sync, uid, username, first_name)

def available_coins(user) -> int:
    return int(user.get("daily_coins", 0)) + int(user.get("referral_coins", 0))

def spend_coin(user_id: int) -> str:
    user = users_coll.find_one({"_id": user_id})
    if int(user.get("daily_coins", 0)) > 0:
        users_coll.update_one({"_id": user_id},
            {"$inc": {"daily_coins": -1, "total_spent": 1}})
        return "daily"
    users_coll.update_one({"_id": user_id},
        {"$inc": {"referral_coins": -1, "total_spent": 1}})
    return "referral"

# ══════════════════════════════════════════════════════════════
# 🔐 CRYPTO + PROTOBUF — OPTIMIZED
# ══════════════════════════════════════════════════════════════
def encrypt_payload(data: bytes) -> bytes:
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    return cipher.encrypt(pad(data, AES.block_size))

def get_jwt(uid: str, password: str):
    try:
        r = requests.get(f"{JWT_API}?uid={uid}&password={password}", timeout=10)
        if r.status_code == 200:
            data = r.json()
            return (data.get("token") or data.get("jwt")
                    or (data.get("data") or {}).get("token"))
    except Exception:
        pass
    return None

def do_follow(target_id: int, jwt: str):
    try:
        req = follow_pb2.CSFollowReq()
        req.target_id = target_id
        encrypted = encrypt_payload(req.SerializeToString())
        headers = {
            "User-Agent": "UnityPlayer/2022.3.47f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
            "Accept": "*/*",
            "Accept-Encoding": "deflate, gzip",
            "Authorization": f"Bearer {jwt}",
            "X-Ga": "v1 1",
            "Releaseversion": "OB54",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Unity-Version": "2022.3.47f1",
        }
        resp = requests.post(FOLLOW_URL, headers=headers, data=encrypted, timeout=15)
        if resp.status_code != 200:
            return "http_error", {"status": resp.status_code, "text": resp.text[:120]}
        res = follow_pb2.CSFollowRes()
        res.ParseFromString(resp.content)
        d = MessageToDict(res, preserving_proto_field_name=True)
        return categorize(d), d
    except Exception as e:
        return "exception", {"error": str(e)}

def categorize(d: dict) -> str:
    fi = d.get("fail_info", "")
    if fi == "BR_WORKSHOP_FOLLOW_LIMIT_EXCEEDED":
        return "limit"
    if fi == "BR_WORKSHOP_ALREADY_FOLLOWED":
        return "already"
    if fi == "BR_WORKSHOP_ACCOUNT_NOT_FOUND":
        return "not_found"
    if "info" in d:
        return "success"
    return "other"

def extract_capacity(d: dict) -> int:
    cap = d.get("remaining_follow_capacity")
    if cap is None:
        cap = d.get("remaining_capacity")
    if cap is None:
        cs = d.get("creator_stats") or {}
        cap = cs.get("remaining_follow_capacity")
    if cap is None:
        return None
    try:
        return int(cap)
    except (TypeError, ValueError):
        return None

# ══════════════════════════════════════════════════════════════
# 📦 ACCOUNT FILE PARSING — FAST
# ══════════════════════════════════════════════════════════════
def parse_accounts(content: str):
    content = re.sub(r",\s*}", "}", content)
    content = re.sub(r",\s*]", "]", content)
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list) or isinstance(data, dict):
        accounts, items = [], []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for k in ("accounts", "users", "data", "list"):
                if k in data and isinstance(data[k], list):
                    items = data[k]; break
            if not items:
                items = [data]
        for obj in items:
            acc = _extract(obj)
            if acc:
                accounts.append(acc)
        if accounts:
            return accounts

    return _regex_accounts(content) or _line_accounts(content)

def _extract(obj):
    if not isinstance(obj, dict):
        return None
    uid = next((str(obj[k]) for k in ("uid", "UID", "userId", "user_id", "userid", "id", "account_id")
                if obj.get(k)), None)
    if not uid:
        return None
    pwd = next((str(obj[k]) for k in ("password", "pass", "pwd", "Password", "PASSWORD")
                if obj.get(k)), None)
    jwt = next((str(obj[k]) for k in ("jwt_token", "jwt", "token", "JWT", "access_token", "accessToken")
                if obj.get(k)), None)
    cap = next((int(obj[k]) for k in ("capacity", "cap", "remaining")
                if obj.get(k) is not None and str(obj[k]).isdigit()), None)
    acc = {"uid": uid}
    if pwd: acc["password"] = pwd
    if jwt: acc["jwt_token"] = jwt
    if cap: acc["capacity"] = cap
    return acc

def _regex_accounts(content: str):
    accounts = []
    pattern = r'["\']?uid["\']?\s*:\s*["\']?(\d+)["\']?.*?["\']?password["\']?\s*:\s*["\']([^"\']+)["\']'
    for uid, pwd in re.findall(pattern, content, re.IGNORECASE | re.DOTALL):
        accounts.append({"uid": uid, "password": pwd})
    return accounts

def _line_accounts(content: str):
    accounts = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\|([^|]+)(?:\|(\d+))?$", line)
        if m:
            acc = {"uid": m.group(1), "password": m.group(2).strip()}
            if m.group(3):
                acc["capacity"] = int(m.group(3))
            accounts.append(acc)
            continue
        m = re.match(r"^(\d+)[:,\s]+(\S+)$", line)
        if m:
            accounts.append({"uid": m.group(1), "password": m.group(2)})
    return accounts

def looks_like_accounts(text: str) -> bool:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines:
        pipe = sum(1 for ln in lines if re.match(r"^\d+\|.+$", ln))
        if pipe and pipe >= max(1, int(len(lines) * 0.6)):
            return True
    low = text.lower()
    if '"uid"' in low or "'uid'" in low or '"password"' in low or "'password'" in low:
        return True
    return False

# ══════════════════════════════════════════════════════════════
# 📢 CHANNEL MANAGEMENT — FAST
# ══════════════════════════════════════════════════════════════
def parse_channel_ref(text: str):
    t = text.strip()
    m = re.match(r"https?://t\.me/([A-Za-z0-9_+]+)", t)
    if m:
        ref = m.group(1)
        if ref.startswith("+") or ref == "joinchat":
            return f"https://t.me/{ref}", None
        return f"https://t.me/{ref}", ref
    m = re.match(r"^@([A-Za-z0-9_]+)$", t)
    if m:
        return f"https://t.me/{m.group(1)}", m.group(1)
    if re.match(r"^-?\d+$", t):
        return f"https://t.me/{t}", int(t)
    if re.match(r"^[A-Za-z][A-Za-z0-9_]{3,}$", t):
        return f"https://t.me/{t}", t
    return None, None

async def add_channel(bot, text: str):
    link, chat_ref = parse_channel_ref(text)
    if not link:
        return None, ("Couldn't parse input.\nSend: `https://t.me/ChannelName`, "
                      "`@ChannelName`, `ChannelName` or chat ID `-100xxxx`."), False
    if chat_ref is None:
        return None, ("Private invite links can't be verified. Add the bot as *admin* "
                      "in the channel and send the channel *username* or *ID* instead."), False
    api_ref = chat_ref if isinstance(chat_ref, int) else f"@{chat_ref}"
    title = str(chat_ref)
    chat_id_store = api_ref
    try:
        chat = await bot.get_chat(api_ref)
        title = chat.title or chat.username or str(chat.id)
        chat_id_store = chat.id
        if chat.username:
            link = f"https://t.me/{chat.username}"
    except Exception:
        pass
    existing = await _run(channels_coll.find_one, {"chat_id": str(chat_id_store)})
    if existing:
        return existing, None, True
    doc = {
        "chat_id": str(chat_id_store),
        "link": link,
        "title": title,
        "added_at": datetime.utcnow(),
    }
    await _run(channels_coll.insert_one, doc)
    return doc, None, False

async def not_joined_channels(bot, user_id: int) -> list:
    out = []
    channels = await _run(lambda: list(channels_coll.find({})))
    for ch in channels:
        chat_ref = ch.get("chat_id")
        if not chat_ref:
            continue
        try:
            chat_ref = int(chat_ref) if str(chat_ref).lstrip("-").isdigit() else chat_ref
            m = await bot.get_chat_member(chat_ref, user_id)
            if m.status in ("member", "administrator", "creator"):
                continue
            out.append(ch)
        except Exception:
            continue
    return out

def join_prompt_text(missing: list) -> str:
    names = "\n".join(f"🔸 {ch.get('title') or ch.get('chat_id')}" for ch in missing)
    return (f"{box('🔒 JOIN REQUIRED')}\n"
            f"To use the bot, you must join our channel(s) first:\n\n"
            f"{names}\n\n"
            f"Join and tap *✅ I've Joined* below.")

def join_kb(missing: list) -> InlineKeyboardMarkup:
    kb = [[InlineKeyboardButton(f"🔗 Join {ch.get('title') or ch.get('chat_id')}", url=ch["link"])]
          for ch in missing if ch.get("link")]
    kb.append([B("✅ I've Joined", "check_joined")])
    return InlineKeyboardMarkup(kb)

async def join_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if is_admin(uid):
        return True
    missing = await not_joined_channels(context.bot, uid)
    if missing:
        await update.message.reply_text(join_prompt_text(missing),
                                        parse_mode=ParseMode.MARKDOWN,
                                        reply_markup=join_kb(missing))
        return False
    return True

def channels_text() -> str:
    chs = list(channels_coll.find({}))
    txt = f"{box('📢 CHANNEL MGMT')}\n"
    if not chs:
        txt += "No channels added yet.\nUsers are NOT forced to join."
    else:
        for i, ch in enumerate(chs, 1):
            txt += f"{i}. {ch.get('title')} — {ch.get('link')}\n"
    txt += (f"\n{THIN}\n📡 Total: *{len(chs)}* channel(s)\n\n"
            f"⚠️ Bot must be *admin* in each channel\n"
            f"to verify memberships.")
    return txt

def channels_kb() -> InlineKeyboardMarkup:
    kb = []
    for ch in channels_coll.find({}):
        kb.append([B(f"❌ {ch.get('title')}", f"remove_channel_{ch['_id']}")])
    kb.append([B("➕ Add Channel", "action_add_channel")])
    kb.append([B("⬅️ Back", "menu_admin")])
    return InlineKeyboardMarkup(kb)

# ══════════════════════════════════════════════════════════════
# 🧵 ACTIVE JOBS / CANCEL
# ══════════════════════════════════════════════════════════════
active_tasks = {}
cancel_flags = {}

def is_cancelled(uid: int) -> bool:
    return cancel_flags.get(uid, False)

# ══════════════════════════════════════════════════════════════
# 🔔 ADMIN NOTIFICATION — FAST
# ══════════════════════════════════════════════════════════════
async def notify_admin_limit_reached(context: ContextTypes.DEFAULT_TYPE, uid: str, 
                                     target_id: int, response_data: dict):
    acc = await _run(accounts_coll.find_one, {"uid": uid})
    if not acc:
        return
    
    password = acc.get("password", "N/A")
    capacity = acc.get("capacity", "N/A")
    
    error_doc = {
        "uid": uid,
        "error_type": "FOLLOW_LIMIT_EXCEEDED",
        "error_detail": "Account reached follow limit",
        "response_data": response_data,
        "target_id": target_id,
        "at": datetime.utcnow(),
        "resolved": False
    }
    await _run(error_logs_coll.insert_one, error_doc)
    
    notification = (
        f"{box('🚨 FOLLOW LIMIT REACHED')}\n"
        f"⚠️ *Account:* `{uid}`\n"
        f"🎯 *Target:* `{target_id}`\n"
        f"🔑 *Password:* `{password}`\n"
        f"⚡ *Capacity:* `{capacity}`\n"
        f"📝 *Status:* Account can no longer follow anyone\n\n"
        f"{THIN}\n"
        f"🕐 *Time:* {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    
    kb = InlineKeyboardMarkup([
        [B("🗑️ Delete Account", f"admin_delete_acc_{uid}")],
        [B("🔄 Restock Account", f"admin_restock_acc_{uid}")],
        [B("📋 View Response", f"admin_view_error_{uid}")]
    ])
    
    admins = get_all_admins()
    for admin_id in admins:
        try:
            await context.bot.send_message(
                admin_id,
                notification,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb
            )
        except Exception:
            pass

# ══════════════════════════════════════════════════════════════
# 📢 NOTIFICATIONS — NON-BLOCKING
# ══════════════════════════════════════════════════════════════

async def _send_single_notification(context: ContextTypes.DEFAULT_TYPE, uid: int, message: str):
    """Send a single notification."""
    try:
        await context.bot.send_message(uid, message, parse_mode=ParseMode.MARKDOWN)
        return 1
    except Exception:
        return 0

async def _send_notifications_async(context: ContextTypes.DEFAULT_TYPE, user_ids: list, new: int):
    """Send notifications in background without blocking."""
    message = (
        f"📦 *NEW STOCK!*\n"
        f"🎉 *{new}* fresh accounts added!\n"
        f"🚀 `/follow <UID>`"
    )
    
    # Send in batches of 50 to avoid rate limits
    batch_size = 50
    for i in range(0, len(user_ids), batch_size):
        batch = user_ids[i:i+batch_size]
        tasks = [_send_single_notification(context, uid, message) for uid in batch]
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(0.1)  # Small delay between batches

async def notify_stock_update(context: ContextTypes.DEFAULT_TYPE, new: int) -> int:
    """Send stock notifications in background without blocking the bot."""
    if new <= 0:
        return 0
    
    # Get user IDs in background
    user_ids = await _run(lambda: [u["_id"] for u in users_coll.find({}, {"_id": 1})])
    
    if not user_ids:
        return 0
    
    # Create notification task in background - doesn't block the main loop
    asyncio.create_task(_send_notifications_async(context, user_ids, new))
    
    # Return immediately - don't wait for notifications to complete
    return len(user_ids)

# ══════════════════════════════════════════════════════════════
# 🚀 ULTRA FAST FOLLOW ENGINE
# ══════════════════════════════════════════════════════════════
async def run_follow_job(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         target_id: int, budget: int, is_admin_user: bool):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    budget_txt = "∞" if is_admin_user else str(budget)

    msg = await context.bot.send_message(
        chat_id,
        f"🚀 *Follow job started*\n🎯 Target: `{target_id}`\n"
        f"🪙 Budget: {budget_txt}\n"
        f"⚡ *Processing...*",
        parse_mode=ParseMode.MARKDOWN)

    async def show(text: str):
        nonlocal msg
        try:
            await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        except BadRequest:
            try:
                await msg.edit_text(text)
            except Exception:
                msg = await context.bot.send_message(chat_id, text)
        except Exception:
            msg = await context.bot.send_message(chat_id, text)

    C = {"done": 0, "skipped": 0, "failed": 0, "limit_reached": 0, "extra": 0}
    used_uids = []
    seen = set()

    accounts = await _run(
        lambda: list(accounts_coll.find({"status": "active"}).sort("capacity", -1).limit(30000)))

    async def process_one(acc):
        try:
            if is_cancelled(user_id):
                return None
            uid = acc["uid"]
            if uid in seen:
                return None
            seen.add(uid)

            await asyncio.sleep(FOLLOW_DELAY)

            jwt = acc.get("jwt_token") or ""
            if not jwt:
                jwt = await _run(get_jwt, uid, acc.get("password", ""))
                if jwt:
                    await _run(accounts_coll.update_one,
                               {"uid": uid}, {"$set": {"jwt_token": jwt}})
                else:
                    await _run(accounts_coll.update_one,
                               {"uid": uid}, {"$inc": {"fails": 1}})
                    return ("failed", uid, "JWT fail", None, None, None)

            cat, d = await _run(do_follow, target_id, jwt)
            
            server_cap = extract_capacity(d)
            
            cstat = d.get("creator_stats") or {}
            follower_after = int(cstat.get("follower_count", 0)) if cstat else 0
            follower_before = max(follower_after - 1, 0)
            
            if server_cap is not None:
                await _run(accounts_coll.update_one,
                           {"uid": uid}, {"$set": {"capacity": server_cap}})

            if cat == "success":
                info = d.get("info", {})
                await _run(accounts_coll.update_one,
                           {"uid": uid},
                           {"$set": {"last_used": datetime.utcnow(), "jwt_token": jwt},
                            "$inc": {"successes": 1}})
                return ("done", uid, info.get("nickname", "?"), server_cap or acc.get("capacity", MAX_CAP), 
                       follower_before, follower_after)
            
            elif cat == "already":
                await _run(accounts_coll.update_one,
                           {"uid": uid}, {"$inc": {"skips": 1}})
                return ("skipped", uid, None, None, None, None)
            
            elif cat == "limit":
                await _run(accounts_coll.update_one,
                           {"uid": uid}, {"$set": {"status": "limit_reached"}, "$inc": {"fails": 1}})
                await notify_admin_limit_reached(context, uid, target_id, d)
                return ("limit_reached", uid, None, None, None, None)
            
            elif cat == "not_found":
                await _run(accounts_coll.update_one,
                           {"uid": uid}, {"$inc": {"fails": 1}})
                return ("failed", uid, "not found", None, None, None)
            
            else:
                await _run(accounts_coll.update_one,
                           {"uid": uid}, {"$inc": {"fails": 1}})
                err = str(d.get("fail_info") or d.get("error") or "Unknown")[:40]
                return ("failed", uid, err, None, None, None)
                
        except Exception as e:
            logger.warning("process_one error uid=%s: %s", acc.get("uid"), e)
            await _run(accounts_coll.update_one,
                       {"uid": str(acc.get("uid", "?"))}, {"$inc": {"fails": 1}})
            return ("failed", str(acc.get("uid", "?")), "internal", None, None, None)

    def batcher(items, size):
        for i in range(0, len(items), size):
            yield items[i:i + size]

    for batch in batcher(accounts, BATCH_SIZE):
        if is_cancelled(user_id):
            break
        results = await asyncio.gather(*(process_one(a) for a in batch))

        lines = []
        for r in results:
            if not r:
                continue
            kind = r[0]
            if kind == "done":
                if C["done"] >= budget and not is_admin_user:
                    C["extra"] += 1
                    continue
                _, uid, nick, cap, before, after = r
                C["done"] += 1
                used_uids.append(uid)
                if not is_admin_user:
                    await _run(spend_coin, user_id)
                lines.append(f"✅ `{uid}` {E(nick)} — 👥 {before}→{after} (+{after - before}) ⚡{cap}")
            elif kind == "skipped":
                C["skipped"] += 1
                lines.append(f"⏭️ `{r[1]}` already followed")
            elif kind == "limit_reached":
                C["limit_reached"] += 1
                lines.append(f"🚨 `{r[1]}` limit reached - admin notified")
            else:
                C["failed"] += 1
                lines.append(f"❌ `{r[1]}` {r[2]}")

        bar = "▰" * min(C["done"], 12) + "▱" * max(12 - min(C["done"], 12), 0)
        await show(f"{THIN}\n🎯 `{target_id}`  {bar} {C['done']}/{budget_txt}\n{THIN}\n"
                   + "\n".join(lines[-6:]) + f"\n{THIN}\n"
                   f"✅{C['done']} ⏭️{C['skipped']} ❌{C['failed']} 🚨{C['limit_reached']}")

        if not is_admin_user and C["done"] >= budget:
            break

    if is_admin_user:
        final = (f"✅ *Admin job complete!*\n🎯 `{target_id}`\n"
                 f"✅ Followed: {C['done']} | ⏭️ Skipped: {C['skipped']}\n"
                 f"❌ Failed: {C['failed']} | 🚨 Limit reached: {C['limit_reached']}\n"
                 f"♾️ Unlimited — no coins deducted.")
    elif is_cancelled(user_id):
        final = (f"⛔ *Task cancelled.*\nPartial: ✅{C['done']} ⏭️{C['skipped']} ❌{C['failed']}\n"
                 f"🪙 {C['done']} coin{'s' if C['done'] != 1 else ''} deducted.")
    elif C["done"] >= budget:
        bonus = (f"\n🎁 Bonus: {C['extra']} extra free follower{'s' if C['extra'] != 1 else ''}"
                 if C["extra"] else "")
        final = (f"🎉 *Job Complete!*\nAll {budget} followers delivered to `{target_id}`.\n"
                 f"✅{C['done']} ⏭️{C['skipped']} ❌{C['failed']} 🚨{C['limit_reached']}\n"
                 f"🪙 {budget} coins deducted.{bonus}")
    elif C["done"] == 0:
        final = (f"😔 *STOCK OVER*\nNo successful follows for `{target_id}`.\n"
                 f"⏭️ {C['skipped']} | ❌ {C['failed']} | 🚨 {C['limit_reached']}\n\n"
                 f"✅ No coins were deducted.")
    else:
        final = (f"⚠️ *Partial run* — {C['done']}/{budget_txt} done, stock exhausted.\n"
                 f"✅{C['done']} ⏭️{C['skipped']} ❌{C['failed']} 🚨{C['limit_reached']}\n"
                 f"🪙 {C['done']} coin{'s' if C['done'] != 1 else ''} deducted.")

    if C['limit_reached'] > 0:
        final += f"\n\n⚠️ *{C['limit_reached']} accounts reached limit.* Admins notified."

    final += f"\n{THIN}\n✨ *TITAN EDITION*"
    await show(final)

    await _run(log_coll.insert_one, {
        "user": user_id, "target": target_id, "done": C["done"],
        "skipped": C["skipped"], "failed": C["failed"], "limit_reached": C["limit_reached"],
        "accounts_used": used_uids, "at": datetime.utcnow(),
    })

# ══════════════════════════════════════════════════════════════
# 🧭 MENU BUILDERS
# ══════════════════════════════════════════════════════════════
def main_kb(user_id: int) -> InlineKeyboardMarkup:
    kb = [
        [B("👤 My Profile", "menu_profile"), B("🪙 Coins", "menu_coins"), B("👥 Refer", "menu_refer")],
        [B("📖 Help", "menu_help"), B("🚀 Follow", "menu_follow")],
        [B("🏆 Leaderboard", "menu_leaderboard")],
    ]
    if is_admin(user_id):
        kb.append([B("⚙️ Admin Panel", "menu_admin")])
    return InlineKeyboardMarkup(kb)

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [B("📦 Stock", "menu_stock"), B("📜 Logs", "menu_logs")],
        [B("⚠️ Error Logs", "menu_error_logs")],
        [B("➕ Add Coins", "action_addcoin"), B("➖ Remove Coins", "action_removecoin")],
        [B("🚫 Ban User", "action_ban"), B("✅ Unban User", "action_unban")],
        [B("👑 Add Admin", "action_add_admin"), B("👑 Remove Admin", "action_remove_admin")],
        [B("📢 Broadcast", "action_broadcast"), B("📤 Upload Accounts", "action_upload")],
        [B("📢 Channels", "menu_channels"), B("⚙️ Settings", "menu_settings")],
        [B("⬅️ Back", "menu_main")],
    ])

def main_menu_text(user) -> str:
    cfg = get_settings()
    return (f"{box('🏠 MAIN MENU')}\n"
            f"👋 Welcome back!\n\n"
            f"🪙 Daily coins: *{user['daily_coins']}* / {cfg['daily_coins']}\n"
            f"🎁 Referral coins: *{user['referral_coins']}*\n"
            f"📊 Available: *{available_coins(user)}*\n\n"
            f"👇 Choose an option:")

# ══════════════════════════════════════════════════════════════
# 🏆 LEADERBOARD
# ══════════════════════════════════════════════════════════════
def _top_users(start: datetime, limit: int = 5):
    follows = log_coll.aggregate([
        {"$match": {"at": {"$gte": start}}},
        {"$group": {"_id": "$user", "follows": {"$sum": "$done"}}},
    ])
    refs = referral_logs.aggregate([
        {"$match": {"at": {"$gte": start}}},
        {"$group": {"_id": "$ref_by", "refs": {"$sum": 1}}},
    ])
    fm = {d["_id"]: d["follows"] for d in follows}
    rm = {d["_id"]: d["refs"] for d in refs}
    rows = []
    for i in set(fm) | set(rm):
        f = int(fm.get(i, 0))
        r = int(rm.get(i, 0))
        rows.append({"uid": i, "follows": f, "refs": r, "score": f + r})
    rows.sort(key=lambda x: (-x["score"], -x["follows"]))
    return rows[:limit]

def _display_name(uid: int) -> str:
    u = users_coll.find_one({"_id": uid}, {"username": 1, "first_name": 1})
    if not u:
        return f"`{uid}`"
    if u.get("username"):
        return f"`@{u['username']}`"
    return f"`{u.get('first_name') or uid}`"

def leaderboard_text() -> str:
    now = datetime.utcnow()
    day_start  = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    lines = [f"{box('🏆 LEADERBOARD')}",
             f"⚡ Score = ✅ successful follows + 👥 successful referrals"]
    today = _top_users(day_start)
    lines.append(f"\n📅 *TODAY* — top 5")
    if today:
        for i, r in enumerate(today):
            lines.append(f"{medals[i]} {_display_name(r['uid'])}\n"
                         f"     ✅ {r['follows']} follows · 👥 {r['refs']} refs")
    else:
        lines.append("No activity yet — be the first! 🚀")
    week = _top_users(week_start)
    lines.append(f"\n📆 *LAST 7 DAYS* — top 5")
    if week:
        for i, r in enumerate(week):
            lines.append(f"{medals[i]} {_display_name(r['uid'])}\n"
                         f"     ✅ {r['follows']} follows · 👥 {r['refs']} refs")
    else:
        lines.append("No activity in the last 7 days.")
    lines.append(f"\n{THIN}\n✨ *TITAN EDITION*")
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════════
# 🏠 USER COMMANDS — ALL DEFINED
# ══════════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    uname = update.effective_user.username or ""
    fname = update.effective_user.first_name or ""
    args = context.args
    cfg = get_settings()

    ref_by = None
    if args and args[0].startswith("ref_"):
        try:
            ref_by = int(args[0].split("_")[1])
        except (ValueError, IndexError):
            ref_by = None

    user = await _run(get_user_sync, uid, uname, fname)

    if ref_by and ref_by != uid:
        ref_user = await _run(users_coll.find_one, {"_id": ref_by})
        if ref_user and uid not in ref_user.get("referred_users", []):
            await _run(users_coll.update_one,
                {"_id": ref_by},
                {"$inc": {"referral_coins": cfg["ref_coins"], "total_earned": cfg["ref_coins"]},
                 "$push": {"referred_users": uid}})
            await _run(users_coll.update_one, {"_id": uid}, {"$set": {"referred_by": ref_by}})
            try:
                await _run(referral_logs.insert_one, {
                    "ref_by": ref_by, "ref_user": uid,
                    "username": uname, "first_name": fname,
                    "at": datetime.utcnow(),
                })
            except Exception:
                pass
            try:
                updated = await _run(users_coll.find_one, {"_id": ref_by})
                if uname:
                    display = f"`@{uname}`"
                elif fname:
                    display = f"`{fname}`"
                else:
                    display = f"`{uid}`"
                kb = InlineKeyboardMarkup([[B("🏆 Leaderboard", "menu_leaderboard")]])
                await context.bot.send_message(
                    ref_by,
                    f"🎉 *New Referral!*\n\n👤 {display} joined via your link!\n🪙 +{cfg['ref_coins']} coins!",
                    parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
            except Exception:
                pass

    user = await _run(get_user_sync, uid, uname, fname)
    txt = (f"{box('🔥 FF CARFT LAND')}\n"
           f"👋 Hey *{fname}*!\n"
           f"🪙 Coins: *{available_coins(user)}* ({cfg['daily_coins']} daily + {cfg['ref_coins']} per referral)\n\n"
           f"⚡ *How it works:*\n"
           f"1️⃣ `/follow <UID>` — 1 coin per successful follower\n"
           f"2️⃣ Daily {cfg['daily_coins']} coins reset at midnight if unused\n"
           f"3️⃣ Referral coins never expire\n"
           f"4️⃣ Compete on the 🏆 leaderboard — top 5 daily & weekly!\n\n"
           f"💰 Share & earn: `https://t.me/{BOT_USERNAME}?start=ref_{uid}`")

    missing = await not_joined_channels(context.bot, uid)
    if missing and not is_admin(uid):
        await update.message.reply_text(txt + "\n\n" + join_prompt_text(missing),
                                        parse_mode=ParseMode.MARKDOWN,
                                        reply_markup=join_kb(missing))
        return
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=main_kb(uid))

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await join_gate(update, context):
        return
    txt = (f"{box('📖 HELP')}\n"
           f"👤 *User*\n"
           f"`/start` — home menu\n`/follow <UID>` — follow target (1 coin each)\n"
           f"`/profile` — my stats\n`/refer` — referral link\n"
           f"`/leaderboard` — 🏆 top 5 today & last 7 days\n"
           f"`/cancel` — stop job\n\n"
           f"🛡️ *Admin*\n"
           f"`/admin` — panel\n`/addcoin <id> <n>` · `/removecoin <id> <n>`\n"
           f"`/ban <id>` · `/unban <id>` · `/stock` · `/logs`\n"
           f"`/broadcast <text>` · `/upload` (then send file)\n\n"
           f"⚡ Admins follow unlimited & free.\n"
           f"✨ *TITAN EDITION*")
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await join_gate(update, context):
        return
    user = await _run(get_user_sync, update.effective_user.id)
    uid = update.effective_user.id
    cfg = get_settings()
    txt = (f"{box('👤 MY PROFILE')}\n"
           f"🆔 ID: `{uid}`\n"
           f"🪙 Daily: *{user['daily_coins']}* / {cfg['daily_coins']}\n"
           f"🎁 Referral: *{user['referral_coins']}*\n"
           f"📊 Available: *{available_coins(user)}*\n"
           f"💸 Spent: *{user['total_spent']}*\n"
           f"👥 Referrals: *{len(user.get('referred_users', []))}*\n"
           f"🔄 Reset: `{user['last_daily_reset']}`")
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

async def cmd_refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await join_gate(update, context):
        return
    uid = update.effective_user.id
    cfg = get_settings()
    txt = (f"{box('👥 REFER & EARN')}\n"
           f"Earn *{cfg['ref_coins']} coins* per friend who joins:\n\n"
           f"🔗 `https://t.me/{BOT_USERNAME}?start=ref_{uid}`\n\n"
           f"Friends get {cfg['daily_coins']} free daily coins too! 🎁\n"
           f"🏆 Every referral also boosts your leaderboard score!")
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await join_gate(update, context):
        return
    txt = await _run(leaderboard_text)
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=back_kb())

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cancel_flags[uid] = True
    context.user_data.pop("awaiting", None)
    context.user_data.pop("broadcast_waiting", None)
    context.user_data.pop("upload_waiting", None)
    if uid in active_tasks and not active_tasks[uid].done():
        await update.message.reply_text("⛔ *Cancelling job…*", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("ℹ️ Nothing to cancel.", parse_mode=ParseMode.MARKDOWN)

async def cmd_follow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = await _run(get_user_sync, uid)

    if user.get("banned"):
        await update.message.reply_text("🚫 *Banned.*", parse_mode=ParseMode.MARKDOWN)
        return
    if not await join_gate(update, context):
        return
    if uid in active_tasks and not active_tasks[uid].done():
        await update.message.reply_text("⏳ *Job running!* Use /cancel.", parse_mode=ParseMode.MARKDOWN)
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("❌ `/follow <UID>`", parse_mode=ParseMode.MARKDOWN)
        return
    target_id = int(args[0])

    admin = is_admin(uid)
    budget = 10**9 if admin else available_coins(user)
    if not admin and budget <= 0:
        kb = InlineKeyboardMarkup([[B("👥 Refer & Earn", "menu_refer")]])
        await update.message.reply_text("🪙 *No coins!* Use referral.", parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        return

    active = accounts_coll.count_documents({"status": "active"})
    if active == 0:
        await update.message.reply_text("📦 *No stock.* Contact admin.", parse_mode=ParseMode.MARKDOWN)
        return

    cancel_flags[uid] = False
    task = asyncio.create_task(run_follow_job(update, context, target_id, budget, admin))
    active_tasks[uid] = task

# ══════════════════════════════════════════════════════════════
# 🛡️ ADMIN COMMANDS
# ══════════════════════════════════════════════════════════════
def require_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text("🚫 *Admin only!*", parse_mode=ParseMode.MARKDOWN)
            return
        return await func(update, context)
    return wrapper

def stock_text() -> str:
    total  = accounts_coll.count_documents({})
    active = accounts_coll.count_documents({"status": "active"})
    limit_reached = accounts_coll.count_documents({"status": "limit_reached"})
    users  = users_coll.count_documents({})
    agg = accounts_coll.aggregate([
        {"$match": {"status": "active"}},
        {"$group": {"_id": None, "cap": {"$sum": "$capacity"}}}])
    total_cap = next((a["cap"] for a in agg), 0)
    txt = (f"{box('📦 STOCK')}\n"
           f"📦 Total: *{total}* | ✅ Active: *{active}* | 🚨 Limit reached: *{limit_reached}*\n"
           f"👥 Users: *{users}* | ⚡ Total capacity: *{total_cap}*\n"
           f"{THIN}\n")
    rows = []
    for a in accounts_coll.find({}).sort("capacity", -1).limit(50):
        icon = "🚨" if a.get("status") == "limit_reached" else "✅"
        rows.append(f"{icon} `{a['uid']}`\n"
                    f"    ⚡ {a.get('capacity', 0)} | ✅ {a.get('successes', 0)} | ❌ {a.get('fails', 0)}")
    txt += "\n".join(rows) if rows else "No accounts."
    if total > 50:
        txt += f"\n{THIN}\n⚠️ Showing 50 of {total}."
    return txt

def logs_text() -> str:
    logs = log_coll.find().sort("at", -1).limit(10)
    lines = [f"👤 `{l['user']}` → 🎯 `{l['target']}`\n   ✅{l['done']} ⏭️{l['skipped']} ❌{l['failed']} 🚨{l.get('limit_reached', 0)} @ {l['at'].strftime('%m-%d %H:%M')}"
             for l in logs]
    return f"{box('📜 RECENT JOBS')}\n" + ("\n".join(lines) if lines else "No jobs.")

def error_logs_text() -> str:
    errors = error_logs_coll.find().sort("at", -1).limit(20)
    lines = []
    for e in errors:
        status = "✅" if e.get("resolved", False) else "🚨"
        lines.append(f"{status} `{e['uid']}` — {e['error_type']}\n"
                     f"   📝 {e['error_detail'][:50]}… @ {e['at'].strftime('%m-%d %H:%M')}")
    return f"{box('⚠️ ERROR LOGS')}\n" + ("\n".join(lines) if lines else "No errors.")

async def do_text_broadcast(context: ContextTypes.DEFAULT_TYPE, text: str) -> int:
    sent = 0
    user_ids = await _run(lambda: [u["_id"] for u in users_coll.find({}, {"_id": 1})])
    for uid_ in user_ids:
        try:
            await context.bot.send_message(uid_, f"📢 *Broadcast*\n\n{text}",
                                           parse_mode=ParseMode.MARKDOWN)
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    return sent

async def broadcast_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    sent = 0
    user_ids = await _run(lambda: [u["_id"] for u in users_coll.find({}, {"_id": 1})])
    for uid_ in user_ids:
        try:
            await update.message.copy(uid_)
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    return sent

def store_accounts_sync(content: str):
    accounts = parse_accounts(content)
    new = dup = err = 0
    for acc in accounts:
        try:
            accounts_coll.insert_one({
                "uid": acc["uid"],
                "password": acc.get("password", ""),
                "jwt_token": acc.get("jwt_token", ""),
                "capacity": acc.get("capacity", MAX_CAP),
                "status": "active",
                "successes": 0, "skips": 0, "fails": 0,
                "last_used": None,
            })
            new += 1
        except DuplicateKeyError:
            dup += 1
        except Exception as e:
            err += 1
            logger.warning("account insert error: %s", e)
    return new, dup, err

async def store_accounts(content: str):
    return await _run(store_accounts_sync, content)

@require_admin
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"{box('⚙️ ADMIN PANEL')}\nChoose:",
                                    reply_markup=admin_kb())

@require_admin
async def cmd_addcoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    a = context.args
    if len(a) < 2 or not a[0].isdigit() or not a[1].isdigit():
        await update.message.reply_text("❌ `/addcoin <id> <amount>`", parse_mode=ParseMode.MARKDOWN)
        return
    tid, amt = int(a[0]), int(a[1])
    await _run(get_user_sync, tid)
    await _run(users_coll.update_one, {"_id": tid}, {"$inc": {"referral_coins": amt, "total_earned": amt}})
    await update.message.reply_text(f"✅ Added *{amt}* coins to `{tid}`", parse_mode=ParseMode.MARKDOWN)

@require_admin
async def cmd_removecoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    a = context.args
    if len(a) < 2 or not a[0].isdigit() or not a[1].isdigit():
        await update.message.reply_text("❌ `/removecoin <id> <amount>`", parse_mode=ParseMode.MARKDOWN)
        return
    tid, amt = int(a[0]), int(a[1])
    await _run(get_user_sync, tid)
    await _run(users_coll.update_one, {"_id": tid}, {"$inc": {"referral_coins": -amt}})
    await update.message.reply_text(f"➖ Removed *{amt}* coins from `{tid}`", parse_mode=ParseMode.MARKDOWN)

@require_admin
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ `/ban <id>`", parse_mode=ParseMode.MARKDOWN)
        return
    tid = int(context.args[0])
    await _run(get_user_sync, tid)
    await _run(users_coll.update_one, {"_id": tid}, {"$set": {"banned": True}})
    await update.message.reply_text(f"🚫 Banned `{tid}`", parse_mode=ParseMode.MARKDOWN)

@require_admin
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ `/unban <id>`", parse_mode=ParseMode.MARKDOWN)
        return
    tid = int(context.args[0])
    await _run(get_user_sync, tid)
    await _run(users_coll.update_one, {"_id": tid}, {"$set": {"banned": False}})
    await update.message.reply_text(f"✅ Unbanned `{tid}`", parse_mode=ParseMode.MARKDOWN)

@require_admin
async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await _run(stock_text)
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

@require_admin
async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await _run(logs_text)
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

@require_admin
async def cmd_error_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = await _run(error_logs_text)
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

@require_admin
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        sent = await do_text_broadcast(context, " ".join(context.args))
        await update.message.reply_text(f"📢 Sent to *{sent}* users.", parse_mode=ParseMode.MARKDOWN)
        return
    context.user_data["broadcast_waiting"] = True
    await update.message.reply_text(
        "📢 *Broadcast armed!*\nSend message/file.\n`/cancel` to abort.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

@require_admin
async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["upload_waiting"] = True
    await update.message.reply_text(
        "📤 *Upload mode ready!*\nSend accounts file or paste text.\n`/cancel` to abort.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())

# ══════════════════════════════════════════════════════════════
# 🖱️ CALLBACKS
# ══════════════════════════════════════════════════════════════
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    try:
        await q.answer()
    except BadRequest:
        pass
    data, uid = q.data, q.from_user.id
    ud = context.user_data
    user = await _run(get_user_sync, uid)

    # Admin Error Actions
    if data.startswith("admin_delete_acc_"):
        if not is_admin(uid):
            await q.edit_message_text("🚫 Admin only!", parse_mode=ParseMode.MARKDOWN)
            return
        acc_uid = data.replace("admin_delete_acc_", "")
        result = await _run(accounts_coll.delete_one, {"uid": acc_uid})
        await _run(error_logs_coll.update_one, {"uid": acc_uid}, {"$set": {"resolved": True}})
        if result.deleted_count > 0:
            await q.edit_message_text(f"🗑️ *Deleted*\n`{acc_uid}` removed.", parse_mode=ParseMode.MARKDOWN)
        else:
            await q.edit_message_text(f"❌ `{acc_uid}` not found.", parse_mode=ParseMode.MARKDOWN)
        return

    elif data.startswith("admin_restock_acc_"):
        if not is_admin(uid):
            await q.edit_message_text("🚫 Admin only!", parse_mode=ParseMode.MARKDOWN)
            return
        acc_uid = data.replace("admin_restock_acc_", "")
        result = await _run(accounts_coll.update_one,
                           {"uid": acc_uid},
                           {"$set": {"capacity": MAX_CAP, "fails": 0, "status": "active"}})
        await _run(error_logs_coll.update_one, {"uid": acc_uid}, {"$set": {"resolved": True}})
        if result.modified_count > 0:
            await q.edit_message_text(f"🔄 *Restocked*\n`{acc_uid}` reset to {MAX_CAP}.",
                parse_mode=ParseMode.MARKDOWN)
        else:
            await q.edit_message_text(f"❌ `{acc_uid}` not found.", parse_mode=ParseMode.MARKDOWN)
        return

    elif data.startswith("admin_view_error_"):
        if not is_admin(uid):
            await q.edit_message_text("🚫 Admin only!", parse_mode=ParseMode.MARKDOWN)
            return
        acc_uid = data.replace("admin_view_error_", "")
        error = await _run(error_logs_coll.find_one, {"uid": acc_uid}, sort=[("at", -1)])
        if error:
            response_data = error.get("response_data", {})
            full_response = json.dumps(response_data, indent=2)[:3000]
            text = (f"📋 *Full Response*\n🔑 `{acc_uid}`\n{THIN}\n```json\n{full_response}\n```")
            await q.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
        else:
            await q.edit_message_text(f"❌ No error for `{acc_uid}`", parse_mode=ParseMode.MARKDOWN)
        return

    # Regular Menu Callbacks
    if data != "check_joined" and not is_admin(uid):
        missing = await not_joined_channels(context.bot, uid)
        if missing:
            await safe_edit(q, join_prompt_text(missing), parse_mode=ParseMode.MARKDOWN,
                            reply_markup=join_kb(missing))
            return

    if data == "menu_main":
        await safe_edit(q, main_menu_text(user), parse_mode=ParseMode.MARKDOWN,
                        reply_markup=main_kb(uid))
    elif data == "menu_profile":
        cfg = get_settings()
        await safe_edit(q,
            f"{box('👤 PROFILE')}\n🪙 Daily: *{user['daily_coins']}*/{cfg['daily_coins']}\n"
            f"🎁 Referral: *{user['referral_coins']}*\n📊 Available: *{available_coins(user)}*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())
    elif data == "menu_coins":
        cfg = get_settings()
        await safe_edit(q,
            f"{box('🪙 COINS')}\n🪙 Daily: *{user['daily_coins']}*/{cfg['daily_coins']}\n"
            f"🎁 Referral: *{user['referral_coins']}*\n📊 Available: *{available_coins(user)}*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())
    elif data == "menu_refer":
        cfg = get_settings()
        await safe_edit(q,
            f"{box('👥 REFER')}\n🔗 `https://t.me/{BOT_USERNAME}?start=ref_{uid}`\n\n🎁 +{cfg['ref_coins']} 🪙/friend!",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())
    elif data == "menu_help":
        await safe_edit(q,
            f"{box('📖 HELP')}\n`/follow <UID>` — 1 coin/follower\n"
            f"`/profile` · `/refer` · `/leaderboard` · `/cancel`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())
    elif data == "menu_follow":
        await safe_edit(q,
            f"{box('🚀 FOLLOW')}\n`/follow <TARGET_UID>`\nExample: `/follow 7733108466`",
            parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())
    elif data == "menu_leaderboard":
        txt = await _run(leaderboard_text)
        await safe_edit(q, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())
    elif data == "check_joined":
        missing = await not_joined_channels(context.bot, uid)
        if missing:
            await safe_edit(q, join_prompt_text(missing), parse_mode=ParseMode.MARKDOWN,
                            reply_markup=join_kb(missing))
        else:
            await safe_edit(q, main_menu_text(user), parse_mode=ParseMode.MARKDOWN,
                            reply_markup=main_kb(uid))
    elif data == "menu_admin" and is_admin(uid):
        await safe_edit(q, f"{box('⚙️ ADMIN')}\nChoose:", reply_markup=admin_kb())
    elif data == "menu_error_logs" and is_admin(uid):
        txt = await _run(error_logs_text)
        await safe_edit(q, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())
    elif data == "menu_settings" and is_admin(uid):
        cfg = get_settings()
        await safe_edit(q,
            f"{box('⚙️ SETTINGS')}\n🪙 Daily: *{cfg['daily_coins']}*\n🎁 Refer: *{cfg['ref_coins']}*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [B("🪙 Set Daily", "action_set_daily")],
                [B("🎁 Set Refer", "action_set_ref")],
                [B("⬅️ Back", "menu_admin")],
            ]))
    elif data == "menu_stock" and is_admin(uid):
        txt = await _run(stock_text)
        await safe_edit(q, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())
    elif data == "menu_logs" and is_admin(uid):
        txt = await _run(logs_text)
        await safe_edit(q, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=back_kb())
    elif data == "menu_channels" and is_admin(uid):
        txt = await _run(channels_text)
        await safe_edit(q, txt, parse_mode=ParseMode.MARKDOWN, reply_markup=channels_kb())
    elif data.startswith("remove_channel_") and is_admin(uid):
        oid = data[len("remove_channel_"):]
        try:
            ch = await _run(channels_coll.find_one_and_delete, {"_id": ObjectId(oid)})
        except Exception:
            ch = None
        if ch:
            out = f"🗑️ Removed: `{ch.get('title')}`\n\n{await _run(channels_text)}"
        else:
            out = f"❌ Not found.\n\n{await _run(channels_text)}"
        await safe_edit(q, out, parse_mode=ParseMode.MARKDOWN, reply_markup=channels_kb())
    elif data.startswith("action_") and is_admin(uid):
        if data in ("action_broadcast", "action_upload", "action_add_admin", "action_remove_admin"):
            ud.pop("awaiting", None)
            if data == "action_broadcast":
                ud["broadcast_waiting"] = True
                title, hint = "📢 Broadcast", "Send message/file."
            elif data == "action_upload":
                ud["upload_waiting"] = True
                title, hint = "📤 Upload", "Send accounts file/text."
            elif data == "action_add_admin":
                ud["awaiting"] = "action_add_admin"
                title, hint = "👑 Add Admin", "Send user ID: `123456789`"
            else:
                ud["awaiting"] = "action_remove_admin"
                title, hint = "👑 Remove Admin", "Send user ID: `123456789`"
            kb = InlineKeyboardMarkup([[B("⬅️ Back", "clear_state")]])
            await safe_edit(q, f"{box(title)}\n{hint}", parse_mode=ParseMode.MARKDOWN,
                            reply_markup=kb)
            return
        ud["awaiting"] = data
        cfg = get_settings()
        hints = {
            "action_addcoin": ("➕ Add Coins", "Send: `<id> <amount>`"),
            "action_removecoin": ("➖ Remove Coins", "Send: `<id> <amount>`"),
            "action_ban": ("🚫 Ban", "Send: `<id>`"),
            "action_unban": ("✅ Unban", "Send: `<id>`"),
            "action_set_daily": ("🪙 Set Daily", f"Send: `<amount>` Current: {cfg['daily_coins']}"),
            "action_set_ref": ("🎁 Set Refer", f"Send: `<amount>` Current: {cfg['ref_coins']}"),
            "action_add_channel": ("📢 Add Channel", "Send: `@channel` or link"),
        }
        title, hint = hints.get(data, ("⚙️ Admin", "Send input."))
        kb = InlineKeyboardMarkup([[B("⬅️ Back", "clear_state")]])
        await safe_edit(q, f"{box(title)}\n{hint}", parse_mode=ParseMode.MARKDOWN,
                        reply_markup=kb)
    elif data == "clear_state":
        ud.pop("awaiting", None)
        ud.pop("broadcast_waiting", None)
        ud.pop("upload_waiting", None)
        await safe_edit(q, main_menu_text(user), parse_mode=ParseMode.MARKDOWN,
                        reply_markup=main_kb(uid))

# ══════════════════════════════════════════════════════════════
# 📨 MESSAGE / MEDIA HANDLERS
# ══════════════════════════════════════════════════════════════
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.effective_user is None:
        return
    uid = update.effective_user.id
    ud = context.user_data
    text = update.message.text or ""

    if ud.get("broadcast_waiting"):
        if not is_admin(uid):
            return
        ud["broadcast_waiting"] = False
        sent = await do_text_broadcast(context, text)
        await update.message.reply_text(f"📢 Sent to *{sent}* users.", parse_mode=ParseMode.MARKDOWN)
        return

    if ud.get("upload_waiting"):
        if not is_admin(uid):
            return
        ud["upload_waiting"] = False
        new, dup, err = await store_accounts(text)
        # Start notification in background - don't wait
        notified = await notify_stock_update(context, new) if new > 0 else 0
        # Send immediate response to admin
        await update.message.reply_text(
            f"📦 *Upload complete*\n✅ New: {new} | ⏭️ Dup: {dup} | ❌ Err: {err}"
            + (f"\n📢 Notifying *{notified}* users in background..." if notified else ""),
            parse_mode=ParseMode.MARKDOWN)
        return

    awaiting = ud.get("awaiting")
    if awaiting and is_admin(uid):
        valid = ("action_addcoin", "action_removecoin", "action_ban",
                 "action_unban", "action_set_daily", "action_set_ref",
                 "action_add_channel", "action_add_admin", "action_remove_admin")
        if awaiting not in valid:
            ud.pop("awaiting", None)
            await update.message.reply_text("ℹ️ Stale action cleared.", reply_markup=admin_kb())
            return
        parts = text.strip().split()
        try:
            if awaiting == "action_addcoin":
                tid, amt = int(parts[0]), int(parts[1])
                await _run(get_user_sync, tid)
                await _run(users_coll.update_one, {"_id": tid}, {"$inc": {"referral_coins": amt, "total_earned": amt}})
                out = f"✅ Added *{amt}* coins to `{tid}`"
            elif awaiting == "action_removecoin":
                tid, amt = int(parts[0]), int(parts[1])
                await _run(get_user_sync, tid)
                await _run(users_coll.update_one, {"_id": tid}, {"$inc": {"referral_coins": -amt}})
                out = f"➖ Removed *{amt}* coins from `{tid}`"
            elif awaiting == "action_ban":
                tid = int(parts[0])
                await _run(get_user_sync, tid)
                await _run(users_coll.update_one, {"_id": tid}, {"$set": {"banned": True}})
                out = f"🚫 Banned `{tid}`"
            elif awaiting == "action_unban":
                tid = int(parts[0])
                await _run(get_user_sync, tid)
                await _run(users_coll.update_one, {"_id": tid}, {"$set": {"banned": False}})
                out = f"✅ Unbanned `{tid}`"
            elif awaiting == "action_add_admin":
                tid = int(parts[0])
                if add_admin(tid):
                    out = f"👑 Added `{tid}` as admin!"
                else:
                    out = f"❌ Failed to add `{tid}`."
            elif awaiting == "action_remove_admin":
                tid = int(parts[0])
                if remove_admin(tid):
                    out = f"👑 Removed `{tid}` from admins."
                else:
                    out = f"❌ `{tid}` was not an admin."
            elif awaiting == "action_set_daily":
                val = int(parts[0])
                if val < 0:
                    out = "❌ Can't be negative."
                else:
                    set_setting("daily_coins", val)
                    out = f"🪙 Daily coins set to *{val}*"
            elif awaiting == "action_set_ref":
                val = int(parts[0])
                if val < 0:
                    out = "❌ Can't be negative."
                else:
                    set_setting("ref_coins", val)
                    out = f"🎁 Referral coins set to *{val}*"
            else:
                doc, err, existed = await add_channel(context.bot, text)
                if err:
                    out = f"❌ {err}"
                elif existed:
                    out = f"ℹ️ Channel already added: `{doc.get('title')}`"
                else:
                    out = f"✅ *Channel added!*\n📢 `{doc.get('title')}`"
        except (ValueError, IndexError):
            out = "❌ *Invalid format.*"
        ud.pop("awaiting", None)
        await update.message.reply_text(out, parse_mode=ParseMode.MARKDOWN, reply_markup=admin_kb())
        return

    if is_admin(uid) and looks_like_accounts(text):
        new, dup, err = await store_accounts(text)
        notified = await notify_stock_update(context, new) if new > 0 else 0
        await update.message.reply_text(
            f"📦 *Uploaded*\n✅ New: {new} | ⏭️ Dup: {dup} | ❌ Err: {err}"
            + (f"\n📢 Notifying *{notified}* users in background..." if notified else ""),
            parse_mode=ParseMode.MARKDOWN)
        return

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.effective_user is None:
        return
    uid = update.effective_user.id
    ud = context.user_data

    if update.message.text:
        return

    if ud.get("broadcast_waiting"):
        if not is_admin(uid):
            return
        ud["broadcast_waiting"] = False
        sent = await broadcast_media(update, context)
        await update.message.reply_text(f"📢 Media sent to *{sent}* users.", parse_mode=ParseMode.MARKDOWN)
        return

    doc = update.message.document
    if doc and is_admin(uid):
        ud.pop("upload_waiting", None)
        ud.pop("awaiting", None)
        try:
            f = await doc.get_file()
            raw = await f.download_as_bytearray()
            content = raw.decode("utf-8", errors="ignore")
            new, dup, err = await store_accounts(content)
            notified = await notify_stock_update(context, new) if new > 0 else 0
            await update.message.reply_text(
                f"📦 File `{doc.file_name}`\n✅ New: {new} | ⏭️ Dup: {dup} | ❌ Err: {err}"
                + (f"\n📢 Notifying *{notified}* users in background..." if notified else ""),
                parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            await update.message.reply_text(f"❌ Upload error: {e}")
        return

# ══════════════════════════════════════════════════════════════
# ⚠️ ERROR HANDLER
# ══════════════════════════════════════════════════════════════
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    if isinstance(context.error, BadRequest):
        return
    logger.error("Exception: %s", context.error)

# ══════════════════════════════════════════════════════════════
# 🚀 MAIN
# ══════════════════════════════════════════════════════════════
async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "🏠 Home"),
        BotCommand("follow", "🚀 Follow"),
        BotCommand("profile", "👤 Profile"),
        BotCommand("refer", "👥 Refer"),
        BotCommand("leaderboard", "🏆 Leaderboard"),
        BotCommand("help", "📖 Help"),
        BotCommand("cancel", "⛔ Stop"),
        BotCommand("admin", "⚙️ Admin"),
        BotCommand("stock", "📦 Stock"),
        BotCommand("logs", "📜 Logs"),
        BotCommand("errorlogs", "⚠️ Errors"),
        BotCommand("broadcast", "📢 Broadcast"),
        BotCommand("upload", "📤 Upload"),
    ])

def main():
    try:
        _client.admin.command("ping")
        srv = _client.server_info()
        logger.info("✅ MongoDB CONNECTED — %s", srv.get("version", "?"))
        logger.info("📦 users=%d | accounts=%d | admins=%d",
                    users_coll.count_documents({}),
                    accounts_coll.count_documents({}),
                    admin_coll.count_documents({}))
    except Exception as e:
        logger.error("❌ MongoDB connection FAILED: %s", e)

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # All handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("refer", cmd_refer))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("follow", cmd_follow))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("addcoin", cmd_addcoin))
    app.add_handler(CommandHandler("addcoins", cmd_addcoin))
    app.add_handler(CommandHandler("removecoin", cmd_removecoin))
    app.add_handler(CommandHandler("removecoins", cmd_removecoin))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("stock", cmd_stock))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("errorlogs", cmd_error_logs))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.CHANNEL_POST & ~filters.UpdateType.EDITED_MESSAGE & ~filters.UpdateType.EDITED_CHANNEL_POST, handle_message))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.UpdateType.CHANNEL_POST & ~filters.UpdateType.EDITED_MESSAGE & ~filters.UpdateType.EDITED_CHANNEL_POST, handle_media))
    app.add_error_handler(error_handler)

    logger.info("🔥 ULTRA FAST BOT ONLINE (v6.2 - Non-blocking Notifications)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

# python3 -m pip uninstall protobuf google -y
# python3 -m pip install protobuf==7.35.1
# python3 -m pip install google==3.0.0
