import logging
import os
import time
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set in environment")

# Rate‑limit settings (in seconds)
RATE_LIMIT = 300  # 5 minutes

# In‑memory storage
user_data = {}        # user_id -> { ... flow data ... }
last_attempt = {}     # user_id -> last interaction time

# Conversation states for Session Generator
S_API_ID, S_API_HASH, S_PHONE, S_CODE, S_PASSWORD = range(5)

# Conversation states for Account Deletion
D_API_ID, D_API_HASH, D_PHONE, D_CODE, D_PASSWORD, D_CONFIRM = range(6)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ----- Helper functions -----
def rate_limited(user_id: int) -> bool:
    """Check if user is still in the cooldown period."""
    now = time.time()
    if user_id in last_attempt:
        if now - last_attempt[user_id] < RATE_LIMIT:
            return True
    return False

def update_rate_limit(user_id: int):
    last_attempt[user_id] = time.time()

async def cleanup_user(user_id: int):
    """Disconnect client and remove all temporary data."""
    data = user_data.pop(user_id, None)
    if data and "client" in data:
        try:
            await data["client"].disconnect()
        except Exception:
            pass

async def check_rate_limit(update: Update, user_id: int) -> bool:
    """Return False if rate‑limited (and send a message), else True."""
    if rate_limited(user_id):
        remaining = int(RATE_LIMIT - (time.time() - last_attempt[user_id]))
        if update.callback_query:
            await update.callback_query.message.reply_text(
                f"⏳ Please wait {remaining} seconds before starting a new action."
            )
        else:
            await update.message.reply_text(
                f"⏳ Please wait {remaining} seconds before starting a new action."
            )
        return False
    update_rate_limit(user_id)
    return True


# ----- /start handler (menu) -----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("🔑 Session Generator", callback_data="session"),
            InlineKeyboardButton("🗑️ Delete Account", callback_data="delete"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome! Choose an option:",
        reply_markup=reply_markup
    )


# ----- Help -----
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "**Session Generator Bot**\n\n"
        "I can do two things:\n"
        "1. **Generate a user session string** (for Telethon / Pyrogram)\n"
        "2. **Permanently delete your Telegram account**\n\n"
        "**How to use:**\n"
        "- Send /start to see the menu.\n"
        "- You'll need your `api_id` and `api_hash` from https://my.telegram.org\n"
        "- I never store any of your data.\n"
        "- Each action has a 5‑minute cooldown to prevent abuse."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ======================================================================
# SESSION GENERATOR FLOW
# ======================================================================
async def session_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not await check_rate_limit(update, user_id):
        return ConversationHandler.END

    await cleanup_user(user_id)

    await query.message.reply_text(
        "**Session Generator**\n\n"
        "Step 1/3: Send your **API ID** (integer).\n"
        "Get it at https://my.telegram.org\n"
        "/cancel to stop."
    )
    return S_API_ID

async def s_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    try:
        api_id = int(text)
        if api_id <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid API ID. It must be a positive integer.")
        return S_API_ID

    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]["api_id"] = api_id
    await update.message.reply_text("✅ Got it. Now send your **API Hash** (string).")
    return S_API_HASH

async def s_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    api_hash = update.message.text.strip()
    if not api_hash:
        await update.message.reply_text("❌ API Hash cannot be empty.")
        return S_API_HASH

    data = user_data.get(user_id, {})
    data["api_hash"] = api_hash
    await update.message.reply_text(
        "✅ Now send your **phone number** (international format, e.g., +1234567890)."
    )
    return S_PHONE

async def s_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = update.message.text.strip()
    data = user_data.get(user_id, {})
    if not data or "api_id" not in data or "api_hash" not in data:
        await update.message.reply_text("Session expired. /start again.")
        return ConversationHandler.END

    if not phone.startswith("+"):
        await update.message.reply_text("❌ Phone must start with '+' and include country code.")
        return S_PHONE

    try:
        client = TelegramClient(StringSession(), data["api_id"], data["api_hash"])
        await client.connect()
    except Exception as e:
        await update.message.reply_text(f"❌ Connection error: {e}")
        await cleanup_user(user_id)
        return ConversationHandler.END

    try:
        sent_code = await client.send_code_request(phone)
    except Exception as e:
        await client.disconnect()
        await update.message.reply_text(f"❌ Failed to send code: {e}")
        await cleanup_user(user_id)
        return ConversationHandler.END

    data["phone"] = phone
    data["client"] = client
    data["phone_code_hash"] = sent_code.phone_code_hash
    user_data[user_id] = data

    await update.message.reply_text(
        "📲 Verification code sent! Please enter the code.\n\n"
        "⚠️ **To avoid security blocks, DO NOT send the code as plain numbers.**\n"
        "Add a few extra characters, e.g.: `-2-1-8-0-7` or `2a1b8c0d7` – I’ll extract the digits automatically."
    )
    return S_CODE

async def s_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Extract digits only – bypasses code‑sharing detection
    code = ''.join(filter(str.isdigit, update.message.text))
    if not code:
        await update.message.reply_text(
            "❌ I need the numeric code. Try adding it with some extra characters (e.g., -2-1-8-0-7)."
        )
        return S_CODE

    data = user_data.get(user_id)
    if not data or "client" not in data:
        await update.message.reply_text("Session expired. /start again.")
        return ConversationHandler.END

    client: TelegramClient = data["client"]
    phone = data["phone"]
    phone_code_hash = data["phone_code_hash"]

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        await update.message.reply_text("🔐 Two‑step verification enabled. Enter your password.")
        return S_PASSWORD
    except PhoneCodeExpiredError:
        await client.disconnect()
        user_data.pop(user_id, None)
        await update.message.reply_text(
            "⏰ The verification code has expired.\n"
            "Please /start again and enter the new code as soon as you receive it."
        )
        return ConversationHandler.END
    except PhoneCodeInvalidError:
        await client.disconnect()
        user_data.pop(user_id, None)
        await update.message.reply_text(
            "❌ The verification code you entered is invalid.\n"
            "Please /start again and double‑check the code."
        )
        return ConversationHandler.END
    except Exception as e:
        await client.disconnect()
        user_data.pop(user_id, None)
        await update.message.reply_text(f"❌ Login failed: {e}")
        return ConversationHandler.END

    session_str = client.session.save()
    await client.disconnect()
    user_data.pop(user_id, None)

    logger.info(f"User {user_id} generated a session string.")
    await update.message.reply_text(
        "✅ Session string generated! Keep it safe:\n\n"
        f"`{session_str}`\n\n"
        "**Do not share it with anyone.** It gives full access to your account.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def s_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    password = update.message.text.strip()
    data = user_data.get(user_id)
    if not data or "client" not in data:
        await update.message.reply_text("Session expired. /start again.")
        return ConversationHandler.END

    client: TelegramClient = data["client"]
    try:
        await client.sign_in(password=password)
    except Exception as e:
        await client.disconnect()
        user_data.pop(user_id, None)
        await update.message.reply_text(f"❌ Login failed: {e}")
        return ConversationHandler.END

    session_str = client.session.save()
    await client.disconnect()
    user_data.pop(user_id, None)

    logger.info(f"User {user_id} generated a session string (2FA).")
    await update.message.reply_text(
        "✅ Session string generated! Keep it safe:\n\n"
        f"`{session_str}`\n\n"
        "**Never share it.**",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ======================================================================
# ACCOUNT DELETION FLOW
# ======================================================================
async def delete_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if not await check_rate_limit(update, user_id):
        return ConversationHandler.END

    await cleanup_user(user_id)

    await query.message.reply_text(
        "⚠️ **Account Deletion**\n\n"
        "This will **permanently delete** your Telegram account.\n"
        "You cannot undo this.\n\n"
        "To proceed, I will need to log you in.\n"
        "Step 1/3: Send your **API ID**.\n"
        "/cancel to stop."
    )
    return D_API_ID

async def d_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    try:
        api_id = int(text)
        if api_id <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid API ID. Positive integer required.")
        return D_API_ID

    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]["api_id"] = api_id
    await update.message.reply_text("✅ Got it. Now send your **API Hash**.")
    return D_API_HASH

async def d_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    api_hash = update.message.text.strip()
    if not api_hash:
        await update.message.reply_text("❌ API Hash cannot be empty.")
        return D_API_HASH

    data = user_data.get(user_id, {})
    data["api_hash"] = api_hash
    await update.message.reply_text(
        "✅ Now send your **phone number** (international format, e.g., +1234567890)."
    )
    return D_PHONE

async def d_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone = update.message.text.strip()
    data = user_data.get(user_id, {})
    if not data or "api_id" not in data or "api_hash" not in data:
        await update.message.reply_text("Session expired. /start again.")
        return ConversationHandler.END

    if not phone.startswith("+"):
        await update.message.reply_text("❌ Phone must start with '+' and include country code.")
        return D_PHONE

    try:
        client = TelegramClient(StringSession(), data["api_id"], data["api_hash"])
        await client.connect()
    except Exception as e:
        await update.message.reply_text(f"❌ Connection error: {e}")
        await cleanup_user(user_id)
        return ConversationHandler.END

    try:
        sent_code = await client.send_code_request(phone)
    except Exception as e:
        await client.disconnect()
        await update.message.reply_text(f"❌ Failed to send code: {e}")
        await cleanup_user(user_id)
        return ConversationHandler.END

    data["phone"] = phone
    data["client"] = client
    data["phone_code_hash"] = sent_code.phone_code_hash
    user_data[user_id] = data

    await update.message.reply_text(
        "📲 Verification code sent! Please enter the code.\n\n"
        "⚠️ **To avoid security blocks, DO NOT send the code as plain numbers.**\n"
        "Add a few extra characters, e.g.: `-2-1-8-0-7` or `2a1b8c0d7` – I’ll extract the digits automatically."
    )
    return D_CODE

async def d_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Extract digits only – bypasses code‑sharing detection
    code = ''.join(filter(str.isdigit, update.message.text))
    if not code:
        await update.message.reply_text(
            "❌ I need the numeric code. Try adding it with some extra characters (e.g., -2-1-8-0-7)."
        )
        return D_CODE

    data = user_data.get(user_id)
    if not data or "client" not in data:
        await update.message.reply_text("Session expired. /start again.")
        return ConversationHandler.END

    client: TelegramClient = data["client"]
    phone = data["phone"]
    phone_code_hash = data["phone_code_hash"]

    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        await update.message.reply_text("🔐 Two‑step verification enabled. Enter your password.")
        return D_PASSWORD
    except PhoneCodeExpiredError:
        await client.disconnect()
        user_data.pop(user_id, None)
        await update.message.reply_text(
            "⏰ The verification code has expired.\n"
            "Please /start again and enter the new code as soon as you receive it."
        )
        return ConversationHandler.END
    except PhoneCodeInvalidError:
        await client.disconnect()
        user_data.pop(user_id, None)
        await update.message.reply_text(
            "❌ The verification code you entered is invalid.\n"
            "Please /start again and double‑check the code."
        )
        return ConversationHandler.END
    except Exception as e:
        await client.disconnect()
        user_data.pop(user_id, None)
        await update.message.reply_text(f"❌ Login failed: {e}")
        return ConversationHandler.END

    # Login succeeded -> ask for final confirmation
    await update.message.reply_text(
        "☢️ **FINAL WARNING**\n"
        "Type `YES` (all caps) to permanently delete your account.\n"
        "Any other message will cancel the deletion.\n\n"
        "/cancel to stop."
    )
    return D_CONFIRM

async def d_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    data = user_data.get(user_id)

    if not data or "client" not in data:
        await update.message.reply_text("Session expired. /start again.")
        return ConversationHandler.END

    client: TelegramClient = data["client"]

    if text == "YES":
        try:
            await client.delete_account(reason="User requested deletion via bot")
            await client.disconnect()
            user_data.pop(user_id, None)
            await update.message.reply_text("✅ Your account has been deleted. Goodbye.")
        except Exception as e:
            await client.disconnect()
            user_data.pop(user_id, None)
            await update.message.reply_text(f"❌ Deletion failed: {e}")
    else:
        await client.disconnect()
        user_data.pop(user_id, None)
        await update.message.reply_text("❎ Account deletion cancelled.")

    return ConversationHandler.END

async def d_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    password = update.message.text.strip()
    data = user_data.get(user_id)
    if not data or "client" not in data:
        await update.message.reply_text("Session expired. /start again.")
        return ConversationHandler.END

    client: TelegramClient = data["client"]
    try:
        await client.sign_in(password=password)
    except Exception as e:
        await client.disconnect()
        user_data.pop(user_id, None)
        await update.message.reply_text(f"❌ Login failed: {e}")
        return ConversationHandler.END

    # After password, ask for final confirmation
    await update.message.reply_text(
        "☢️ **FINAL WARNING**\n"
        "Type `YES` (all caps) to permanently delete your account.\n"
        "Any other message will cancel the deletion.\n\n"
        "/cancel to stop."
    )
    return D_CONFIRM


# ----- Universal cancel -----
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await cleanup_user(user_id)
    await update.message.reply_text("❎ Cancelled. All temporary data removed.")
    return ConversationHandler.END


# ----- Main -----
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Session generator conversation
    session_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(session_entry, pattern="^session$")],
        states={
            S_API_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, s_api_id)],
            S_API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, s_api_hash)],
            S_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, s_phone)],
            S_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, s_code)],
            S_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, s_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Account deletion conversation
    delete_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(delete_entry, pattern="^delete$")],
        states={
            D_API_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, d_api_id)],
            D_API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, d_api_hash)],
            D_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, d_phone)],
            D_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, d_code)],
            D_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, d_password)],
            D_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, d_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Add handlers in order
    app.add_handler(CommandHandler("start", start))
    app.add_handler(session_conv)
    app.add_handler(delete_conv)
    app.add_handler(CommandHandler("help", help_command))

    logger.info("Bot is running with session generator and account deletion...")
    app.run_polling()


if __name__ == "__main__":
    main()
