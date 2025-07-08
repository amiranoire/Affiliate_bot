#!/usr/bin/env python3
import logging
import sqlite3
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable, Any
from functools import wraps

from telegram import Update, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import config

# --- Decorators ---
def is_admin(func: Callable) -> Callable:
    """Decorator to restrict command usage to the ADMIN_CHAT_ID."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any) -> None:
        if config.ADMIN_CHAT_ID and update.effective_user.id != config.ADMIN_CHAT_ID:
            await update.message.reply_text("🚫 You are not authorized to use this command.")
            logging.warning(f"Unauthorized access attempt to {func.__name__} by user {update.effective_user.id}")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def run_async(func: Callable) -> Callable:
    """Decorator to ensure handler runs asynchronously, preventing blocking."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any) -> None:
        return await func(update, context, *args, **kwargs)
    return wrapper

# --- Database Setup ---
def init_db():
    """Initialize database with enhanced employee tracking schema"""
    with sqlite3.connect(config.DATABASE_NAME) as conn:
        # Messages table to store all incoming and outgoing messages
        conn.execute('''CREATE TABLE IF NOT EXISTS messages
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     user_id INTEGER NOT NULL,
                     username TEXT,
                     full_name TEXT,
                     chat_id INTEGER NOT NULL,
                     message_id INTEGER NOT NULL,
                     text TEXT,
                     timestamp DATETIME NOT NULL,
                     replied_to_message_id INTEGER DEFAULT NULL,
                     was_answered BOOLEAN DEFAULT FALSE,
                     is_question BOOLEAN DEFAULT FALSE,
                     UNIQUE(chat_id, message_id))''')

        # Table to store individual response metrics
        conn.execute('''CREATE TABLE IF NOT EXISTS response_metrics
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                     reply_message_id INTEGER NOT NULL,
                     original_message_id INTEGER NOT NULL,
                     responder_user_id INTEGER NOT NULL,
                     original_sender_user_id INTEGER NOT NULL,
                     chat_id INTEGER NOT NULL,
                     response_duration_seconds REAL NOT NULL,
                     timestamp DATETIME NOT NULL,
                     UNIQUE(chat_id, reply_message_id))''')

        # Table for daily employee activity summaries
        conn.execute('''CREATE TABLE IF NOT EXISTS employee_activity
                     (user_id INTEGER NOT NULL,
                     date DATE NOT NULL,
                     message_count INTEGER DEFAULT 0,
                     avg_response_time REAL DEFAULT 0.0,
                     questions_asked INTEGER DEFAULT 0,
                     questions_answered INTEGER DEFAULT 0,
                     PRIMARY KEY (user_id, date))''')

        # Unanswered questions tracking for PARTNER turns
        conn.execute('''CREATE TABLE IF NOT EXISTS unanswered_questions
                     (turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
                     chat_id INTEGER NOT NULL,
                     partner_user_id INTEGER NOT NULL,
                     last_partner_message_id INTEGER NOT NULL,
                     last_partner_message_text TEXT NOT NULL,
                     turn_start_timestamp DATETIME NOT NULL,
                     last_message_timestamp DATETIME NOT NULL,
                     reminded BOOLEAN DEFAULT FALSE,
                     UNIQUE(chat_id, partner_user_id))''')

        # Table to store employee IDs
        conn.execute('''CREATE TABLE IF NOT EXISTS employees
                     (user_id INTEGER PRIMARY KEY,
                     username TEXT,
                     full_name TEXT,
                     added_by TEXT,
                     added_timestamp DATETIME NOT NULL)''')

        # Add indexes for better performance
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_messages_user_timestamp 
                     ON messages(user_id, timestamp)''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_messages_chat_timestamp 
                     ON messages(chat_id, timestamp)''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_response_metrics_responder 
                     ON response_metrics(responder_user_id, timestamp)''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_unanswered_chat_partner 
                     ON unanswered_questions(chat_id, partner_user_id)''')

        conn.commit()

def is_employee(user_id: int) -> bool:
    """Checks if a given user ID is registered as an employee."""
    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            cursor = conn.execute("SELECT 1 FROM employees WHERE user_id = ?", (user_id,))
            return cursor.fetchone() is not None
    except Exception as e:
        logging.error(f"Error checking if user {user_id} is employee: {e}")
        return False

# --- Scheduled Jobs ---
async def check_unanswered_questions(context: ContextTypes.DEFAULT_TYPE):
    """Check for unanswered partner conversation turns and send reminders."""
    logging.info("Running check_unanswered_questions job...")
    now = datetime.now()
    
    if not config.ADMIN_CHAT_ID:
        logging.warning("ADMIN_CHAT_ID not configured, skipping unanswered questions check")
        return

    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            cursor = conn.execute('''
                SELECT turn_id, chat_id, partner_user_id, last_partner_message_id, 
                       last_partner_message_text, last_message_timestamp
                FROM unanswered_questions
                WHERE reminded = FALSE
                AND (JULIANDAY(?) - JULIANDAY(last_message_timestamp)) * 86400 > ?
            ''', (now, config.EMPLOYEE_RESPONSE_THRESHOLD_SECONDS))

            unanswered_turns = cursor.fetchall()

            for turn_id, chat_id, partner_user_id, last_msg_id, last_msg_text, last_msg_timestamp_str in unanswered_turns:
                try:
                    turn_timestamp = datetime.strptime(last_msg_timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
                    time_since_last_message = (now - turn_timestamp).total_seconds()
                    hours_since_last_message = time_since_last_message / 3600

                    # Get partner info
                    partner_info = conn.execute(
                        "SELECT username, full_name FROM messages WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1", 
                        (partner_user_id,)
                    ).fetchone()
                    
                    partner_display_name = partner_info[1] if partner_info and partner_info[1] else f"Partner {partner_user_id}"

                    # Create chat link
                    if str(chat_id).startswith('-100'):
                        chat_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{last_msg_id}"
                    else:
                        chat_link = f"Chat ID: {chat_id}"
                    
                    # Truncate message if too long
                    display_text = last_msg_text[:100] + "..." if len(last_msg_text) > 100 else last_msg_text
                    
                    await context.bot.send_message(
                        chat_id=config.ADMIN_CHAT_ID,
                        text=f"🚨 **Unanswered Partner Message**\n\n"
                             f"**From:** {partner_display_name} (ID: {partner_user_id})\n"
                             f"**Message:** \"{display_text}\"\n"
                             f"**Unanswered for:** {hours_since_last_message:.1f} hours\n"
                             f"**Link:** {chat_link}",
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    
                    # Mark as reminded
                    conn.execute("UPDATE unanswered_questions SET reminded = TRUE WHERE turn_id = ?", (turn_id,))
                    conn.commit()
                    
                    logging.info(f"Sent reminder for turn {turn_id} by partner {partner_user_id}")
                    
                    # Add small delay to avoid rate limiting
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logging.error(f"Failed to send reminder for turn {turn_id}: {e}")

    except Exception as e:
        logging.error(f"Error in check_unanswered_questions job: {e}")

async def update_employee_activity_summary(context: ContextTypes.DEFAULT_TYPE):
    """Daily job to summarize employee activity."""
    logging.info("Running update_employee_activity_summary job...")
    today = datetime.now().strftime('%Y-%m-%d')
    
    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            # Get message counts and questions asked
            messages_data = conn.execute('''
                SELECT
                    user_id,
                    COUNT(id) AS message_count,
                    SUM(CASE WHEN is_question THEN 1 ELSE 0 END) AS questions_asked
                FROM messages
                WHERE DATE(timestamp) = ? AND user_id IN (SELECT user_id FROM employees)
                GROUP BY user_id
            ''', (today,)).fetchall()

            # Get response metrics
            response_data = conn.execute('''
                SELECT
                    responder_user_id,
                    AVG(response_duration_seconds) AS avg_resp_time,
                    COUNT(reply_message_id) AS questions_answered
                FROM response_metrics
                WHERE DATE(timestamp) = ? AND responder_user_id IN (SELECT user_id FROM employees)
                GROUP BY responder_user_id
            ''', (today,)).fetchall()

            # Consolidate data
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
                    activity_updates[user_id] = {'message_count': 0, 'questions_asked': 0}
                activity_updates[user_id]['avg_response_time'] = avg_resp_time if avg_resp_time is not None else 0.0
                activity_updates[user_id]['questions_answered'] = q_answered

            # Update database
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
    """Track all messages and manage partner conversation turns."""
    try:
        msg = update.message
        user = msg.from_user

        if not msg.text:  # Skip non-text messages
            return

        username = user.username if user.username else None
        full_name = user.full_name if user.full_name else f"User {user.id}"

        with sqlite3.connect(config.DATABASE_NAME) as conn:
            # Store the message (with conflict handling)
            try:
                conn.execute('''INSERT INTO messages
                             (user_id, username, full_name, chat_id,
                              message_id, text, timestamp, replied_to_message_id)
                             VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                          (user.id, username, full_name, msg.chat_id, 
                           msg.message_id, msg.text, datetime.now(),
                           msg.reply_to_message.message_id if msg.reply_to_message else None))
            except sqlite3.IntegrityError:
                # Message already exists, skip processing
                return

            sender_is_employee = is_employee(user.id)

            # Handle partner messages (create/update unanswered turns)
            if not sender_is_employee:
                await handle_partner_message(conn, msg, user)

            # Handle employee replies (mark partner turns as answered)
            if sender_is_employee and msg.reply_to_message:
                await handle_employee_reply(conn, msg, user)

            # Track response metrics for all replies
            if msg.reply_to_message:
                await track_response_metrics(conn, msg, user)

            # Mark questions
            is_question = any(q in msg.text.lower() for q in ['?', 'how to', 'help with', 'can i', 'do you know'])
            if is_question:
                conn.execute('UPDATE messages SET is_question = TRUE WHERE chat_id = ? AND message_id = ?', 
                           (msg.chat_id, msg.message_id))

            conn.commit()

    except Exception as e:
        logging.error(f"Tracking error: {e}", exc_info=True)
        if config.ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=config.ADMIN_CHAT_ID,
                    text=f"⚠️ Tracking Error in chat {msg.chat_id}: {str(e)[:200]}..."
                )
            except Exception as admin_e:
                logging.error(f"Failed to send admin error notification: {admin_e}")

async def handle_partner_message(conn, msg, user):
    """Handle messages from partners (non-employees)."""
    try:
        # Check for existing unanswered turn
        cursor = conn.execute('''SELECT turn_id, last_message_timestamp
                                 FROM unanswered_questions
                                 WHERE chat_id = ? AND partner_user_id = ?''',
                              (msg.chat_id, user.id))
        
        existing_turn = cursor.fetchone()
        current_time = datetime.now()

        if existing_turn:
            turn_id, last_msg_timestamp_str = existing_turn
            last_msg_timestamp = datetime.strptime(last_msg_timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
            
            # Check if within conversation turn timeout
            if (current_time - last_msg_timestamp).total_seconds() < config.CONVERSATION_TURN_TIMEOUT_SECONDS:
                # Update existing turn
                conn.execute('''UPDATE unanswered_questions
                                 SET last_partner_message_id = ?,
                                     last_partner_message_text = ?,
                                     last_message_timestamp = ?,
                                     reminded = FALSE
                                 WHERE turn_id = ?''',
                              (msg.message_id, msg.text, current_time, turn_id))
                logging.info(f"Updated existing partner turn {turn_id} for user {user.id}")
            else:
                # Replace old turn with new one
                conn.execute('DELETE FROM unanswered_questions WHERE turn_id = ?', (turn_id,))
                conn.execute('''INSERT INTO unanswered_questions
                             (chat_id, partner_user_id, last_partner_message_id,
                              last_partner_message_text, turn_start_timestamp, last_message_timestamp)
                             VALUES (?, ?, ?, ?, ?, ?)''',
                          (msg.chat_id, user.id, msg.message_id,
                           msg.text, current_time, current_time))
                logging.info(f"Created new partner turn for user {user.id} (old turn expired)")
        else:
            # Create new turn
            conn.execute('''INSERT INTO unanswered_questions
                         (chat_id, partner_user_id, last_partner_message_id,
                          last_partner_message_text, turn_start_timestamp, last_message_timestamp)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (msg.chat_id, user.id, msg.message_id,
                       msg.text, current_time, current_time))
            logging.info(f"Created new partner turn for user {user.id}")
            
    except Exception as e:
        logging.error(f"Error handling partner message: {e}")

async def handle_employee_reply(conn, msg, user):
    """Handle replies from employees to mark partner turns as answered."""
    try:
        original_msg_id = msg.reply_to_message.message_id
        
        # Get the original message sender
        original_sender_info = conn.execute(
            "SELECT user_id FROM messages WHERE chat_id = ? AND message_id = ?", 
            (msg.chat_id, original_msg_id)
        ).fetchone()
        
        if original_sender_info:
            original_sender_user_id = original_sender_info[0]
            
            # If replying to a partner (not another employee)
            if not is_employee(original_sender_user_id):
                # Remove the unanswered turn for this partner
                rows_deleted = conn.execute('''DELETE FROM unanswered_questions
                                             WHERE chat_id = ? AND partner_user_id = ?''',
                                          (msg.chat_id, original_sender_user_id)).rowcount
                
                if rows_deleted > 0:
                    logging.info(f"Employee {user.id} replied to partner {original_sender_user_id}. Turn marked as answered.")
                
    except Exception as e:
        logging.error(f"Error handling employee reply: {e}")

async def track_response_metrics(conn, msg, user):
    """Track response time metrics for all replies."""
    try:
        original_msg_id = msg.reply_to_message.message_id
        original_msg_date = msg.reply_to_message.date
        response_time_seconds = (msg.date - original_msg_date).total_seconds()

        # Get original sender
        original_sender_info = conn.execute(
            "SELECT user_id FROM messages WHERE chat_id = ? AND message_id = ?", 
            (msg.chat_id, original_msg_id)
        ).fetchone()
        
        original_sender_user_id = original_sender_info[0] if original_sender_info else None

        # Store response metrics (with conflict handling)
        try:
            conn.execute('''INSERT INTO response_metrics
                         (reply_message_id, original_message_id, responder_user_id,
                          original_sender_user_id, chat_id, response_duration_seconds, timestamp)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (msg.message_id, original_msg_id, user.id,
                       original_sender_user_id, msg.chat_id, response_time_seconds, datetime.now()))
        except sqlite3.IntegrityError:
            # Response already tracked
            pass

        # Mark original message as answered
        conn.execute('UPDATE messages SET was_answered = TRUE WHERE chat_id = ? AND message_id = ?',
                   (msg.chat_id, original_msg_id))

        # Send slow response alert if configured
        if (response_time_seconds > config.RESPONSE_ALERT_THRESHOLD and 
            hasattr(config, 'SEND_SLOW_RESPONSE_ALERTS') and 
            config.SEND_SLOW_RESPONSE_ALERTS):
            await notify_slow_response(msg, response_time_seconds)
            
    except Exception as e:
        logging.error(f"Error tracking response metrics: {e}")

async def notify_slow_response(message: Message, response_time: float):
    """Notify about slow response times."""
    hours = response_time / 3600
    try:
        await message.reply_text(
            f"⏰ Response took {hours:.1f} hours\n"
            f"Target: <{config.RESPONSE_ALERT_THRESHOLD / 3600:.1f}h",
            reply_to_message_id=message.message_id
        )
    except Exception as e:
        logging.error(f"Failed to send slow response notification: {e}")

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message with available commands."""
    await update.message.reply_text(
        "👔 **Employee Communication Tracker**\n\n"
        "I monitor team communication and response times.\n\n"
        "**Available Commands:**\n"
        "• `/stats` - Your personal metrics\n"
        "• `/teamstats` - Team overview (admin only)\n"
        "• `/unanswered` - List pending partner messages\n"
        "• `/add_employee <user_id>` - Add employee (admin only)\n"
        "• `/employee_activity <id> <start> <end>` - Activity report (admin only)\n"
        "• `/help` - Show this help message",
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help information."""
    await start(update, context)

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show personal statistics."""
    try:
        user = update.message.from_user

        with sqlite3.connect(config.DATABASE_NAME) as conn:
            # Get message statistics
            stats_messages = conn.execute('''SELECT
                                  COUNT(id) as total_messages,
                                  SUM(CASE WHEN is_question THEN 1 ELSE 0 END) as questions_asked
                                  FROM messages
                                  WHERE user_id = ?''',
                               (user.id,)).fetchone()

            total_messages = stats_messages[0] if stats_messages else 0
            questions_asked = stats_messages[1] if stats_messages else 0

            # Get response statistics
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

            avg_response_h = avg_response_s / 3600
            max_response_h = max_response_s / 3600

            # Check if user is employee
            employee_status = "✅ Employee" if is_employee(user.id) else "👤 Partner"

            await update.message.reply_text(
                f"📊 **Your Statistics**\n\n"
                f"**Status:** {employee_status}\n"
                f"**Messages Sent:** {total_messages}\n"
                f"**Questions Asked:** {questions_asked}\n"
                f"**Replies Sent:** {replies_sent}\n"
                f"**Avg Response Time:** {avg_response_h:.1f}h\n"
                f"**Longest Response:** {max_response_h:.1f}h",
                parse_mode='Markdown'
            )

    except Exception as e:
        logging.error(f"Stats error for user {update.message.from_user.id}: {e}")
        await update.message.reply_text("🔧 Error fetching your statistics.")

@is_admin
async def teamstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show team statistics (admin only)."""
    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            seven_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            
            team_summary = conn.execute('''
                SELECT
                    ea.user_id,
                    COALESCE(SUM(ea.message_count), 0) AS total_messages,
                    COALESCE(AVG(ea.avg_response_time), 0) AS average_response_time,
                    COALESCE(SUM(ea.questions_asked), 0) AS total_questions_asked,
                    COALESCE(SUM(ea.questions_answered), 0) AS total_questions_answered,
                    e.full_name,
                    e.username
                FROM employees e
                LEFT JOIN employee_activity ea ON ea.user_id = e.user_id AND ea.date >= ?
                GROUP BY ea.user_id, e.full_name, e.username
                ORDER BY total_messages DESC
            ''', (seven_days_ago,)).fetchall()

            if not team_summary:
                await update.message.reply_text("No employees found or no activity in the last 7 days.")
                return

            response_text = "📊 **Team Performance (Last 7 Days)**\n\n"
            for user_id, msg_count, avg_resp_s, q_asked, q_answered, full_name, username in team_summary:
                user_display_name = full_name or username or f"Employee {user_id}"
                avg_resp_h = (avg_resp_s / 3600) if avg_resp_s else 0.0
                
                response_text += (
                    f"**{user_display_name}** (ID: {user_id})\n"
                    f"• Messages: {msg_count}\n"
                    f"• Questions Asked: {q_asked}\n"
                    f"• Questions Answered: {q_answered}\n"
                    f"• Avg Response: {avg_resp_h:.1f}h\n\n"
                )

            await update.message.reply_text(response_text, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Team stats error: {e}")
        await update.message.reply_text("🔧 Error fetching team statistics.")

async def unanswered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List pending unanswered partner messages."""
    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            now = datetime.now()
            
            cursor = conn.execute('''
                SELECT turn_id, chat_id, partner_user_id, last_partner_message_id, 
                       last_partner_message_text, last_message_timestamp
                FROM unanswered_questions
                WHERE (JULIANDAY(?) - JULIANDAY(last_message_timestamp)) * 86400 > ?
                ORDER BY last_message_timestamp ASC
            ''', (now, config.EMPLOYEE_RESPONSE_THRESHOLD_SECONDS))

            unanswered_turns = cursor.fetchall()

            if not unanswered_turns:
                await update.message.reply_text(
                    "🎉 No partner messages are currently unanswered past the threshold!"
                )
                return

            response_text = "📚 **Unanswered Partner Messages**\n\n"
            for turn_id, chat_id, partner_user_id, last_msg_id, last_msg_text, last_msg_timestamp_str in unanswered_turns:
                partner_info = conn.execute(
                    "SELECT username, full_name FROM messages WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1", 
                    (partner_user_id,)
                ).fetchone()
                
                partner_name = partner_info[1] if partner_info and partner_info[1] else f"Partner {partner_user_id}"

                turn_timestamp = datetime.strptime(last_msg_timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
                hours_since = (now - turn_timestamp).total_seconds() / 3600

                # Truncate long messages
                display_text = last_msg_text[:80] + "..." if len(last_msg_text) > 80 else last_msg_text

                response_text += (
                    f"**{partner_name}** (ID: {partner_user_id})\n"
                    f"• Message: \"{display_text}\"\n"
                    f"• Waiting: {hours_since:.1f}h\n"
                    f"• Chat: {chat_id}\n\n"
                )

            await update.message.reply_text(response_text, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Unanswered questions error: {e}")
        await update.message.reply_text("🔧 Error fetching unanswered questions.")

@is_admin
async def add_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add an employee to the database (admin only)."""
    if not context.args:
        await update.message.reply_text(
            "Please provide the employee's user ID or username.\n"
            "Example: `/add_employee 123456789` or `/add_employee @username`",
            parse_mode='Markdown'
        )
        return

    target_id_str = context.args[0]
    target_user_id = None
    target_username = None
    target_full_name = "Unknown"

    if target_id_str.startswith('@'):
        target_username = target_id_str[1:]
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            cursor = conn.execute(
                "SELECT user_id, full_name FROM messages WHERE username = ? ORDER BY timestamp DESC LIMIT 1", 
                (target_username,)
            )
            result = cursor.fetchone()
            if result:
                target_user_id, target_full_name = result
            else:
                await update.message.reply_text(
                    f"Couldn't find user '{target_id_str}' in message history. "
                    "Please use their numeric user ID."
                )
                return
    else:
        try:
            target_user_id = int(target_id_str)
            with sqlite3.connect(config.DATABASE_NAME) as conn:
                cursor = conn.execute(
                    "SELECT username, full_name FROM messages WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1", 
                    (target_user_id,)
                )
                result = cursor.fetchone()
                if result:
                    target_username, target_full_name = result
                else:
                    target_full_name = f"User {target_user_id}"
        except ValueError:
            await update.message.reply_text(
                "Invalid user ID. Please provide a numeric user ID or username."
            )
            return

    if target_user_id is None:
        await update.message.reply_text("Could not determine a valid user ID.")
        return

    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            cursor = conn.cursor()
            cursor.execute('''INSERT OR IGNORE INTO employees
                              (user_id, username, full_name, added_by, added_timestamp)
                              VALUES (?, ?, ?, ?, ?)''',
                           (target_user_id, target_username, target_full_name,
                            f"{update.message.from_user.full_name} ({update.message.from_user.id})",
                            datetime.now()))
            conn.commit()

            if cursor.rowcount > 0:
                logging.info(f"Admin {update.message.from_user.id} added employee {target_user_id}")
                await update.message.reply_text(
                    f"✅ Employee **{target_full_name}** (ID: {target_user_id}) has been added.",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text(
                    f"Employee **{target_full_name}** (ID: {target_user_id}) is already registered.",
                    parse_mode='Markdown'
                )

    except Exception as e:
        logging.error(f"Error adding employee {target_user_id}: {e}")
        await update.message.reply_text("An error occurred while adding the employee.")

@is_admin
async def employee_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display activity metrics for a specific employee within a date range (admin only)."""
    if len(context.args) != 3:
        await update.message.reply_text(
            "**Usage:** `/employee_activity <user_id> <start_date> <end_date>`\n"
            "**Example:** `/employee_activity 123456789 2024-01-01 2024-01-31`",
            parse_mode='Markdown'
        )
        return

    try:
        employee_id = int(context.args[0])
        start_date_str = context.args[1]
        end_date_str = context.args[2]

        # Validate date formats
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

        if start_date > end_date:
            await update.message.reply_text("Start date cannot be after end date.")
            return

    except ValueError:
        await update.message.reply_text(
            "Invalid employee ID or date format. Dates must be YYYY-MM-DD."
        )
        return

    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            # Check if user is an employee
            if not is_employee(employee_id):
                await update.message.reply_text(f"User ID {employee_id} is not registered as an employee.")
                return

            # Get employee info
            employee_info = conn.execute(
                "SELECT full_name, username FROM employees WHERE user_id = ?", 
                (employee_id,)
            ).fetchone()
            
            employee_display_name = (employee_info[0] if employee_info and employee_info[0] 
                                   else employee_info[1] if employee_info and employee_info[1] 
                                   else f"Employee {employee_id}")

            # Get activity metrics
            total_messages = conn.execute('''
                SELECT COUNT(id) FROM messages
                WHERE user_id = ? AND DATE(timestamp) BETWEEN ? AND ?
            ''', (employee_id, start_date_str, end_date_str)).fetchone()[0]

            total_replies = conn.execute('''
                SELECT COUNT(reply_message_id) FROM response_metrics
                WHERE responder_user_id = ? AND DATE(timestamp) BETWEEN ? AND ?
            ''', (employee_id, start_date_str, end_date_str)).fetchone()[0]

            avg_response_s = conn.execute('''
                SELECT AVG(response_duration_seconds) FROM response_metrics
                WHERE responder_user_id = ? AND DATE(timestamp) BETWEEN ? AND ?
            ''', (employee_id, start_date_str, end_date_str)).fetchone()[0]

            max_response_s = conn.execute('''
                SELECT MAX(response_duration_seconds) FROM response_metrics
                WHERE responder_user_id = ? AND DATE(timestamp) BETWEEN ? AND ?
            ''', (employee_id, start_date_str, end_date_str)).fetchone()[0]

            avg_response_h = (avg_response_s / 3600) if avg_response_s is not None else 0.0
            max_response_h = (max_response_s / 3600) if max_response_s is not None else 0.0

            # Get partner reply metrics
            partner_replies = conn.execute('''
                SELECT COUNT(reply_message_id) FROM response_metrics
                WHERE responder_user_id = ? AND DATE(timestamp) BETWEEN ? AND ?
                AND original_sender_user_id NOT IN (SELECT user_id FROM employees)
            ''', (employee_id, start_date_str, end_date_str)).fetchone()[0]

            response_text = (
                f"📊 **Activity Report: {employee_display_name}**\n"
                f"📅 **Period:** {start_date_str} to {end_date_str}\n\n"
                f"• **Total Messages:** {total_messages}\n"
                f"• **Total Replies:** {total_replies}\n"
                f"• **Replies to Partners:** {partner_replies}\n"
                f"• **Avg Response Time:** {avg_response_h:.1f}h\n"
                f"• **Max Response Time:** {max_response_h:.1f}h"
            )
            
            await update.message.reply_text(response_text, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Error fetching employee activity for {employee_id}: {e}")
        await update.message.reply_text("An error occurred while fetching employee activity.")

@is_admin
async def list_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all registered employees (admin only)."""
    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            employees = conn.execute('''
                SELECT user_id, username, full_name, added_timestamp 
                FROM employees 
                ORDER BY added_timestamp DESC
            ''').fetchall()

            if not employees:
                await update.message.reply_text("No employees registered yet.")
                return

            response_text = "👥 **Registered Employees:**\n\n"
            for user_id, username, full_name, added_timestamp in employees:
                display_name = full_name or username or f"Employee {user_id}"
                added_date = datetime.strptime(added_timestamp, '%Y-%m-%d %H:%M:%S.%f').strftime('%Y-%m-%d')
                response_text += f"• **{display_name}** (ID: {user_id}) - Added: {added_date}\n"

            await update.message.reply_text(response_text, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Error listing employees: {e}")
        await update.message.reply_text("Error fetching employee list.")

@is_admin
async def remove_employee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove an employee from the database (admin only)."""
    if not context.args:
        await update.message.reply_text(
            "Please provide the employee's user ID.\n"
            "Example: `/remove_employee 123456789`",
            parse_mode='Markdown'
        )
        return

    try:
        employee_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID. Please provide a numeric user ID.")
        return

    try:
        with sqlite3.connect(config.DATABASE_NAME) as conn:
            # Get employee info before deletion
            employee_info = conn.execute(
                "SELECT full_name, username FROM employees WHERE user_id = ?", 
                (employee_id,)
            ).fetchone()

            if not employee_info:
                await update.message.reply_text(f"User ID {employee_id} is not registered as an employee.")
                return

            display_name = employee_info[0] or employee_info[1] or f"Employee {employee_id}"

            # Remove employee
            cursor = conn.cursor()
            cursor.execute("DELETE FROM employees WHERE user_id = ?", (employee_id,))
            conn.commit()

            if cursor.rowcount > 0:
                logging.info(f"Admin {update.message.from_user.id} removed employee {employee_id}")
                await update.message.reply_text(
                    f"✅ Employee **{display_name}** (ID: {employee_id}) has been removed.",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("No employee was removed.")

    except Exception as e:
        logging.error(f"Error removing employee {employee_id}: {e}")
        await update.message.reply_text("An error occurred while removing the employee.")

# --- Error Handler ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and notify admin if configured."""
    logging.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    
    if config.ADMIN_CHAT_ID and isinstance(update, Update) and update.effective_message:
        try:
            await context.bot.send_message(
                chat_id=config.ADMIN_CHAT_ID,
                text=f"🚨 **Bot Error**\n\n"
                     f"**Error:** {str(context.error)[:200]}...\n"
                     f"**Chat:** {update.effective_chat.id if update.effective_chat else 'Unknown'}\n"
                     f"**User:** {update.effective_user.id if update.effective_user else 'Unknown'}",
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"Failed to send error notification to admin: {e}")

# --- Main Application ---
def main():
    """Initialize and start the Telegram bot."""
    # Initialize database
    init_db()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('bot.log'),
            logging.StreamHandler()
        ]
    )

    # Create bot application
    application = Application.builder().token(config.TOKEN).build()

    # Add error handler
    application.add_error_handler(error_handler)

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("teamstats", teamstats))
    application.add_handler(CommandHandler("unanswered", unanswered))
    application.add_handler(CommandHandler("add_employee", add_employee))
    application.add_handler(CommandHandler("remove_employee", remove_employee))
    application.add_handler(CommandHandler("list_employees", list_employees))
    application.add_handler(CommandHandler("employee_activity", employee_activity))

    # Add message handler for tracking
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_message))

    # Configure scheduler (using AsyncIOScheduler for better compatibility)
    scheduler = AsyncIOScheduler()
    
    # Schedule jobs
    scheduler.add_job(
        update_employee_activity_summary, 
        'cron', 
        hour=0, 
        minute=5,  # Run at 00:05 daily
        args=[application]
    )
    
    scheduler.add_job(
        check_unanswered_questions, 
        'interval', 
        minutes=30,  # Check every 30 minutes
        args=[application]
    )
    
    scheduler.start()

    # Start polling
    logging.info("Bot is starting...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.error(f"Bot error: {e}")
    finally:
        scheduler.shutdown()
        logging.info("Bot stopped")

if __name__ == "__main__":
    main()