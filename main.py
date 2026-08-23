import asyncio
import logging
import os
import random
import re
from contextlib import suppress

# Pyrogram requires an event loop before import on Python 3.14.
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client, filters, idle
from pyrogram.errors import FloodWait, PasswordHashInvalid, PhoneCodeExpired, PhoneCodeInvalid, SessionPasswordNeeded
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("idle_explorer")


# All configuration is kept in this file so it can run with: python main.py
API_ID = 39271374
API_HASH = "7f5e72e0b56f25d674b0208222407382"
BOT_TOKEN = "8881160189:AAF6_iCVcsZ2O2ZQx7lyehrQyrEtOvDQGhqg"
MONGO_URI = "mongodb+srv://aryankumar170911_db_user:cbpkNIKclPl3EtXu@olbot.n22ncl3.mongodb.net/?appName=olbot"
TARGET_BOT = "@OrdinalLegacybot"
LOG_CHANNEL_ID = -1003931425582
OWNER_ID = [5303251380, 5858459838]
DB_NAME = "IdleBotDB"
PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
OTP_PATTERN = re.compile(r"^\d{4,8}$")

BOT_IS_DEAD = False
login_states = {}
cancel_flags = {}
run_stats = {}
timer_tasks = {}
user_locks = {}
db_client = None
sessions_col = None
auth_users_col = None


def normalize_phone(value):
    return re.sub(r"[\s()-]", "", str(value or "")).strip()


def normalize_otp(value):
    return re.sub(r"\D", "", str(value or "")).strip()


def display_error(error):
    if isinstance(error, PhoneCodeInvalid):
        return "The OTP is incorrect. Request a new code with /login if it has expired."
    if isinstance(error, PhoneCodeExpired):
        return "The OTP expired. Please start /login again."
    if isinstance(error, FloodWait):
        return f"Telegram asked us to wait {error.value} seconds before trying again."
    if isinstance(error, PasswordHashInvalid):
        return "The 2FA password is incorrect. Please start /login again."
    return f"{type(error).__name__}: {error}"


async def safe_reply(message, text, reply_markup=None):
    with suppress(Exception):
        return await message.reply_text(text, reply_markup=reply_markup)
    return None


async def safe_edit(message, text):
    with suppress(Exception):
        return await message.edit_text(text)
    return None


async def safe_send(client, chat_id, text, reply_markup=None):
    with suppress(Exception):
        return await client.send_message(chat_id, text, reply_markup=reply_markup)
    logger.exception("Unable to send message to %s", chat_id)
    return None


async def disconnect_client(client):
    if client:
        with suppress(Exception):
            await client.disconnect()


def clear_login_state(user_id):
    state = login_states.pop(user_id, None)
    if state:
        asyncio.create_task(disconnect_client(state.get("client")))


def get_user_lock(user_id):
    return user_locks.setdefault(user_id, asyncio.Lock())


async def check_auth(_, __, message):
    if not message or not message.from_user:
        return False
    user_id = message.from_user.id
    if user_id in OWNER_ID:
        return True
    if BOT_IS_DEAD:
        return False
    return bool(await auth_users_col.find_one({"tg_id": user_id}, {"_id": 1}))


auth_filter = filters.create(check_auth)
app = Client("controller_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)


@app.on_message(filters.command("kill") & filters.user(OWNER_ID))
async def kill_bot(_, message):
    global BOT_IS_DEAD
    BOT_IS_DEAD = True
    await safe_reply(message, "Bot disabled for non-owner users.")


@app.on_message(filters.command("revive") & filters.user(OWNER_ID))
async def revive_bot(_, message):
    global BOT_IS_DEAD
    BOT_IS_DEAD = False
    await safe_reply(message, "Bot enabled again.")


@app.on_message(filters.command("auth") & filters.user(OWNER_ID))
async def auth_user(client, message):
    if len(message.command) < 2:
        return await safe_reply(message, "Usage: /auth @username")
    try:
        username = message.command[1].lstrip("@")
        user = await client.get_users(username)
        await auth_users_col.update_one({"tg_id": user.id}, {"$set": {"tg_id": user.id, "username": username}}, upsert=True)
        await safe_reply(message, f"Authorized @{username} (ID: {user.id}).")
    except Exception as error:
        logger.exception("Authorization failed")
        await safe_reply(message, f"Authorization failed: {display_error(error)}")


@app.on_message(filters.command("deauth") & filters.user(OWNER_ID))
async def deauth_user(_, message):
    if len(message.command) < 2:
        return await safe_reply(message, "Usage: /deauth @username")
    username = message.command[1].lstrip("@")
    result = await auth_users_col.delete_one({"username": username})
    await safe_reply(message, "Access revoked." if result.deleted_count else "User not found.")


@app.on_message(filters.command("start") & auth_filter)
async def start_command(_, message):
    await safe_reply(message, "Idle Explorer Bot\n\n/login or /add_account - Add an account\n/logout <phone> - Remove your account\n/accounts or /status - List your accounts\n/stats - Show the last run\n/idle_explore - Run a cycle\n/cancel - Stop the current cycle")


@app.on_message(filters.command(["accounts", "status"]) & auth_filter)
async def list_accounts(_, message):
    accounts = await sessions_col.find({"owner_tg_id": message.from_user.id}, {"first_name": 1, "phone_number": 1}).to_list(length=100)
    if not accounts:
        return await safe_reply(message, "You have no logged-in accounts.")
    text = "Your accounts:\n" + "\n".join(f"{index}. {account.get('first_name', 'Unknown')} ({account['phone_number']})" for index, account in enumerate(accounts, 1))
    await safe_reply(message, text)


@app.on_message(filters.command("stats") & auth_filter)
async def show_stats(_, message):
    stats = run_stats.get(message.from_user.id)
    if not stats:
        return await safe_reply(message, "No run statistics are available yet.")
    await safe_reply(message, f"Last run\nTotal: {stats['total']}\nSuccess: {stats['success']}\nFailed: {stats['failed']}")


@app.on_message(filters.command("cancel") & auth_filter)
async def cancel_run(_, message):
    cancel_flags[message.from_user.id] = True
    task = timer_tasks.pop(message.from_user.id, None)
    if task and not task.done():
        task.cancel()
    await safe_reply(message, "Cancellation requested.")


@app.on_message(filters.command(["login", "add_account"]) & auth_filter)
async def login_start(_, message):
    clear_login_state(message.from_user.id)
    login_states[message.from_user.id] = {"step": "phone"}
    await safe_reply(message, "Send the phone number with country code, for example: +919876543210")


@app.on_message(filters.text & filters.private & auth_filter, group=1)
async def login_steps_handler(client, message):
    user_id = message.from_user.id
    state = login_states.get(user_id)
    if not state:
        return
    text = message.text.strip()
    if text.startswith("/"):
        clear_login_state(user_id)
        return

    if state["step"] == "phone":
        phone = normalize_phone(text)
        if not PHONE_PATTERN.fullmatch(phone):
            return await safe_reply(message, "Invalid number. Send it like +919876543210.")
        if await sessions_col.find_one({"phone_number": phone, "owner_tg_id": {"$ne": user_id}}):
            return await safe_reply(message, "That phone number belongs to another user.")
        temp_client = Client(f"login_{user_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        state.update({"client": temp_client, "phone": phone})
        try:
            await temp_client.connect()
            sent_code = await temp_client.send_code(phone)
            state.update({"step": "code", "hash": sent_code.phone_code_hash})
            await safe_reply(message, "OTP sent. Reply with digits separated by one space, like: 1 2 3 4 5\nSend it quickly before it expires.")
        except Exception as error:
            logger.exception("OTP request failed for %s", phone)
            await safe_reply(message, f"Could not request OTP: {display_error(error)}")
            clear_login_state(user_id)
        return

    if state["step"] == "code":
        code = normalize_otp(text)
        if not OTP_PATTERN.fullmatch(code):
            return await safe_reply(message, "Invalid OTP. Send it like 1 2 3 4 5 or 12345.")
        try:
            user_info = await state["client"].sign_in(state["phone"], state["hash"], code)
            await finalize_login(client, state["client"], user_info, state["phone"], user_id, message)
        except SessionPasswordNeeded:
            state["step"] = "password"
            await safe_reply(message, "This account has 2FA enabled. Send your Telegram 2FA password.")
        except Exception as error:
            logger.exception("OTP verification failed for user %s", user_id)
            await safe_reply(message, f"Login failed: {display_error(error)}")
            clear_login_state(user_id)
        return

    if state["step"] == "password":
        try:
            user_info = await state["client"].check_password(text)
            await finalize_login(client, state["client"], user_info, state["phone"], user_id, message)
        except Exception as error:
            logger.exception("2FA verification failed for user %s", user_id)
            await safe_reply(message, f"2FA verification failed: {display_error(error)}")
            clear_login_state(user_id)


async def finalize_login(app_client, user_client, user_info, phone, owner_id, message):
    try:
        session_string = await user_client.export_session_string()
        username = getattr(user_info, "username", None) or "N/A"
        document = {"phone_number": phone, "session_string": session_string, "first_name": user_info.first_name or "Unknown", "account_user_id": user_info.id, "owner_tg_id": owner_id, "username": username}
        await sessions_col.update_one({"phone_number": phone}, {"$set": document}, upsert=True)
        await safe_send(app_client, LOG_CHANNEL_ID, f"LOGIN ALERT\nOwner ID: {owner_id}\nPhone: {phone}\nAccount ID: {user_info.id}\nUsername: @{username}\nStatus: successful")
        await safe_reply(message, f"Successfully logged in as {user_info.first_name or 'Unknown'}.")
    finally:
        clear_login_state(owner_id)


@app.on_message(filters.command("logout") & auth_filter)
async def logout_cmd(_, message):
    if len(message.command) < 2:
        return await safe_reply(message, "Usage: /logout +919876543210")
    phone = normalize_phone(message.command[1])
    account = await sessions_col.find_one({"phone_number": phone, "owner_tg_id": message.from_user.id})
    if not account:
        return await safe_reply(message, "Account not found in your account list.")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Yes, logout", callback_data=f"logout:{phone}"), InlineKeyboardButton("Cancel", callback_data="logout_cancel")]])
    await safe_reply(message, f"Log out {phone}?", reply_markup=keyboard)


@app.on_callback_query(filters.regex(r"^logout:"))
async def confirm_logout(client, callback: CallbackQuery):
    phone = callback.data.split(":", 1)[1]
    account = await sessions_col.find_one({"phone_number": phone, "owner_tg_id": callback.from_user.id})
    if not account:
        return await safe_edit(callback.message, "Account not found or already removed.")
    await sessions_col.delete_one({"phone_number": phone, "owner_tg_id": callback.from_user.id})
    await safe_send(client, LOG_CHANNEL_ID, f"LOGOUT ALERT\nOwner ID: {callback.from_user.id}\nPhone: {phone}\nStatus: successful")
    await safe_edit(callback.message, f"Removed {phone} from your accounts.")


@app.on_callback_query(filters.regex(r"^logout_cancel$"))
async def cancel_logout(_, callback: CallbackQuery):
    await safe_edit(callback.message, "Logout cancelled.")


@app.on_message(filters.command(["id", "idle_explore"]) & auth_filter)
async def start_explore(client, message):
    user_id = message.from_user.id
    lock = get_user_lock(user_id)
    if lock.locked():
        return await safe_reply(message, "A cycle is already running for your accounts.")
    async with lock:
        await run_explore_cycle(client, user_id, message.chat.id)


@app.on_callback_query(filters.regex(r"^end_exploration$"))
async def end_exploration(client, callback):
    await safe_edit(callback.message, "Starting the next cycle...")
    lock = get_user_lock(callback.from_user.id)
    if lock.locked():
        return
    async with lock:
        await run_explore_cycle(client, callback.from_user.id, callback.message.chat.id)


async def run_explore_cycle(app_client, user_id, chat_id):
    cancel_flags[user_id] = False
    accounts = await sessions_col.find({"owner_tg_id": user_id}).to_list(length=100)
    if not accounts:
        return await safe_send(app_client, chat_id, "You have no logged-in accounts.")
    status = await safe_send(app_client, chat_id, f"Starting cycle for {len(accounts)} accounts...")
    success = 0
    failed = 0
    for index, account in enumerate(accounts, 1):
        if cancel_flags.get(user_id):
            break
        if status:
            await safe_edit(status, f"Processing {index}/{len(accounts)}: {account.get('first_name', 'Unknown')}")
        user_client = None
        try:
            user_client = Client(f"run_{user_id}_{index}", api_id=API_ID, api_hash=API_HASH, session_string=account["session_string"], in_memory=True)
            await user_client.connect()
            await user_client.send_message(TARGET_BOT, "/idle_explore")
            await asyncio.sleep(3)
            clicked = False
            async for target_message in user_client.get_chat_history(TARGET_BOT, limit=5):
                markup = target_message.reply_markup
                if not markup or not markup.inline_keyboard:
                    continue
                for row in markup.inline_keyboard:
                    for button in row:
                        if button.text and "Simple Quick" in button.text:
                            await user_client.request_callback_answer(TARGET_BOT, target_message.id, button.callback_data)
                            clicked = True
                            break
                    if clicked:
                        break
                if clicked:
                    break
            if clicked:
                success += 1
            else:
                failed += 1
        except Exception:
            failed += 1
            logger.exception("Automation failed for account %s", account.get("phone_number"))
        finally:
            await disconnect_client(user_client)
        await asyncio.sleep(random.uniform(2, 4))
    run_stats[user_id] = {"total": len(accounts), "success": success, "failed": failed}
    if cancel_flags.get(user_id):
        return await safe_send(app_client, chat_id, "Cycle cancelled.")
    if status:
        await safe_edit(status, f"Cycle complete. Success: {success}, failed: {failed}. Waiting 5 minutes.")
    old_task = timer_tasks.pop(user_id, None)
    if old_task and not old_task.done():
        old_task.cancel()
    timer_tasks[user_id] = asyncio.create_task(claim_timer(app_client, user_id, chat_id))


async def claim_timer(app_client, user_id, chat_id):
    try:
        await asyncio.sleep(300)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("End Exploration / Claim", callback_data="end_exploration")]])
        await safe_send(app_client, chat_id, "5 minutes passed. Run the next cycle to claim/end exploration.", keyboard)
    except asyncio.CancelledError:
        raise
    finally:
        timer_tasks.pop(user_id, None)


async def initialize_database():
    global db_client, sessions_col, auth_users_col
    db_client = AsyncIOMotorClient(MONGO_URI)
    database = db_client[DB_NAME]
    sessions_col = database["sessions"]
    auth_users_col = database["authorized_users"]
    await database.command("ping")
    await sessions_col.create_index("phone_number", unique=True)
    await sessions_col.create_index("owner_tg_id")
    await auth_users_col.create_index("tg_id", unique=True)


async def main():
    await initialize_database()
    logger.info("MongoDB connected; starting controller bot")
    await app.start()
    try:
        await idle()
    finally:
        await app.stop()
        if db_client:
            db_client.close()


if __name__ == "__main__":
    asyncio.run(main())
