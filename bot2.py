# bot.py - Complete Working Telegram Bot with Progress Bars, Coin System & Concurrency
import json
import requests
import os
import re
import time
import asyncio
import threading
import sys
import uuid
from datetime import datetime
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToDict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ================= CONFIG =================
BOT_TOKEN = "8680371930:AAFgIIrLBQi_YlEBdVIZCfL9k9AWdGf-Yqw"  # Replace with your bot token
ADMIN_IDS = [8888758201]  # Replace with your admin Telegram IDs

# Same encryption keys as CLI version
KEY = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
IV = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])

JWT_API = "https://ff-jwt-gen-api.lovable.app/api/public/token"
URL = "https://client.ind.freefiremobile.com/Follow"

# Protobuf module (keep follow_pb2.py next to this file)
try:
    import follow_pb2
except ImportError:
    follow_pb2 = None
    print("⚠️ follow_pb2.py not found! Follow requests will not work.")

# ================= JSON STORAGE (atomic, thread-safe) =================
# RLock is RE-ENTRANT: update_data() can safely call load_data() while holding it.
# A plain Lock here caused a deadlock on the first account upload -> bot froze.
DATA_FILE = "ff_bot_data.json"
data_lock = threading.RLock()

def load_data():
    """Load data from JSON file"""
    with data_lock:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {
            "users": {},
            "referrals": [],
            "accounts": [],
            "follow_history": [],
            "followed_targets": [],
            "admins": ADMIN_IDS.copy(),
            "channels": [],
            "settings": {
                "daily_coins": 5,
                "referral_coins": 5,
                "follow_delay": 2
            }
        }

def save_data(data):
    """Save data to JSON file"""
    with data_lock:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

def update_data(mutator):
    """Atomic read-modify-write under one lock (safe for concurrent requests)"""
    with data_lock:
        data = load_data()  # RLock allows this re-entry
        result = mutator(data)
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return result

# ================= IN-MEMORY ACCOUNT RESERVATION (concurrency) =================
_active_accounts = set()          # UIDs currently being used by another request
_active_lock = threading.Lock()

def reserve_accounts(accounts):
    """Pick only accounts not already in use by a concurrent request"""
    with _active_lock:
        reserved = []
        for acc in accounts:
            uid = str(acc[0])
            if uid not in _active_accounts:
                _active_accounts.add(uid)
                reserved.append(acc)
        return reserved

def release_accounts(accounts):
    with _active_lock:
        for acc in accounts:
            _active_accounts.discard(str(acc[0]))

# ================= COIN SYSTEM =================
def get_user(user_id):
    data = load_data()
    return data["users"].get(str(user_id))

def get_user_info(user_id):
    """Alias for get_user()"""
    return get_user(user_id)

def generate_referral_code(user_id):
    return f"ref_{user_id}"

def add_user(user_id, username, first_name, last_name, referred_by=None):
    data = load_data()
    user_id_str = str(user_id)

    if user_id_str in data["users"]:
        return False

    referral_code = generate_referral_code(user_id)
    joined_date = datetime.now().isoformat()

    data["users"][user_id_str] = {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "joined_date": joined_date,
        "referral_code": referral_code,
        "referred_by": referred_by,
        "total_follows": 0,
        "coins": 0,
        "daily_coins_granted": 0,
        "last_coin_date": None,
        "is_banned": False
    }

    # If referred by someone: record referral + give coins to referrer
    if referred_by:
        data["referrals"].append({
            "referrer_id": referred_by,
            "referred_id": user_id,
            "date": joined_date
        })
        bonus = data["settings"].get("referral_coins", 5)
        referrer = data["users"].get(str(referred_by))
        if referrer:
            referrer["coins"] = referrer.get("coins", 0) + bonus

    save_data(data)
    return True

def get_available_coins(user_id):
    """Return current coin balance (auto-grants daily coins on a new day). Admin = unlimited."""
    if user_id in load_data().get("admins", []):
        return 999999

    def mutate(data):
        user = data["users"].get(str(user_id))
        if not user:
            return 0
        daily = data["settings"].get("daily_coins", 5)
        today = datetime.now().date().isoformat()
        if user.get("last_coin_date") != today:
            user["coins"] = user.get("coins", 0) + daily
            user["daily_coins_granted"] = daily
            user["last_coin_date"] = today
        return user.get("coins", 0)

    return update_data(mutate)

def get_coins_info(user_id):
    """Returns (balance, daily_granted_today, daily_limit)"""
    def mutate(data):
        user = data["users"].get(str(user_id))
        if not user:
            return (0, 0, data["settings"].get("daily_coins", 5))
        if user_id in data.get("admins", []):
            return (999999, 999999, 999999)
        daily = data["settings"].get("daily_coins", 5)
        today = datetime.now().date().isoformat()
        if user.get("last_coin_date") != today:
            user["coins"] = user.get("coins", 0) + daily
            user["daily_coins_granted"] = daily
            user["last_coin_date"] = today
        return (user.get("coins", 0), user.get("daily_coins_granted", 0), daily)

    return update_data(mutate)

def spend_coins(user_id, amount=1):
    """Deduct coins. Returns True if spent. Admin is never charged."""
    def mutate(data):
        if user_id in data.get("admins", []):
            return True
        user = data["users"].get(str(user_id))
        if not user:
            return False
        if user.get("coins", 0) >= amount:
            user["coins"] = user["coins"] - amount
            return True
        return False
    return update_data(mutate)

# ================= DATABASE HELPERS =================
def get_setting(key, default=None):
    data = load_data()
    return data["settings"].get(key, default)

def set_setting(key, value):
    data = load_data()
    data["settings"][key] = value
    save_data(data)

def get_available_accounts():
    data = load_data()
    accounts = []
    for acc in data["accounts"]:
        if acc.get("is_active", True):
            accounts.append((acc["uid"], acc["password"], acc.get("jwt_token")))
    return accounts

def get_accounts_count():
    data = load_data()
    return len([acc for acc in data["accounts"] if acc.get("is_active", True)])

def get_total_users():
    data = load_data()
    return len(data["users"])

def get_today_follows():
    data = load_data()
    today = datetime.now().date().isoformat()
    count = 0
    for entry in data["follow_history"]:
        if entry["follow_date"][:10] == today and entry["status"] == "success":
            count += 1
    return count

def get_referral_count(user_id):
    data = load_data()
    return len([r for r in data["referrals"] if r["referrer_id"] == user_id])

def get_referred_users(user_id):
    data = load_data()
    users = []
    for ref in data["referrals"]:
        if ref["referrer_id"] == user_id:
            referred_id = ref["referred_id"]
            if str(referred_id) in data["users"]:
                user = data["users"][str(referred_id)]
                users.append((referred_id, user.get("first_name"), user.get("username"), ref["date"]))
    return users

def get_user_by_referral_code(ref_code):
    if not ref_code or not ref_code.startswith('ref_'):
        return None
    try:
        return int(ref_code.replace('ref_', ''))
    except:
        return None

def is_admin(user_id):
    data = load_data()
    return user_id in data.get("admins", [])

def add_admin(user_id):
    def mutate(data):
        if user_id not in data["admins"]:
            data["admins"].append(user_id)
            return True
        return False
    return update_data(mutate)

def remove_admin(user_id):
    def mutate(data):
        if user_id in data["admins"] and user_id not in ADMIN_IDS:
            data["admins"].remove(user_id)
            return True
        return False
    return update_data(mutate)

def get_channels():
    data = load_data()
    return data.get("channels", [])

def add_channel(channel):
    def mutate(data):
        if channel not in data["channels"]:
            data["channels"].append(channel)
            return True
        return False
    return update_data(mutate)

def remove_channel(channel):
    def mutate(data):
        if channel in data["channels"]:
            data["channels"].remove(channel)
            return True
        return False
    return update_data(mutate)

async def is_user_in_channel(user_id, bot, channel_id):
    """Check if user is a member of the channel/group.
    NOTE: PTB v20+ bot.get_chat_member() is ASYNC - must be awaited."""
    try:
        cid = str(channel_id)
        if not cid.lstrip('-').isdigit():
            cid = "@" + cid.lstrip('@')
        chat_member = await bot.get_chat_member(chat_id=cid, user_id=user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except Exception:
        return False

def build_join_keyboard(channels):
    """Build the 'join channel' keyboard for any stored format (username or numeric id)"""
    buttons = []
    for ch in channels:
        s = str(ch)
        if s.lstrip('-').isdigit():
            url = None
            try:
                n = abs(int(s))
                if s.startswith('-100'):
                    n = int(str(n)[3:])
                url = f"https://t.me/c/{n}"
            except Exception:
                url = None
            if url:
                buttons.append([InlineKeyboardButton("📢 Open Channel", url=url)])
        else:
            buttons.append([InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{s.lstrip('@')}")])
    buttons.append([InlineKeyboardButton("✅ I've Joined", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)

def parse_channel_identifier(text):
    """
    Accept channel/group in many formats:
      https://t.me/username | t.me/username | @username | username | -1001234567890
    Returns (identifier, kind) where kind is 'public', 'id' or 'private' (unsupported)
    """
    t = text.strip()
    if t.lstrip('-').isdigit():
        return t, 'id'
    if t.startswith('@'):
        return t[1:].strip(), 'public'
    for prefix in ('https://t.me/', 'http://t.me/', 't.me/'):
        if t.startswith(prefix):
            rest = t[len(prefix):].split('?')[0].strip('/')
            if rest.startswith('+'):
                return rest, 'private'
            return rest, 'public'
    return t, 'public'

def add_account(uid, password, jwt_token=None, added_by=None):
    def mutate(data):
        for acc in data["accounts"]:
            if acc["uid"] == uid:
                acc["password"] = password
                if jwt_token:
                    acc["jwt_token"] = jwt_token
                return True
        data["accounts"].append({
            "uid": uid,
            "password": password,
            "jwt_token": jwt_token,
            "is_active": True,
            "last_used": None,
            "total_follows_sent": 0,
            "added_by": added_by,
            "added_date": datetime.now().isoformat()
        })
        return True
    return update_data(mutate)

def mark_followed(account_uid, target_uid, user_id, status, response=""):
    def mutate(data):
        data["followed_targets"].append({
            "account_uid": account_uid,
            "target_uid": target_uid,
            "follow_date": datetime.now().isoformat()
        })
        data["follow_history"].append({
            "account_uid": account_uid,
            "target_uid": target_uid,
            "user_id": user_id,
            "follow_date": datetime.now().isoformat(),
            "status": status,
            "response": response
        })
        return True
    return update_data(mutate)

def is_target_followed_by_account(account_uid, target_uid):
    data = load_data()
    for entry in data["followed_targets"]:
        if entry["account_uid"] == str(account_uid) and entry["target_uid"] == str(target_uid):
            return True
    return False

def update_account_used(uid):
    def mutate(data):
        for acc in data["accounts"]:
            if acc["uid"] == str(uid):
                acc["last_used"] = datetime.now().isoformat()
                acc["total_follows_sent"] = acc.get("total_follows_sent", 0) + 1
                return True
        return False
    return update_data(mutate)

def record_success(user_id, uid, target_uid):
    """Shared bookkeeping after a successful follow: history + coin charge + counters"""
    mark_followed(str(uid), str(target_uid), user_id, "success")
    update_account_used(uid)
    if not is_admin(user_id):
        spend_coins(user_id, 1)
    def mutate(data):
        user = data["users"].get(str(user_id))
        if user:
            user["total_follows"] = user.get("total_follows", 0) + 1
        return True
    update_data(mutate)

def load_accounts_from_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    content = re.sub(r',\s*}', '}', content)
    content = re.sub(r',\s*]', ']', content)

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return extract_accounts_regex(content)

    accounts = []
    if isinstance(data, list):
        for item in data:
            acc = extract_account_data(item)
            if acc:
                accounts.append(acc)
    elif isinstance(data, dict):
        for key in ['accounts', 'users', 'data', 'list']:
            if key in data and isinstance(data[key], list):
                for item in data[key]:
                    acc = extract_account_data(item)
                    if acc:
                        accounts.append(acc)
                if accounts:
                    return accounts
        acc = extract_account_data(data)
        if acc:
            accounts.append(acc)

    if not accounts:
        accounts = extract_accounts_regex(content)

    return accounts

def extract_account_data(obj):
    if not isinstance(obj, dict):
        return None

    uid = None
    password = None
    jwt_token = None

    uid_keys = ['uid', 'UID', 'userId', 'user_id', 'userid', 'id', 'account_id']
    for key in uid_keys:
        if key in obj and obj[key]:
            uid = str(obj[key])
            break

    pwd_keys = ['password', 'pass', 'pwd', 'Password', 'PASSWORD']
    for key in pwd_keys:
        if key in obj and obj[key]:
            password = str(obj[key])
            break

    token_keys = ['jwt_token', 'jwt', 'token', 'JWT', 'access_token', 'accessToken']
    for key in token_keys:
        if key in obj and obj[key]:
            jwt_token = str(obj[key])
            break

    if uid:
        account = {'uid': uid}
        if password:
            account['password'] = password
        if jwt_token:
            account['jwt_token'] = jwt_token
        return account

    return None

def extract_accounts_regex(content):
    accounts = []
    pattern = r'["\']?uid["\']?\s*:\s*["\']?(\d+)["\']?.*?["\']?password["\']?\s*:\s*["\']([^"\']+)["\']'
    matches = re.findall(pattern, content, re.IGNORECASE | re.DOTALL)
    for uid, pwd in matches:
        accounts.append({'uid': uid, 'password': pwd})
    return accounts

# ================= CORE FOLLOW FUNCTIONS =================
def encrypt_payload(data: bytes) -> bytes:
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    return cipher.encrypt(pad(data, AES.block_size))

def get_jwt_token(uid, password):
    url = f"{JWT_API}?uid={uid}&password={password}"

    try:
        response = requests.get(url, timeout=15)

        if response.status_code == 200:
            data = response.json()

            if data.get("status") == "live" and data.get("token"):
                token = data.get("token")
                account_id = data.get("account_id", "N/A")
                region = data.get("region", "N/A")
                print(f"    ✓ Account ID: {account_id} | Region: {region}")
                return token
            elif data.get("token"):
                return data.get("token")
            elif data.get("jwt"):
                return data.get("jwt")
            elif data.get("data") and isinstance(data.get("data"), dict):
                if data["data"].get("token"):
                    return data["data"]["token"]
            else:
                print(f"    ✗ Invalid response format")
                return None
        else:
            print(f"    ✗ API error: {response.status_code}")
            return None

    except Exception as e:
        print(f"    ✗ Exception: {e}")
        return None

def send_follow(target_id, jwt):
    if follow_pb2 is None:
        print("    ✗ follow_pb2 not available")
        return False, "follow_pb2 missing"

    try:
        target_id_int = int(target_id)
    except:
        target_id_int = target_id

    req = follow_pb2.CSFollowReq()
    req.target_id = target_id_int
    encrypted_data = encrypt_payload(req.SerializeToString())

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

    try:
        response = requests.post(URL, headers=headers, data=encrypted_data, timeout=20)

        if response.status_code == 200:
            print(f"    ✓ Status: {response.status_code}")
            try:
                res = follow_pb2.CSFollowRes()
                res.ParseFromString(response.content)
                res_dict = MessageToDict(res, preserving_proto_field_name=True)
                follower_count = res_dict.get('creator_stats', {}).get('follower_count', 'N/A')
                print(f"    📊 Follower Count: {follower_count}")
                return True, follower_count
            except Exception as e:
                print(f"    ⚠ Response received but could not decode: {e}")
                return True, "Success"
        elif response.status_code == 401:
            print(f"    ✗ Status: {response.status_code} - Token Expired or Invalid")
            return False, "Token expired"
        else:
            print(f"    ✗ Status: {response.status_code}")
            return False, f"Status {response.status_code}"
    except Exception as e:
        print(f"    ✗ Error: {e}")
        return False, str(e)

# ================= PROGRESS BAR =================
def create_progress_bar(current, total, width=20):
    """Create a visual progress bar"""
    if total == 0:
        return "[" + "░" * width + "] 0%"

    filled = int((current / total) * width)
    bar = "█" * filled + "░" * (width - filled)
    percentage = int((current / total) * 100)
    return f"[{bar}] {percentage}%"

# ================= TELEGRAM BOT =================
class FollowBot:
    def __init__(self, token):
        self.token = token
        self._last_edit = {}  # progress edit throttle
        print("✅ Bot initialized")

    async def update_progress(self, context, update_msg, target_id, current, total, stats, uid, final=False):
        # Throttle edits (Telegram rate-limits editMessageText)
        now = time.monotonic()
        last = self._last_edit.get(update_msg.message_id, 0)
        if not final and now - last < 0.4:
            return
        self._last_edit[update_msg.message_id] = now

        # Follower counts captured from follow responses
        before = stats.get('first_follower_count')
        after = stats.get('last_follower_count')
        if before is not None:
            before_text = str(before - 1)   # count before our first follow
            after_text = str(after) if after is not None else "N/A"
        else:
            before_text = "N/A"
            after_text = "N/A"

        bar = create_progress_bar(current, total)
        if final:
            text = (f"🔄 **Processing Complete!**\n\n"
                    f"`{bar}`\n"
                    f"📊 Progress: {current}/{total}\n"
                    f"✅ Success: {stats['success']} | ❌ Failed: {stats['failed']}\n"
                    f"👥 Before Followers: {before_text}\n"
                    f"👥 After Followers: {after_text}")
        else:
            text = (f"🔄 **Processing follows for UID:** `{target_id}`\n\n"
                    f"`{bar}`\n"
                    f"📊 Progress: {current}/{total}\n"
                    f"✅ Success: {stats['success']} | ❌ Failed: {stats['failed']}\n"
                    f"👥 Before Followers: {before_text}\n"
                    f"👥 After Followers: {after_text}\n"
                    f"⏳ Current: `{uid}`")
        try:
            await context.bot.edit_message_text(
                chat_id=update_msg.chat_id,
                message_id=update_msg.message_id,
                text=text,
                parse_mode='Markdown'
            )
        except Exception:
            pass

    async def process_follows(self, target_id, accounts, user_id, update_msg=None, context=None):
        """
        Process follows with live progress.
        Each call is fully isolated (own stats/results) -> safe for concurrent requests.
        Blocking network calls run via asyncio.to_thread so the bot stays responsive.
        """
        stats = {
            "total": len(accounts),
            "success": 0,
            "failed": 0,
            "jwt_failed": 0,
            "used_existing_tokens": 0,
            "expired_tokens": 0,
            "first_follower_count": None,   # after-count of first successful follow
            "last_follower_count": None     # after-count of last successful follow
        }

        def track_count(info):
            """Store follower count from a follow response"""
            try:
                c = int(info)
            except (TypeError, ValueError):
                return
            if stats["first_follower_count"] is None:
                stats["first_follower_count"] = c
            stats["last_follower_count"] = c

        results = []
        total = len(accounts)

        try:
            delay = float(get_setting('follow_delay', 2) or 2)
        except:
            delay = 2

        for i, acc in enumerate(accounts, 1):
            uid = str(acc.get("uid", "Unknown"))
            print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"  [{i}/{total}] Processing UID: {uid}")
            print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

            # Update progress bar
            if update_msg and context:
                await self.update_progress(context, update_msg, target_id, i - 1, total, stats, uid)

            jwt = acc.get('jwt_token')

            if jwt:
                print("  ✓ Using existing JWT token")
                stats["used_existing_tokens"] += 1

                print(f"  → Sending follow request to {target_id}...")
                success, info = await asyncio.to_thread(send_follow, target_id, jwt)

                if success:
                    stats["success"] += 1
                    track_count(info)
                    results.append({"uid": uid, "status": "success", "account_uid": uid})
                    record_success(user_id, uid, target_id)
                else:
                    stats["failed"] += 1
                    password = acc.get("password", "")
                    if password:
                        print("  → Token may be expired. Trying to get new JWT...")
                        new_jwt = await asyncio.to_thread(get_jwt_token, uid, password)
                        if new_jwt:
                            print("  ✓ New JWT obtained, retrying follow...")
                            retry_success, retry_info = await asyncio.to_thread(send_follow, target_id, new_jwt)
                            if retry_success:
                                stats["success"] += 1
                                stats["failed"] -= 1
                                stats["expired_tokens"] += 1
                                track_count(retry_info)
                                results.append({"uid": uid, "status": "success", "account_uid": uid})
                                record_success(user_id, uid, target_id)
                            else:
                                stats["expired_tokens"] += 1
                                results.append({"uid": uid, "status": "failed", "account_uid": uid})
                                mark_followed(uid, str(target_id), user_id, "failed")
                        else:
                            print("  ✗ Failed to get new JWT")
                            stats["jwt_failed"] += 1
                            results.append({"uid": uid, "status": "failed", "account_uid": uid})
                            mark_followed(uid, str(target_id), user_id, "failed")
                    else:
                        results.append({"uid": uid, "status": "failed", "account_uid": uid})
                        mark_followed(uid, str(target_id), user_id, "failed")
            else:
                password = acc.get("password", "")

                if not password:
                    print(f"  ✗ No password or JWT token found for UID: {uid}")
                    stats["failed"] += 1
                    stats["jwt_failed"] += 1
                    results.append({"uid": uid, "status": "failed", "account_uid": uid})
                    continue

                print("  → Getting JWT token from API...")
                jwt = await asyncio.to_thread(get_jwt_token, uid, password)

                if not jwt:
                    print(f"  ✗ Failed to get JWT for UID: {uid}")
                    stats["jwt_failed"] += 1
                    stats["failed"] += 1
                    results.append({"uid": uid, "status": "failed", "account_uid": uid})
                    continue

                print("  ✓ JWT obtained successfully")
                print(f"  → Sending follow request to {target_id}...")
                success, info = await asyncio.to_thread(send_follow, target_id, jwt)

                if success:
                    stats["success"] += 1
                    track_count(info)
                    results.append({"uid": uid, "status": "success", "account_uid": uid})
                    record_success(user_id, uid, target_id)
                else:
                    stats["failed"] += 1
                    results.append({"uid": uid, "status": "failed", "account_uid": uid})
                    mark_followed(uid, str(target_id), user_id, "failed")

            # Small delay between follows to avoid rate limiting
            if i < total and delay > 0:
                await asyncio.sleep(delay)

        # Final progress update
        if update_msg and context:
            await self.update_progress(context, update_msg, target_id, total, total, stats, "Done", final=True)

        return stats, results

# ================= TELEGRAM HANDLERS =================
bot_instance = None

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"⚠️ Error: {context.error}")

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("✅ Cancelled!")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # Register user first (so referral bonus works immediately)
    existing_user = get_user(user_id)
    if not existing_user:
        referred_by = None
        if context.args and len(context.args) > 0:
            ref_code = context.args[0]
            referrer_id = get_user_by_referral_code(ref_code)
            if referrer_id and referrer_id != user_id:
                referred_by = referrer_id
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=f"🎉 New referral! {user.first_name} joined using your link!\nYou earned +{get_setting('referral_coins', 5)} coins!"
                    )
                except:
                    pass
        add_user(user_id, user.username, user.first_name, user.last_name, referred_by=referred_by)
        existing_user = get_user(user_id)

    # Channel join gate
    channels = get_channels()
    if channels:
        not_joined = []
        for channel in channels:
            if not await is_user_in_channel(user_id, context.bot, channel):
                not_joined.append(channel)

        if not_joined:
            reply_markup = build_join_keyboard(not_joined)

            await update.message.reply_text(
                "⚠️ **Please Join Required Channels First!**\n\n"
                "You need to join the following channels to use this bot:\n"
                + "\n".join([f"• @{ch}" for ch in not_joined]),
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            return

    user_info = get_user_info(user_id)
    referral_code = user_info["referral_code"] if user_info else f"ref_{user_id}"

    is_admin_user = is_admin(user_id)
    admin_text = " 👑 Admin" if is_admin_user else ""

    keyboard = [
        [InlineKeyboardButton("🎯 Follow Now", callback_data="follow")],
        [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
        [InlineKeyboardButton("👥 Referrals", callback_data="referrals")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
        [InlineKeyboardButton("🔗 My Referral Link", callback_data="referral_link")]
    ]
    # NOTE: Admin button REMOVED from user menu. Admins use /admin.

    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = f"""👋 Welcome {user.first_name}!{admin_text}

🎮 **Free Fire Follow Bot**

🪙 You get {get_setting('daily_coins', 5)} free coins daily!
1 coin = 1 follow

👥 Refer friends to earn +{get_setting('referral_coins', 5)} coins each!

🔗 Your Referral Link:
`https://t.me/{context.bot.username}?start={referral_code}`
"""

    await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if user joined channels after clicking button"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    channels = get_channels()

    not_joined = []
    for channel in channels:
        if not await is_user_in_channel(user_id, context.bot, channel):
            not_joined.append(channel)

    if not_joined:
        reply_markup = build_join_keyboard(not_joined)

        await query.edit_message_text(
            "⚠️ **Please Join All Required Channels First!**\n\n"
            "You still need to join:\n"
            + "\n".join([f"• @{ch}" for ch in not_joined]),
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    # Make sure user is registered
    user = query.from_user
    if not get_user(user_id):
        add_user(user_id, user.username, user.first_name, user.last_name)

    user_info = get_user_info(user_id)
    referral_code = user_info["referral_code"] if user_info else f"ref_{user_id}"

    is_admin_user = is_admin(user_id)
    admin_text = " 👑 Admin" if is_admin_user else ""

    keyboard = [
        [InlineKeyboardButton("🎯 Follow Now", callback_data="follow")],
        [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
        [InlineKeyboardButton("👥 Referrals", callback_data="referrals")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
        [InlineKeyboardButton("🔗 My Referral Link", callback_data="referral_link")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    welcome_text = f"""👋 Welcome {user.first_name}!{admin_text}

🎮 **Free Fire Follow Bot**

🪙 You get {get_setting('daily_coins', 5)} free coins daily!
1 coin = 1 follow

👥 Refer friends to earn +{get_setting('referral_coins', 5)} coins each!

🔗 Your Referral Link:
`https://t.me/{context.bot.username}?start={referral_code}`
"""

    await query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "follow":
        await follow_command_callback(update, context)
    elif data == "stats":
        await stats_command_callback(update, context)
    elif data == "referrals":
        await referrals_command_callback(update, context)
    elif data == "help":
        await help_command_callback(update, context)
    elif data == "referral_link":
        await referral_link_command_callback(update, context)
    elif data == "back_to_menu":
        await back_to_menu(update, context)
    elif data == "back_to_admin":
        await admin_command_callback(update, context)
    elif data == "check_join":
        await check_join_callback(update, context)
    elif data == "admin_command":
        await admin_command_callback(update, context)
    elif data.startswith("admin_"):
        await admin_handler(update, context, data)
    else:
        # Unknown callback -> ignore gracefully
        pass

async def follow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /follow command from message"""
    user_id = update.effective_user.id
    message = update.effective_message

    # Channel join check
    channels = get_channels()
    for channel in channels:
        if not await is_user_in_channel(user_id, context.bot, channel):
            await message.reply_text(
                "⚠️ **Please Join Required Channels First!**",
                reply_markup=build_join_keyboard(channels),
                parse_mode='Markdown'
            )
            return

    user = get_user(user_id)
    if user and user.get("is_banned", False):
        await message.reply_text("❌ You are banned!")
        return

    coins = get_available_coins(user_id)
    if coins <= 0:
        await message.reply_text("❌ You have 0 coins!\n\n🪙 Daily coins reset at midnight.\n👥 Or refer friends to earn +5 coins each!")
        return

    accounts = get_available_accounts()
    if not accounts:
        await message.reply_text("❌ No follower accounts available right now!\n\n⏳ Stock Over — wait for new stock. You'll be notified when new followers are added!")
        return

    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    coins_text = "♾️ Unlimited" if is_admin(user_id) else str(coins)

    msg = f"""🎯 Enter UID to follow

🪙 Your Coins: {coins_text}
💡 1 coin = 1 follow
📌 Each account follows a UID only once

Send UID (numbers only):"""

    await message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    context.user_data['awaiting_target'] = True

async def follow_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle follow button click"""
    query = update.callback_query
    user_id = query.from_user.id

    # Channel join check
    channels = get_channels()
    for channel in channels:
        if not await is_user_in_channel(user_id, context.bot, channel):
            await query.edit_message_text(
                "⚠️ **Please Join Required Channels First!**",
                reply_markup=build_join_keyboard(channels),
                parse_mode='Markdown'
            )
            return

    user = get_user(user_id)
    if user and user.get("is_banned", False):
        await query.edit_message_text("❌ You are banned!")
        return

    coins = get_available_coins(user_id)
    if coins <= 0:
        await query.edit_message_text("❌ You have 0 coins!\n\n🪙 Daily coins reset at midnight.\n👥 Or refer friends to earn +5 coins each!")
        return

    accounts = get_available_accounts()
    if not accounts:
        await query.edit_message_text("❌ No follower accounts available right now!\n\n⏳ Stock Over — wait for new stock. You'll be notified when new followers are added!")
        return

    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    coins_text = "♾️ Unlimited" if is_admin(user_id) else str(coins)

    msg = f"""🎯 Enter UID to follow

🪙 Your Coins: {coins_text}
💡 1 coin = 1 follow
📌 Each account follows a UID only once

Send UID (numbers only):"""

    await query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    context.user_data['awaiting_target'] = True

# ---- MERGED TEXT MESSAGE DISPATCHER (only ONE text handler) ----
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # 1) Broadcast message
    if context.user_data.get('awaiting_broadcast'):
        context.user_data.pop('awaiting_broadcast', None)
        if not is_admin(user_id):
            await update.message.reply_text("❌ Unauthorized!")
            return
        await do_broadcast(update, context, text)
        return

    # 2) Add admin
    if context.user_data.get('awaiting_add_admin'):
        context.user_data.pop('awaiting_add_admin', None)
        if not is_admin(user_id):
            await update.message.reply_text("❌ Unauthorized!")
            return
        await do_add_admin(update, context, text)
        return

    # 3) Remove admin
    if context.user_data.get('awaiting_remove_admin'):
        context.user_data.pop('awaiting_remove_admin', None)
        if not is_admin(user_id):
            await update.message.reply_text("❌ Unauthorized!")
            return
        await do_remove_admin(update, context, text)
        return

    # 4) Add channel
    if context.user_data.get('awaiting_add_channel'):
        context.user_data.pop('awaiting_add_channel', None)
        if not is_admin(user_id):
            await update.message.reply_text("❌ Unauthorized!")
            return
        await do_add_channel(update, context, text)
        return

    # 5) Remove channel
    if context.user_data.get('awaiting_remove_channel'):
        context.user_data.pop('awaiting_remove_channel', None)
        if not is_admin(user_id):
            await update.message.reply_text("❌ Unauthorized!")
            return
        await do_remove_channel(update, context, text)
        return

    # 6) Settings change
    if context.user_data.get('awaiting_setting'):
        if not is_admin(user_id):
            context.user_data.pop('awaiting_setting', None)
            await update.message.reply_text("❌ Unauthorized!")
            return
        await do_setting(update, context, text)
        return

    # 7) Follow target UID
    if context.user_data.get('awaiting_target'):
        context.user_data.pop('awaiting_target', None)
        if not text.isdigit():
            await update.message.reply_text("❌ Invalid UID! Please enter numbers only.")
            return
        await process_follow(update, context, text)
        return

async def process_follow(update: Update, context: ContextTypes.DEFAULT_TYPE, target_uid: str):
    user_id = update.effective_user.id

    # Channel join check
    channels = get_channels()
    for channel in channels:
        if not await is_user_in_channel(user_id, context.bot, channel):
            await update.message.reply_text(
                "⚠️ **Please Join Required Channels First!**",
                reply_markup=build_join_keyboard(channels),
                parse_mode='Markdown'
            )
            return

    user = get_user(user_id)
    if not user:
        await update.message.reply_text("❌ Start bot first with /start")
        return

    if user.get("is_banned", False):
        await update.message.reply_text("❌ You are banned!")
        return

    coins = get_available_coins(user_id)
    if coins <= 0:
        await update.message.reply_text("❌ You have 0 coins!\n\n🪙 Daily coins reset at midnight.\n👥 Or refer friends to earn +5 coins each!")
        return

    # ---- Stock logic: accounts that NEVER followed this target ----
    all_accounts = get_available_accounts()
    fresh_accounts = []
    for acc in all_accounts:
        if not is_target_followed_by_account(acc[0], target_uid):
            fresh_accounts.append({
                'uid': acc[0],
                'password': acc[1],
                'jwt_token': acc[2]
            })

    if not fresh_accounts:
        await update.message.reply_text(
            "❌ **Followers not available right now!**\n\n"
            "⏳ **Stock Over** — all available follower accounts have already followed this UID.\n\n"
            "📢 Wait for new stock. You'll be notified automatically when new follower accounts are added!"
        )
        return

    # ---- Concurrency: reserve accounts not currently in use by another request ----
    reserve_list = [(a['uid'], a['password'], a['jwt_token']) for a in fresh_accounts]
    reserved = reserve_accounts(reserve_list)

    if not reserved:
        await update.message.reply_text(
            "⏳ All follower accounts are busy right now. Please try again in a few seconds!"
        )
        return

    try:
        # Convert reserved tuples back to dicts
        accounts_to_use = [
            {'uid': r[0], 'password': r[1], 'jwt_token': r[2]}
            for r in reserved[:min(coins, len(reserved))]
        ]

        stock_notice = ""
        if len(reserved) < coins:
            stock_notice = (f"\n⚠️ Stock limited: only {len(reserved)} follower accounts available "
                            f"for this UID right now.\n📢 New stock coming soon!\n")

        # Initial progress message with bar
        bar = create_progress_bar(0, len(accounts_to_use))
        msg = await update.message.reply_text(
            f"🔄 **Processing follows for UID:** `{target_uid}`\n\n"
            f"`{bar}`\n"
            f"📊 Progress: 0/{len(accounts_to_use)}\n"
            f"✅ Success: 0 | ❌ Failed: 0\n"
            f"⏳ Starting...",
            parse_mode='Markdown'
        )

        print(f"\n📌 Processing {len(accounts_to_use)} follows for user {user_id} to target {target_uid}")

        # Process follows with live progress (async, non-blocking, concurrent-safe)
        stats, results = await bot_instance.process_follows(target_uid, accounts_to_use, user_id, msg, context)

        # Remaining coins after processing
        remaining_coins = get_available_coins(user_id)
        coins_disp = "♾️ Unlimited" if is_admin(user_id) else str(remaining_coins)

        keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        success_count = stats["success"]
        failed_count = stats["failed"]

        await msg.edit_text(
            f"✅ **Follow Request Complete!**\n\n"
            f"🎯 Target: `{target_uid}`\n"
            f"✅ Successful: {success_count}/{len(accounts_to_use)}\n"
            f"❌ Failed: {failed_count}/{len(accounts_to_use)}\n"
            f"🪙 Coins Left: {coins_disp}\n"
            f"{stock_notice}"
            f"\n💡 Use /follow to follow more!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    finally:
        release_accounts(reserved)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.effective_message

    user = get_user(user_id)
    if not user:
        await message.reply_text("❌ Start bot first with /start")
        return

    await show_stats_message(message, user_id)

async def stats_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    user = get_user(user_id)
    if not user:
        await query.edit_message_text("❌ Start bot first with /start")
        return

    await show_stats_callback(query, user_id)

async def show_stats_message(message, user_id):
    user = get_user(user_id)
    is_admin_user = is_admin(user_id)
    referrals = get_referral_count(user_id)
    coins, daily_granted, daily_limit = get_coins_info(user_id)

    # Get total follows from history
    data = load_data()
    total_follows = 0
    for entry in data["follow_history"]:
        if entry["user_id"] == user_id and entry["status"] == "success":
            total_follows += 1

    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    name = user.get("first_name") or user.get("username") or str(user_id)
    joined = user.get("joined_date", "N/A")[:10] if user.get("joined_date") else "N/A"

    if is_admin_user:
        coins_text = "♾️ Unlimited"
        daily_text = "♾️ Unlimited"
    else:
        coins_text = str(coins)
        daily_text = f"{daily_granted}/{daily_limit} today"

    await message.reply_text(
        f"📊 **Your Stats**\n\n"
        f"👤 {name}\n"
        f"📅 Joined: {joined}\n"
        f"👑 Admin: {'Yes' if is_admin_user else 'No'}\n\n"
        f"🎯 Follow Stats:\n"
        f"• Total: {total_follows}\n\n"
        f"🪙 Coins: {coins_text}\n"
        f"• Daily: {daily_text}\n"
        f"👥 Referrals: {referrals}\n"
        f"🔗 Code: `{user.get('referral_code', 'N/A')}`",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def show_stats_callback(query, user_id):
    user = get_user(user_id)
    is_admin_user = is_admin(user_id)
    referrals = get_referral_count(user_id)
    coins, daily_granted, daily_limit = get_coins_info(user_id)

    # Get total follows from history
    data = load_data()
    total_follows = 0
    for entry in data["follow_history"]:
        if entry["user_id"] == user_id and entry["status"] == "success":
            total_follows += 1

    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    name = user.get("first_name") or user.get("username") or str(user_id)
    joined = user.get("joined_date", "N/A")[:10] if user.get("joined_date") else "N/A"

    if is_admin_user:
        coins_text = "♾️ Unlimited"
        daily_text = "♾️ Unlimited"
    else:
        coins_text = str(coins)
        daily_text = f"{daily_granted}/{daily_limit} today"

    await query.edit_message_text(
        f"📊 **Your Stats**\n\n"
        f"👤 {name}\n"
        f"📅 Joined: {joined}\n"
        f"👑 Admin: {'Yes' if is_admin_user else 'No'}\n\n"
        f"🎯 Follow Stats:\n"
        f"• Total: {total_follows}\n\n"
        f"🪙 Coins: {coins_text}\n"
        f"• Daily: {daily_text}\n"
        f"👥 Referrals: {referrals}\n"
        f"🔗 Code: `{user.get('referral_code', 'N/A')}`",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def referral_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.effective_message

    user = get_user(user_id)
    if not user:
        await message.reply_text("❌ Start bot first with /start")
        return

    referral_code = user.get("referral_code", f"ref_{user_id}")

    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await message.reply_text(
        f"🔗 **Your Referral Link**\n\n"
        f"`https://t.me/{context.bot.username}?start={referral_code}`\n\n"
        f"Each referral gives +{get_setting('referral_coins', 5)} coins!\n"
        f"👥 Current referrals: {get_referral_count(user_id)}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def referral_link_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    user = get_user(user_id)
    if not user:
        await query.edit_message_text("❌ Start bot first with /start")
        return

    referral_code = user.get("referral_code", f"ref_{user_id}")

    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"🔗 **Your Referral Link**\n\n"
        f"`https://t.me/{context.bot.username}?start={referral_code}`\n\n"
        f"Each referral gives +{get_setting('referral_coins', 5)} coins!\n"
        f"👥 Current referrals: {get_referral_count(user_id)}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def referrals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message = update.effective_message

    user = get_user(user_id)
    if not user:
        await message.reply_text("❌ Start bot first with /start")
        return

    referred_users = get_referred_users(user_id)

    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    referral_code = user.get("referral_code", f"ref_{user_id}")

    if not referred_users:
        await message.reply_text(
            f"👥 **Your Referrals**\n\n"
            f"You haven't referred anyone yet!\n\n"
            f"🔗 Your Referral Link:\n"
            f"`https://t.me/{context.bot.username}?start={referral_code}`\n\n"
            f"Share your link to earn +{get_setting('referral_coins', 5)} coins per referral!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    ref_text = "👥 **Your Referrals**\n\n"
    for ref in referred_users[:20]:
        name = ref[1] or ref[2] or f"User {ref[0]}"
        date = ref[3][:10] if ref[3] else 'N/A'
        ref_text += f"• {name} - {date}\n"

    ref_text += f"\n📊 Total: {len(referred_users)} referrals (+{len(referred_users) * get_setting('referral_coins', 5)} coins earned)\n\n"
    ref_text += f"🔗 Your Referral Link:\n`https://t.me/{context.bot.username}?start={referral_code}`"

    await message.reply_text(ref_text, reply_markup=reply_markup, parse_mode='Markdown')

async def referrals_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    user = get_user(user_id)
    if not user:
        await query.edit_message_text("❌ Start bot first with /start")
        return

    referred_users = get_referred_users(user_id)

    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    referral_code = user.get("referral_code", f"ref_{user_id}")

    if not referred_users:
        await query.edit_message_text(
            f"👥 **Your Referrals**\n\n"
            f"You haven't referred anyone yet!\n\n"
            f"🔗 Your Referral Link:\n"
            f"`https://t.me/{context.bot.username}?start={referral_code}`\n\n"
            f"Share your link to earn +{get_setting('referral_coins', 5)} coins per referral!",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return

    ref_text = "👥 **Your Referrals**\n\n"
    for ref in referred_users[:20]:
        name = ref[1] or ref[2] or f"User {ref[0]}"
        date = ref[3][:10] if ref[3] else 'N/A'
        ref_text += f"• {name} - {date}\n"

    ref_text += f"\n📊 Total: {len(referred_users)} referrals (+{len(referred_users) * get_setting('referral_coins', 5)} coins earned)\n\n"
    ref_text += f"🔗 Your Referral Link:\n`https://t.me/{context.bot.username}?start={referral_code}`"

    await query.edit_message_text(ref_text, reply_markup=reply_markup, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await message.reply_text(
        f"""ℹ️ **Help**

Commands:
/start - Start bot
/follow - Follow a user
/stats - Your statistics
/referrals - View referrals
/help - This help
/cancel - Cancel any pending action

🪙 Coin system:
• {get_setting('daily_coins', 5)} free coins daily (reset at midnight)
• +{get_setting('referral_coins', 5)} coins per referral
• 1 coin = 1 follow

How to use:
1. Click "Follow Now"
2. Enter UID
3. Bot follows using fresh accounts (each account follows a UID only once)

📌 If stock runs out, you'll be notified when new follower accounts are added.""",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def help_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"""ℹ️ **Help**

Commands:
/start - Start bot
/follow - Follow a user
/stats - Your statistics
/referrals - View referrals
/help - This help
/cancel - Cancel any pending action

🪙 Coin system:
• {get_setting('daily_coins', 5)} free coins daily (reset at midnight)
• +{get_setting('referral_coins', 5)} coins per referral
• 1 coin = 1 follow

How to use:
1. Click "Follow Now"
2. Enter UID
3. Bot follows using fresh accounts (each account follows a UID only once)

📌 If stock runs out, you'll be notified when new follower accounts are added.""",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.pop('awaiting_target', None)

    keyboard = [
        [InlineKeyboardButton("🎯 Follow Now", callback_data="follow")],
        [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
        [InlineKeyboardButton("👥 Referrals", callback_data="referrals")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
        [InlineKeyboardButton("🔗 My Referral Link", callback_data="referral_link")]
    ]
    # NOTE: Admin button REMOVED from user menu.

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "🏠 **Main Menu**\n\nChoose an option:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# ================= ADMIN COMMANDS =================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized!")
        return

    keyboard = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dashboard")],
        [InlineKeyboardButton("📤 Upload Accounts", callback_data="admin_upload")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("📈 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("📤 Export Data", callback_data="admin_export")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("👑 Add Admin", callback_data="admin_add_admin")],
        [InlineKeyboardButton("❌ Remove Admin", callback_data="admin_remove_admin")],
        [InlineKeyboardButton("📋 Channel Management", callback_data="admin_channels")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"🔐 **Admin Panel**\n\n"
        f"Users: {get_total_users()}\n"
        f"Accounts: {get_accounts_count()}\n"
        f"Today: {get_today_follows()}\n"
        f"Admins: {len(load_data().get('admins', []))}\n"
        f"Channels: {len(load_data().get('channels', []))}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def admin_command_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if not is_admin(user_id):
        await query.edit_message_text("❌ Unauthorized!")
        return

    keyboard = [
        [InlineKeyboardButton("📊 Dashboard", callback_data="admin_dashboard")],
        [InlineKeyboardButton("📤 Upload Accounts", callback_data="admin_upload")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="admin_settings")],
        [InlineKeyboardButton("📈 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("📤 Export Data", callback_data="admin_export")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("👑 Add Admin", callback_data="admin_add_admin")],
        [InlineKeyboardButton("❌ Remove Admin", callback_data="admin_remove_admin")],
        [InlineKeyboardButton("📋 Channel Management", callback_data="admin_channels")]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"🔐 **Admin Panel**\n\n"
        f"Users: {get_total_users()}\n"
        f"Accounts: {get_accounts_count()}\n"
        f"Today: {get_today_follows()}\n"
        f"Admins: {len(load_data().get('admins', []))}\n"
        f"Channels: {len(load_data().get('channels', []))}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if not is_admin(user_id):
        await query.edit_message_text("❌ Unauthorized!")
        return

    if data == "admin_dashboard":
        await admin_dashboard(query)
    elif data in ("admin_upload", "admin_upload_json"):
        await admin_upload_prompt(query)
    elif data == "admin_settings":
        await admin_settings_menu(query)
    elif data == "admin_stats":
        await admin_stats_view(query)
    elif data == "admin_export":
        await admin_export_data(query)
    elif data == "admin_broadcast":
        await admin_broadcast_prompt(query, context)
    elif data == "admin_add_admin":
        await admin_add_admin_prompt(query, context)
    elif data == "admin_remove_admin":
        await admin_remove_admin_prompt(query, context)
    elif data == "admin_channels":
        await admin_channels_menu(query, context)
    elif data == "admin_add_channel":
        await admin_add_channel_prompt(query, context)
    elif data == "admin_remove_channel":
        await admin_remove_channel_prompt(query, context)
    elif data == "admin_setting_daily":
        await admin_setting_prompt(query, context, "daily_coins", "Daily Coins")
    elif data == "admin_setting_referral":
        await admin_setting_prompt(query, context, "referral_coins", "Referral Coins")
    elif data == "admin_setting_delay":
        await admin_setting_prompt(query, context, "follow_delay", "Follow Delay (seconds)")

async def admin_dashboard(query):
    data = load_data()
    await query.edit_message_text(
        f"📊 **Dashboard**\n\n"
        f"📈 Stats:\n"
        f"• Users: {get_total_users()}\n"
        f"• Accounts: {get_accounts_count()}\n"
        f"• Today: {get_today_follows()}\n"
        f"• Admins: {len(data.get('admins', []))}\n"
        f"• Channels: {len(data.get('channels', []))}\n\n"
        f"⚙️ Settings:\n"
        f"• Daily Coins: {get_setting('daily_coins', 5)}\n"
        f"• Referral Coins: {get_setting('referral_coins', 5)}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]]),
        parse_mode='Markdown'
    )

async def admin_broadcast_prompt(query, context):
    context.user_data['awaiting_broadcast'] = True
    await query.edit_message_text(
        "📢 **Broadcast Message**\n\n"
        "Send me the message you want to broadcast to all users.\n\n"
        "(Send /cancel to abort)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]]),
        parse_mode='Markdown'
    )

async def admin_add_admin_prompt(query, context):
    context.user_data['awaiting_add_admin'] = True
    await query.edit_message_text(
        "👑 **Add Admin**\n\n"
        "Send me the Telegram User ID of the user you want to make admin.\n\n"
        "Example: `123456789`\n\n"
        "(Send /cancel to abort)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]]),
        parse_mode='Markdown'
    )

async def admin_remove_admin_prompt(query, context):
    context.user_data['awaiting_remove_admin'] = True
    await query.edit_message_text(
        "❌ **Remove Admin**\n\n"
        "Send me the Telegram User ID of the admin you want to remove.\n\n"
        "⚠️ Original admins (from ADMIN_IDS in config) are protected and cannot be removed.\n\n"
        "(Send /cancel to abort)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]]),
        parse_mode='Markdown'
    )

async def admin_add_channel_prompt(query, context):
    context.user_data['awaiting_add_channel'] = True
    await query.edit_message_text(
        "📋 **Add Channel / Group**\n\n"
        "Send the channel link or username. Accepted formats:\n"
        "• `https://t.me/my_channel`\n"
        "• `t.me/my_channel`\n"
        "• `@my_channel`\n"
        "• `my_channel`\n"
        "• numeric chat ID: `-1001234567890`\n\n"
        "⚠️ The bot must be **admin** in the channel/group.\n"
        "❌ Private invite links (`t.me/+xxxx`) are NOT supported.\n\n"
        "(Send /cancel to abort)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]]),
        parse_mode='Markdown'
    )

async def admin_remove_channel_prompt(query, context):
    context.user_data['awaiting_remove_channel'] = True
    await query.edit_message_text(
        "📋 **Remove Channel**\n\n"
        "Send the channel username or link to remove.\n\n"
        "Example: `my_channel` or `https://t.me/my_channel`\n\n"
        "(Send /cancel to abort)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]]),
        parse_mode='Markdown'
    )

async def admin_setting_prompt(query, context, key, label):
    context.user_data['awaiting_setting'] = key
    await query.edit_message_text(
        f"⚙️ **Set {label}**\n\n"
        "Send the new value (positive number).\n\n"
        "(Send /cancel to abort)",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]]),
        parse_mode='Markdown'
    )

async def admin_channels_menu(query, context):
    data = load_data()
    channels = data.get('channels', [])

    if channels:
        channel_text = "📋 **Channel Management**\n\nCurrent channels:\n"
        for i, channel in enumerate(channels, 1):
            s = str(channel)
            display = f"@{s.lstrip('@')}" if not s.lstrip('-').isdigit() else s
            channel_text += f"{i}. {display}\n"
        channel_text += f"\nTotal: {len(channels)} channel(s)"
    else:
        channel_text = "📋 **Channel Management**\n\nNo channels added yet."

    keyboard = [
        [InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel")],
        [InlineKeyboardButton("➖ Remove Channel", callback_data="admin_remove_channel")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]
    ]
    await query.edit_message_text(
        channel_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_upload_prompt(query):
    keyboard = [
        [InlineKeyboardButton("📄 Upload JSON", callback_data="admin_upload_json")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]
    ]
    await query.edit_message_text(
        "📤 **Upload Accounts**\n\n"
        "Send a **JSON file** to this chat.\n\n"
        "Format:\n"
        "```json\n"
        "[\n"
        "  {\n"
        "    \"uid\": \"123456\",\n"
        "    \"password\": \"pass\"\n"
        "  }\n"
        "]\n"
        "```\n\n"
        "✅ All users will be notified when new follower accounts are added!",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_settings_menu(query):
    keyboard = [
        [InlineKeyboardButton("🪙 Daily Coins", callback_data="admin_setting_daily")],
        [InlineKeyboardButton("🎁 Referral Coins", callback_data="admin_setting_referral")],
        [InlineKeyboardButton("⏱ Follow Delay", callback_data="admin_setting_delay")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]
    ]
    await query.edit_message_text(
        f"⚙️ **Settings**\n\n"
        f"• Daily Coins: {get_setting('daily_coins', 5)}\n"
        f"• Referral Coins: {get_setting('referral_coins', 5)}\n"
        f"• Follow Delay: {get_setting('follow_delay', 2)}s",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def admin_stats_view(query):
    data = load_data()
    total = len(data["follow_history"])
    success = len([e for e in data["follow_history"] if e["status"] == "success"])
    rate = ((success / total) * 100) if total > 0 else 0

    daily = {}
    for entry in data["follow_history"]:
        date = entry["follow_date"][:10]
        daily[date] = daily.get(date, 0) + 1

    text = f"📈 **Statistics**\n\nTotal: {total}\nSuccess: {success}\nRate: {rate:.1f}%\n\nLast 7 days:\n"
    for date, count in sorted(daily.items(), reverse=True)[:7]:
        text += f"• {date}: {count}\n"

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]]),
        parse_mode='Markdown'
    )

async def admin_export_data(query):
    data = load_data()
    accounts = []
    for acc in data["accounts"]:
        if acc.get("is_active", True):
            accounts.append({
                "uid": acc["uid"],
                "password": acc["password"],
                "jwt_token": acc.get("jwt_token")
            })

    if not accounts:
        await query.edit_message_text("No accounts to export.")
        return

    filename = f"accounts_{int(time.time())}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(accounts, f, indent=2)

    try:
        with open(filename, 'rb') as f:
            await query.message.reply_document(document=f, filename=filename)
        await query.edit_message_text("✅ Export complete!")
    except Exception as e:
        await query.edit_message_text(f"❌ Export error: {e}")
    finally:
        if os.path.exists(filename):
            os.remove(filename)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ Unauthorized!")
        return

    document = update.message.document
    if not document.file_name.endswith('.json'):
        await update.message.reply_text("❌ Upload JSON file!")
        return

    file = await context.bot.get_file(document.file_id)
    file_path = f"temp_{int(time.time())}_{uuid.uuid4().hex[:6]}.json"
    await file.download_to_drive(file_path)

    try:
        accounts = load_accounts_from_file(file_path)

        if not accounts:
            await update.message.reply_text("❌ No valid accounts found in file!")
            return

        added = 0
        for acc in accounts:
            uid = acc.get('uid')
            password = acc.get('password')
            jwt_token = acc.get('jwt_token')

            if uid and password:
                if add_account(uid, password, jwt_token, added_by=user_id):
                    added += 1

        await update.message.reply_text(
            f"✅ **Upload Complete!**\n\n"
            f"Added: {added} accounts\n"
            f"Total accounts: {get_accounts_count()}"
        )

        # Notify users in a BACKGROUND task so the bot never freezes on upload
        if added > 0:
            context.application.create_task(notify_new_stock(context, added))

    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def notify_new_stock(context, count):
    """Notify all users that new follower accounts were added (runs in background)"""
    data = load_data()
    users = list(data["users"].keys())

    sent = 0
    for user_id_str in users:
        try:
            await context.bot.send_message(
                chat_id=int(user_id_str),
                text=f"🆕 **New Followers Added!**\n\n"
                     f"✅ {count} new follower account(s) are now available!\n\n"
                     f"🎯 Click **Follow Now** to boost your follows!\n"
                     f"🪙 1 coin = 1 follow",
                parse_mode='Markdown'
            )
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)  # avoid Telegram flood limits

    print(f"📢 New-stock notification sent to {sent} users")

# ================= ACTION HELPERS (used by merged text dispatcher) =================

async def do_broadcast(update, context, message):
    data = load_data()
    users = list(data["users"].keys())

    if not users:
        await update.message.reply_text("📭 No users to broadcast to yet.")
        return

    status = await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...\n⏳ This may take a while.")

    sent = 0
    failed = 0

    for user_id_str in users:
        try:
            await context.bot.send_message(
                chat_id=int(user_id_str),
                text=f"📢 Announcement\n\n{message}"
            )
            sent += 1
        except Exception:
            failed += 1

        if (sent + failed) % 20 == 0:
            try:
                await status.edit_text(f"📢 Broadcasting... {sent + failed}/{len(users)}")
            except Exception:
                pass

        await asyncio.sleep(0.05)

    try:
        await status.edit_text(
            f"✅ **Broadcast Complete!**\n\n📤 Sent: {sent}\n❌ Failed: {failed}",
            parse_mode='Markdown'
        )
    except Exception:
        await status.edit_text(f"✅ Broadcast Complete!\n\nSent: {sent}\nFailed: {failed}")

async def do_add_admin(update, context, text):
    try:
        new_admin_id = int(text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID! Send numbers only.")
        return

    if add_admin(new_admin_id):
        await update.message.reply_text(f"✅ User `{new_admin_id}` is now an admin!")
        try:
            await context.bot.send_message(
                chat_id=new_admin_id,
                text="👑 You have been promoted to Admin!\n\nYou now have access to the admin panel (use /admin) and unlimited coins."
            )
        except Exception:
            pass
    else:
        await update.message.reply_text("❌ User is already an admin!")

async def do_remove_admin(update, context, text):
    try:
        remove_id = int(text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID! Send numbers only.")
        return

    if remove_admin(remove_id):
        await update.message.reply_text(f"✅ User `{remove_id}` is no longer an admin!")
    else:
        await update.message.reply_text("❌ User is not an admin or cannot be removed (original admins are protected).")

async def do_add_channel(update, context, text):
    """Add channel/group accepting full links, @username, plain username or numeric ID"""
    identifier, kind = parse_channel_identifier(text)

    if kind == 'private':
        await update.message.reply_text(
            "❌ Private invite links (`t.me/+xxxx`) are NOT supported.\n\n"
            "Please add the bot as admin in the channel/group and send the public username "
            "(e.g. `https://t.me/my_channel` or `@my_channel`) or the numeric chat ID."
        )
        return

    if not identifier:
        await update.message.reply_text("❌ Invalid channel link/name!")
        return

    # Validate the bot can actually access this chat
    try:
        cid = identifier if identifier.lstrip('-').isdigit() else "@" + identifier.lstrip('@')
        chat = await context.bot.get_chat(chat_id=cid)
        chat_title = chat.title or identifier
    except Exception:
        await update.message.reply_text(
            f"❌ Cannot access `{identifier}`.\n\n"
            f"Make sure the bot is **added as admin** in the channel/group first.",
            parse_mode='Markdown'
        )
        return

    if add_channel(identifier):
        await update.message.reply_text(
            f"✅ Channel `{chat_title}` added!\n\n"
            f"📌 Users must join `@{identifier}` to use the bot.",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ Channel already exists!")

async def do_remove_channel(update, context, text):
    identifier, kind = parse_channel_identifier(text)
    if kind == 'private' or not identifier:
        await update.message.reply_text("❌ Invalid channel link/name!")
        return

    if remove_channel(identifier):
        await update.message.reply_text(f"✅ Channel `{identifier}` removed!")
    else:
        await update.message.reply_text("❌ Channel not found!")

async def do_setting(update, context, text):
    setting_key = context.user_data.pop('awaiting_setting', None)

    try:
        value = int(text.strip())
        if value <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Send a positive number!")
        return

    set_setting(setting_key, value)
    labels = {
        'daily_coins': 'Daily Coins',
        'referral_coins': 'Referral Coins',
        'follow_delay': 'Follow Delay (seconds)'
    }
    await update.message.reply_text(f"✅ {labels.get(setting_key, setting_key)} set to {value}!")

# ================= MAIN =================
def main():
    global bot_instance

    print("🤖 Starting bot...")

    bot_instance = FollowBot(BOT_TOKEN)

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)   # handle 2+ requests simultaneously (no queue/freeze)
        .build()
    )

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("follow", follow_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("referrals", referrals_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("cancel", cancel_command))

    # Single callback handler routes everything
    application.add_handler(CallbackQueryHandler(button_handler))

    # Single text message handler (dispatches all awaiting states)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    application.add_error_handler(error_handler)

    print(f"✅ Bot running! Admin: {ADMIN_IDS}")
    print(f"👥 Users: {get_total_users()}")
    print(f"📁 Accounts: {get_accounts_count()}")

    application.run_polling()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped")
        sys.exit(0)
