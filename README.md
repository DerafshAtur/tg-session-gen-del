# Telegram Utility Bot

A public Telegram bot that helps users generate user‑client session strings (for Telethon/Pyrogram) and permanently delete Telegram accounts.

No personal data is ever stored – everything is held in server memory only during the active session and wiped immediately afterward.

## ✨ Features

- 🔑 Session generator – returns a `StringSession`
- 🗑️ Account deletion – permanent account removal with confirmation
- 🔒 Fully private – never logs phone numbers, passwords, or session strings
- ⏳ Rate‑limited – 5‑minute cooldown per user
- 🛠️ Easy to self‑host

## 📋 What you’ll need

1. A **Telegram bot token** from [@BotFather](https://t.me/BotFather)
2. A cloud server or platform that can run Python 24/7
3. The ability to set **environment variables**

## 🚀 Quick self‑host guide

1. **Clone the repo**  
   `git clone https://github.com/DerafshAtur/tg-session-gen-del.git`

2. **Install dependencies**  
   `pip install -r requirements.txt`

3. **Set the environment variable**  
   Create a variable named `BOT_TOKEN` with your bot’s token.  
   **Never hard‑code your token in the source files.**

4. **Run the bot**  
   `python main.py`

5. **(Optional)** Deploy to a cloud platform that supports Python (set the `BOT_TOKEN` variable there and start the same script).

The bot uses long‑polling – no webhooks required.

## ⚠️ Security

- **Never share your session string** – it gives full access to your account.
- This bot does **not** store, log, or transmit any user credentials. All data is held in volatile memory and erased immediately after the operation finishes or the user cancels.
- Use at your own risk. Self‑hosting is recommended so you control the environment completely.

## 📁 Files

| File | Purpose |
|------|---------|
| `main.py` | Bot source code |
| `requirements.txt` | Python dependencies |
| `.env.example` | Example environment variable template |
| `README.md` | This file |

## 📜 License

MIT – free for any use.
