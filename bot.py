#!/usr/bin/env python3
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Optional

from telegram import Update, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from apscheduler.schedulers.background import BackgroundScheduler

from config import config

# --- Database Setup ---
def init_db():
    """Initialize database with enhanced employee tracking schema"""
    with sqlite3.connect(config.DATABASE_NAME) as conn:
        # Messages table with additional tracking fields
        conn.execute('''CREATE TABLE IF NOT EXISTS messages
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER NOT NULL,
                     username TEXT,
                     full_name TEXT,
                     chat_id INTEGER NOT NULL,
                     message_id INTEGER NOT NULL UNIQUE,
                     text TEXT,
                     timestamp DATETIME NOT NULL,
                     replied_to_message_id INTEGER DEFAULT NULL,
                     was_answered BOOLEAN DEFAULT FALSE,
                     is_question BOOLEAN DEFAULT FALSE)''')

        # New table to store individual response metrics
        conn.execute('''CREATE TABLE IF NOT EXISTS response_metrics
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     reply_message_id INTEGER NOT NULL UNIQUE,
                     original_message_id INTEGER NOT NULL,
                     responder_user_id INTEGER NOT NULL,
                     response_duration_seconds REAL NOT NULL,
                     timestamp DATETIME NOT NULL)''')

        # Employee activity summary - populated by scheduled job
        conn.execute('''CREATE TABLE IF NOT EXISTS employee_activity
                     (user_id INTEGER NOT NULL,
                     date DATE NOT NULL,
                     message_count INTEGER DEFAULT 0,
                     avg_response_time REAL DEFAULT 0.0,
                     questions_asked INTEGER DEFAULT 0,
                     questions_answered INTEGER DEFAULT 0,
                     PRIMARY KEY (user_id, date))''')

        # Unanswered questions tracking
        conn.execute('''CREATE TABLE IF NOT EXISTS unanswered_questions
                     (message_id INTEGER PRIMARY KEY,
                     chat_id INTEGER NOT NULL,
                     user_id INTEGER NOT NULL,
                     question_text TEXT NOT NULL,
                     timestamp DATETIME NOT NULL,
                     reminded BOOLEAN DEFAULT FALSE)''')

        # NEW TABLE: Store employee IDs
        conn.execute('''CREATE TABLE IF NOT EXISTS employees
                     (user_id INTEGER PRIMARY KEY,
                     username TEXT,
                     full_name TEXT,
                     added_by TEXT,
                     added_timestamp DATETIME NOT NULL)''')

        conn.commit()

# Helper to check if a user is an employee
def is_employee(user_id: int) -> bool:
    with sqlite3.connect(config.DATABASE_NAME) as conn:
        cursor = conn.execute("SELECT 1 FROM employees WHERE user_id = ?", (user_id,))
        return cursor.fetchone() is not None

# --- Scheduled Jobs (Placeholder Functions) ---
async def check_unanswered_questions(context: ContextTypes.DEFAULT_TYPE):
    """Periodically checks for unanswered questions and sends reminders."""
    logging.info("Running check_unanswered_questions job...")
    now = datetime.now()
    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            # Select unanswered questions older than the alert threshold and not yet reminded
            cursor = conn.execute('''SELECT message_id, chat_id, user_id, question_text, timestamp
                                     FROM unanswered_questions
                                     WHERE reminded = FALSE
                                     AND (JULIANDAY(?) - JULIANDAY(timestamp)) * 86400 > ?''',
                                  (now, config.UNANSWERED_ALERT_THRESHOLD))

            unanswered_list = cursor.fetchall()

            for msg_id, chat_id, user_id, question_text, timestamp_str in unanswered_list:
                # Convert timestamp_str back to datetime object for calculation if needed, or just for display
                question_timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
                
                try:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"🚨 Reminder: This question has been unanswered for over {config.UNANSWERED_ALERT_THRESHOLD / 3600:.1f} hours:\n\n"
                             f"Original message (ID: {msg_id}): \"{question_text}\"\n"
                             f"Please provide a response!",
                        reply_to_message_id=msg_id
                    )
                    # Mark as reminded
                    conn.execute("UPDATE unanswered_questions SET reminded = TRUE WHERE message_id = ?", (msg_id,))
                    conn.commit()
                    logging.info(f"Sent unanswered reminder for message {msg_id} in chat {chat_id}")
                except Exception as e:
                    logging.error(f"Failed to send reminder for message {msg_id}: {e}")

    except Exception as e:
        logging.error(f"Error in check_unanswered_questions job: {e}")

async def update_employee_activity_summary(context: ContextTypes.DEFAULT_TYPE):
    """Daily job to summarize employee activity into employee_activity table."""
    logging.info("Running update_employee_activity_summary job...")
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            # Calculate message count and questions asked
            messages_data = conn.execute('''
                SELECT
                    user_id,
                    COUNT(id) AS message_count,
                    SUM(CASE WHEN is_question THEN 1 ELSE 0 END) AS questions_asked
                FROM messages
                WHERE DATE(timestamp) = ?
                GROUP BY user_id
            ''', (today,)).fetchall()

            # Calculate average response time and questions answered
            response_data = conn.execute('''
                SELECT
                    responder_user_id,
                    AVG(response_duration_seconds) AS avg_resp_time,
                    COUNT(reply_message_id) AS questions_answered
                FROM response_metrics
                WHERE DATE(timestamp) = ?
                GROUP BY responder_user_id
            ''', (today,)).fetchall()

            # Prepare data for upsert (INSERT or UPDATE)
            activity_updates = {}
            for user_id, msg_count, q_asked in messages_data:
                activity_updates[user_id] = {
                    'message_count': msg_count,
                    'questions_asked': q_asked,
                    'avg_response_time': 0.0,
                    'questions_answered': 0
                }

            for user_id, avg_resp_time, q_answered in response_data:
                if user_id not in activity_updates:
                    activity_updates[user_id] = {
                        'message_count': 0, 'questions_asked': 0
                    }
                activity_updates[user_id]['avg_response_time'] = avg_resp_time if avg_resp_time is not None else 0.0
                activity_updates[user_id]['questions_answered'] = q_answered


            for user_id, data in activity_updates.items():
                conn.execute('''
                    INSERT OR REPLACE INTO employee_activity
                    (user_id, date, message_count, avg_response_time, questions_asked, questions_answered)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (user_id, today, data['message_count'], data['avg_response_time'],
                      data['questions_asked'], data['questions_answered']))

            conn.commit()
            logging.info(f"Employee activity summary updated for {today}")

    except Exception as e:
        logging.error(f"Error in update_employee_activity_summary job: {e}")


# --- Message Processing ---
async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced message tracking with employee monitoring features"""
    try:
        msg = update.message
        user = msg.from_user

        username = user.username if user.username else f"id_{user.id}"
        full_name = user.full_name if user.full_name else f"User {user.id}"

        with sqlite3.connect(config.DATABASE_NAME) as conn:
            # Store the message
            conn.execute('''INSERT INTO messages
                         (user_id, username, full_name, chat_id,
                          message_id, text, timestamp)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (user.id, username, full_name,
                       msg.chat_id, msg.message_id, msg.text, datetime.now()))

            # Check if message is a question (simple keyword check)
            is_question = any(q in msg.text.lower() for q in ['?', 'how to', 'help with', 'can i', 'do you know'])
            if is_question:
                conn.execute('''UPDATE messages SET is_question = TRUE
                             WHERE message_id = ?''', (msg.message_id,))

                # Track unanswered questions
                conn.execute('''INSERT INTO unanswered_questions
                             (message_id, chat_id, user_id, question_text, timestamp)
                             VALUES (?, ?, ?, ?, ?)''',
                          (msg.message_id, msg.chat_id, user.id, msg.text, datetime.now()))

            # Handle replies
            if msg.reply_to_message:
                original_msg_id = msg.reply_to_message.message_id
                original_msg_date = msg.reply_to_message.date
                response_time_seconds = (msg.date - original_msg_date).total_seconds()

                # Store response metrics
                conn.execute('''INSERT INTO response_metrics
                             (reply_message_id, original_message_id, responder_user_id,
                              response_duration_seconds, timestamp)
                             VALUES (?, ?, ?, ?, ?)''',
                          (msg.message_id, original_msg_id, user.id,
                           response_time_seconds, datetime.now()))

                # Mark original message as answered in the messages table
                conn.execute('''UPDATE messages SET was_answered = TRUE
                             WHERE message_id = ?''',
                          (original_msg_id,))

                # Remove from unanswered_questions table if it was a question and now answered
                conn.execute('''DELETE FROM unanswered_questions
                             WHERE message_id = ?''',
                          (original_msg_id,))

                # Alert slow responses (if it was a question and got a slow reply)
                original_message_is_question = conn.execute("SELECT is_question FROM messages WHERE message_id = ?", (original_msg_id,)).fetchone()
                if original_message_is_question and original_message_is_question[0] and response_time_seconds > config.RESPONSE_ALERT_THRESHOLD:
                    await notify_slow_response(msg, response_time_seconds)

            conn.commit()

    except Exception as e:
        logging.error(f"Tracking error: {e}", exc_info=True)
        if config.ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=config.ADMIN_CHAT_ID,
                    text=f"⚠️ Tracking Error in chat {msg.chat_id} (user {user.id}): {e}"
                )
            except Exception as admin_e:
                logging.error(f"Failed to send admin error notification: {admin_e}", exc_info=True)


async def notify_slow_response(message: Message, response_time: float):
    """Notify chat about slow response"""
    hours = response_time / 3600
    try:
        await message.reply_text(
            f"⏰ Slow Response Notice\n"
            f"Reply took {hours:.1f} hours\n"
            f"Team target: <{config.RESPONSE_ALERT_THRESHOLD / 3600:.1f}> hours",
            reply_to_message_id=message.message_id
        )
    except Exception as e:
        logging.error(f"Failed to send slow response notification: {e}", exc_info=True)


# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced welcome message"""
    await update.message.reply_text(
        "👔 Employee Productivity Tracker\n\n"
        "I help monitor team communication:\n"
        "• Message activity tracking\n"
        "• Response time analysis\n"
        "• Question resolution monitoring\n\n"
        "Commands:\n"
        "/stats - Your personal metrics\n"
        "/teamstats - Team overview (managers only)\n"
        "/unanswered - List pending questions\n"
        "/add_employee - Add an employee (admin only)" # Added to start message
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enhanced employee statistics"""
    try:
        user = update.message.from_user

        with sqlite3.connect(config.DATABASE_NAME) as conn:
            # Get basic activity from messages table
            # Calculate total messages by this user, and questions asked by this user
            stats_messages = conn.execute('''SELECT
                                  COUNT(id) as total_messages,
                                  SUM(CASE WHEN is_question THEN 1 ELSE 0 END) as questions_asked
                                  FROM messages
                                  WHERE user_id = ?''',
                               (user.id,)).fetchone()

            total_messages = stats_messages[0] if stats_messages else 0
            questions_asked = stats_messages[1] if stats_messages else 0

            # Get replies sent by this user, and their response times from response_metrics
            response_stats = conn.execute('''SELECT
                                           COUNT(reply_message_id) as replies_sent,
                                           AVG(response_duration_seconds) as avg_response_s,
                                           MAX(response_duration_seconds) as max_response_s
                                           FROM response_metrics
                                           WHERE responder_user_id = ?''',
                                        (user.id,)).fetchone()

            replies_sent = response_stats[0] if response_stats else 0
            avg_response_s = response_stats[1] if response_stats and response_stats[1] is not None else 0
            max_response_s = response_stats[2] if response_stats and response_stats[2] is not None else 0

            # Convert seconds to hours for display
            avg_response_h = avg_response_s / 3600
            max_response_h = max_response_s / 3600

            await update.message.reply_text(
                f"📊 Your Stats\n"
                f"• Total Messages: {total_messages}\n"
                f"• Questions Asked: {questions_asked}\n"
                f"• Replies Sent: {replies_sent}\n"
                f"• Avg Response Time: {avg_response_h:.1f}h\n"
                f"• Longest Response: {max_response_h:.1f}h",
                parse_mode='Markdown'
            )

    except Exception as e:
        logging.error(f"Stats error for user {update.message.from_user.id}: {e}", exc_info=True)
        await update.message.reply_text("🔧 Couldn't fetch your stats. An error occurred.")


async def teamstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Team overview for managers."""
    # Check if the user is an admin
    if config.ADMIN_CHAT_ID and update.message.from_user.id != config.ADMIN_CHAT_ID:
        await update.message.reply_text("🚫 You are not authorized to view team statistics.")
        return

    try:
        # Fetch data from employee_activity table
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            # For simplicity, let's get activity for the last 7 days for all users
            seven_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

            # Aggregate stats for each user over the last 7 days
            team_summary = conn.execute(f'''
                SELECT
                    user_id,
                    SUM(message_count) AS total_messages,
                    AVG(avg_response_time) AS average_response_time,
                    SUM(questions_asked) AS total_questions_asked,
                    SUM(questions_answered) AS total_questions_answered
                FROM employee_activity
                WHERE date >= ?
                GROUP BY user_id
                ORDER BY total_messages DESC
            ''', (seven_days_ago,)).fetchall()

            if not team_summary:
                await update.message.reply_text("No team activity found for the last 7 days.")
                return

            response_text = "📊 Team Performance (Last 7 Days):\n\n"
            for user_id, msg_count, avg_resp_s, q_asked, q_answered in team_summary:
                user_info = conn.execute("SELECT username, full_name FROM messages WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
                user_display_name = user_info[1] if user_info and user_info[1] else f"User {user_id}"

                avg_resp_h = (avg_resp_s / 3600) if avg_resp_s else 0.0
                response_text += (
                    f"• {user_display_name} (ID: {user_id}):\n"
                    f"  - Messages: {msg_count}\n"
                    f"  - Questions Asked: {q_asked}\n"
                    f"  - Questions Answered: {q_answered}\n"
                    f"  - Avg. Response: {avg_resp_h:.1f}h\n\n"
                )

            await update.message.reply_text(response_text, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Team Stats error: {e}", exc_info=True)
        await update.message.reply_text("🔧 Couldn't fetch team stats. An error occurred.")


async def unanswered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List pending unanswered questions."""
    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            cursor = conn.execute('''SELECT message_id, chat_id, user_id, question_text, timestamp
                                     FROM unanswered_questions
                                     ORDER BY timestamp ASC''')

            unanswered_list = cursor.fetchall()

            if not unanswered_list:
                await update.message.reply_text("🎉 No unanswered questions found! Great job, team!")
                return

            response_text = "📚 Unanswered Questions:\n\n"
            for msg_id, chat_id, user_id, question_text, timestamp_str in unanswered_list:
                sender_info = conn.execute("SELECT full_name FROM messages WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
                sender_name = sender_info[0] if sender_info else f"User {user_id}"

                chat_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg_id}" if str(chat_id).startswith('-100') else f"Chat ID: {chat_id}"

                response_text += (
                    f"• From: {sender_name} ({timestamp_str})\n"
                    f"  Question: \"{question_text}\"\n"
                    f"  Link: {chat_link}\n\n"
                )

            await update.message.reply_text(response_text, parse_mode='Markdown', disable_web_page_preview=True)

    except Exception as e:
        logging.error(f"Unanswered questions error: {e}", exc_info=True)
        await update.message.reply_text("🔧 Couldn't fetch unanswered questions. An error occurred.")


async def add_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adds a user as an employee. Only for admins."""
    # Check if the user is an admin (you!)
    if update.message.from_user.id != config.ADMIN_CHAT_ID:
        await update.message.reply_text("🚫 You are not authorized to add employees.")
        return

    if not context.args:
        await update.message.reply_text("Please provide the user ID or username of the employee to add. E.g., `/add_employee 123456789` or `/add_employee @username`")
        return

    # Try to parse the input
    user_identifier = context.args[0]
    target_user_id = None
    target_username = None

    try:
        # If it's a numeric ID
        target_user_id = int(user_identifier)
    except ValueError:
        # If it's a username
        target_username = user_identifier.lstrip('@')
        # We need to look up the user ID from the messages table
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            cursor = conn.execute("SELECT user_id, full_name FROM messages WHERE username = ? LIMIT 1", (target_username,))
            result = cursor.fetchone()
            if result:
                target_user_id = result[0]
                target_full_name = result[1]
            else:
                await update.message.reply_text(f"Couldn't find a user with username '{target_username}' in my message history. Please try with their numeric user ID or ensure they've sent a message to the bot recently.")
                return

    if not target_user_id:
        await update.message.reply_text("Invalid user identifier provided.")
        return

    # If user ID was provided directly, we try to fetch full_name from history
    if not target_username: # means user_identifier was a numeric ID
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            cursor = conn.execute("SELECT username, full_name FROM messages WHERE user_id = ? LIMIT 1", (target_user_id,))
            result = cursor.fetchone()
            if result:
                target_username = result[0]
                target_full_name = result[1]
            else:
                target_username = f"id_{target_user_id}" # Fallback
                target_full_name = f"User {target_user_id}" # Fallback

    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            conn.execute('''INSERT OR IGNORE INTO employees (user_id, username, full_name, added_by, added_timestamp)
                         VALUES (?, ?, ?, ?, ?)''',
                      (target_user_id, target_username, target_full_name,
                       update.message.from_user.full_name, datetime.now()))
            conn.commit()

        # Check if row was actually inserted (not IGNOREd)
        if conn.changes > 0:
            await update.message.reply_text(f"✅ Employee {target_full_name} (ID: `{target_user_id}`) has been added.", parse_mode='Markdown')
            logging.info(f"Admin {update.message.from_user.id} added employee {target_user_id}.")
        else:
            await update.message.reply_text(f"Employee {target_full_name} (ID: `{target_user_id}`) is already in the list.")

    except Exception as e:
        logging.error(f"Error adding employee {target_user_id}: {e}", exc_info=True)
        await update.message.reply_text(f"🔧 An error occurred while trying to add the employee.")


# --- Main Application ---
def main():
    """Start the enhanced bot"""
    # Initialize
    init_db()
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logging.getLogger('apscheduler').setLevel(logging.DEBUG)

    # Create bot
    application = Application.builder().token(config.TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("teamstats", teamstats))
    application.add_handler(CommandHandler("unanswered", unanswered))
    application.add_handler(CommandHandler("add_employee", add_employee)) # ADDED: New command handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_message))

    # Setup APScheduler
    scheduler = BackgroundScheduler()
    # Schedule the daily activity summary update
    scheduler.add_job(update_employee_activity_summary, 'interval', hours=24, args=[application])
    # Schedule the unanswered questions check (e.g., every hour)
    scheduler.add_job(check_unanswered_questions, 'interval', hours=1, args=[application])

    scheduler.start()

    # Start polling
    logging.info("Bot is starting polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    logging.info("Bot polling stopped.")

if __name__ == "__main__":
    main()
    