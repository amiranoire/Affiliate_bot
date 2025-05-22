#!/usr/bin/env python3
import os
import sqlite3
import logging
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# --- Configuration ---
load_dotenv()  # Load environment variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # Optional
DATABASE_NAME = "affiliate_bot.db"

# Validate configuration
if not TOKEN:
    raise ValueError("Missing TELEGRAM_BOT_TOKEN in .env file!")

# --- Database Setup ---
def init_db():
    """Initialize database tables"""
    with sqlite3.connect(DATABASE_NAME) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS messages
                     (id INTEGER PRIMARY KEY,
                     user_id INTEGER,
                     username TEXT,
                     chat_id INTEGER,
                     message_id INTEGER,
                     timestamp DATETIME,
                     replied_to INTEGER DEFAULT NULL)''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS response_times
                     (message_id INTEGER PRIMARY KEY,
                     original_message_id INTEGER,
                     response_time_seconds INTEGER)''')

# --- Tracking Functions ---
async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log messages and track response times"""
    try:
        msg = update.message
        user = msg.from_user
        
        with sqlite3.connect(DATABASE_NAME) as conn:
            # Log message
            conn.execute('''INSERT INTO messages 
                         (user_id, username, chat_id, message_id, timestamp) 
                         VALUES (?, ?, ?, ?, ?)''',
                      (user.id, user.username, msg.chat_id, msg.message_id, datetime.now()))
            
            # Track replies
            if msg.reply_to_message:
                response_time = (msg.date - msg.reply_to_message.date).total_seconds()
                conn.execute('''INSERT INTO response_times 
                               VALUES (?, ?, ?)''',
                            (msg.message_id, msg.reply_to_message.message_id, response_time))
                
                # Alert slow responses (>2h)
                if response_time > 7200:
                    await msg.reply_text(
                        f"⏰ Reminder: {user.mention_markdown()} took "
                        f"{int(response_time/3600)}h to reply",
                        parse_mode='Markdown'
                    )
            
            conn.commit()
            
    except Exception as e:
        logging.error(f"Tracking error: {e}")
        if ADMIN_CHAT_ID:
            await context.bot.send_message(
                chat_id=int(ADMIN_CHAT_ID),
                text=f"⚠️ Tracking Error: {e}"
            )

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message"""
    await update.message.reply_text(
        "🤖 Affiliate Performance Assistant\n\n"
        "I help optimize communication between partners.\n"
        "• I track response times\n"
        "• Provide activity reports\n"
        "Type /stats to see your metrics"
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User statistics"""
    try:
        user = update.message.from_user
        
        with sqlite3.connect(DATABASE_NAME) as conn:
            # Message count
            msg_count = conn.execute('''SELECT COUNT(*) 
                                      FROM messages 
                                      WHERE user_id = ?''', 
                                   (user.id,)).fetchone()[0]
            
            # Avg response time (hours)
            avg_response = conn.execute('''SELECT AVG(response_time_seconds)/3600 
                                         FROM response_times rt
                                         JOIN messages m ON rt.message_id = m.message_id
                                         WHERE m.user_id = ?''',
                                      (user.id,)).fetchone()[0] or 0
            
            await update.message.reply_text(
                f"📊 Your Stats:\n"
                f"• Messages: {msg_count}\n"
                f"• Avg Response: {avg_response:.1f}h\n\n"
                f"Tip: Replies under 2 hours improve partnerships!",
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logging.error(f"Stats error: {e}")
        await update.message.reply_text("🔧 Couldn't fetch stats. Try again later.")

# --- Main Application ---
def main():
    """Start the bot"""
    # Initialize
    init_db()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create bot
    application = Application.builder().token(TOKEN).build()
    
    # Add handlers
    handlers = [
        CommandHandler("start", start),
        CommandHandler("stats", stats),
        MessageHandler(filters.TEXT & ~filters.COMMAND, track_message)
    ]
    for handler in handlers:
        application.add_handler(handler)

    # Start polling
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()