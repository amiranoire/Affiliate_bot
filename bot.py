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
from apscheduler.schedulers.background import BackgroundScheduler

from config import config

# --- Database Connection Helper ---
def get_db_connection():
    """Establishes a SQLite database connection with necessary pragmas."""
    conn = sqlite3.connect(config.DATABASE_NAME, check_same_thread=False) # [3, 1]
    conn.execute("PRAGMA journal_mode=WAL;") # [4, 1]
    conn.execute("PRAGMA foreign_keys = ON;") # [2, 5, 1]
    return conn

# --- Decorators ---
def is_admin(func: Callable) -> Callable:
    """Decorator to restrict command usage to the ADMIN_CHAT_ID."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any) -> None:
        if config.ADMIN_CHAT_ID and update.effective_user.id!= config.ADMIN_CHAT_ID:
            await update.message.reply_text("You are not authorized to use this command.")
            logging.warning(f"Unauthorized access attempt to {func.__name__} by user {update.effective_user.id}")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# The 'run_async' decorator has been removed as it was redundant for async functions in python-telegram-bot. [1]

# --- Database Setup ---
def init_db():
    """Initialize database with enhanced employee tracking schema"""
    with get_db_connection() as conn: # [1]
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
                     is_partner_message BOOLEAN DEFAULT FALSE,
                     turn_id INTEGER DEFAULT NULL,
                     UNIQUE(chat_id, message_id),
                     FOREIGN KEY (turn_id) REFERENCES unanswered_questions(turn_id) ON DELETE SET NULL)''') # [1]

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
                     partner_turns_initiated INTEGER DEFAULT 0,
                     partner_turns_answered INTEGER DEFAULT 0,
                     PRIMARY KEY (user_id, date),
                     FOREIGN KEY (user_id) REFERENCES employees(user_id) ON DELETE CASCADE)''') # [1]

        # Partner conversation turns tracking
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
        with get_db_connection() as conn: # [1]
            cursor = conn.execute("SELECT 1 FROM employees WHERE user_id =?", (user_id,))
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
        with get_db_connection() as conn: # [1]
            cursor = conn.execute('''
                SELECT turn_id, chat_id, partner_user_id, last_partner_message_id, 
                    last_partner_message_text, last_message_timestamp
                FROM unanswered_questions
                WHERE reminded = FALSE
                AND (JULIANDAY(?) - JULIANDAY(last_message_timestamp)) * 86400 >?
''', (now, config.UNANSWERED_ALERT_THRESHOLD))

            unanswered_turns = cursor.fetchall()

            for turn_id, chat_id, partner_user_id, last_msg_id, last_msg_text, last_msg_timestamp_str in unanswered_turns:
                try:
                    turn_timestamp = datetime.strptime(last_msg_timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
                    time_since_last_message = (now - turn_timestamp).total_seconds()
                    hours_since_last_message = time_since_last_message / 3600

                    # Get partner info
                    partner_info = conn.execute(
                        "SELECT username, full_name FROM messages WHERE user_id =? ORDER BY timestamp DESC LIMIT 1", 
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
                        text=f"**Unanswered Partner Turn**\n\n"
                             f"**From:** {partner_display_name} (ID: {partner_user_id})\n"
                             f"**Last Message:** \"{display_text}\"\n"
                             f"**Unanswered for:** {hours_since_last_message:.1f} hours\n"
                             f"**Link:** {chat_link}",
                        parse_mode='Markdown',
                        disable_web_page_preview=True
                    )
                    
                    # Mark as reminded
                    conn.execute("UPDATE unanswered_questions SET reminded = TRUE WHERE turn_id =?", (turn_id,))
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
        with get_db_connection() as conn: # [1]
            # Get message counts for employees
            messages_data = conn.execute('''
                SELECT
                    user_id,
                    COUNT(id) AS message_count
                FROM messages
                WHERE DATE(timestamp) =?
                GROUP BY user_id
            ''', (today,)).fetchall() # [1]

            # Get partner turns answered by employees (responses to partners)
            partner_turns_answered = conn.execute('''
                SELECT
                    responder_user_id,
                    COUNT(reply_message_id) AS turns_answered
                FROM response_metrics
                WHERE DATE(timestamp) =? 
                AND original_sender_user_id NOT IN (SELECT user_id FROM employees)
                GROUP BY responder_user_id
            ''', (today,)).fetchall() # [1]

            # Get average response times for employees
            response_times = conn.execute('''
                SELECT
                    responder_user_id,
                    AVG(response_duration_seconds) AS avg_resp_time
                FROM response_metrics
                WHERE DATE(timestamp) =?
                GROUP BY responder_user_id
            ''', (today,)).fetchall() # [1]

            # Count partner turns that were initiated today (for context)
            partner_turns_today = conn.execute('''
                SELECT COUNT(turn_id) FROM unanswered_questions
                WHERE DATE(turn_start_timestamp) =?
            ''', (today,)).fetchone()

            # Consolidate data
            activity_updates = {}
            
            # Initialize with message counts
            for user_id, msg_count in messages_data:
                activity_updates[user_id] = {
                    'message_count': msg_count,
                    'avg_response_time': 0.0,
                    'partner_turns_initiated': 0,  # Employees don't initiate partner turns
                    'partner_turns_answered': 0
                }

            # Add partner turns answered
            for user_id, turns_answered in partner_turns_answered:
                if user_id not in activity_updates:
                    activity_updates[user_id] = {
                        'message_count': 0,
                        'avg_response_time': 0.0,
                        'partner_turns_initiated': 0,
                        'partner_turns_answered': 0
                    }
                activity_updates[user_id]['partner_turns_answered'] = turns_answered

            # Add response times
            for user_id, avg_resp_time in response_times:
                if user_id in activity_updates:
                    activity_updates[user_id]['avg_response_time'] = avg_resp_time if avg_resp_time is not None else 0.0

            # Update database
            for user_id, data in activity_updates.items():
                conn.execute('''
                    INSERT OR REPLACE INTO employee_activity
                    (user_id, date, message_count, avg_response_time, partner_turns_initiated, partner_turns_answered)
                    VALUES (?,?,?,?,?,?)
                ''', (user_id, today, data['message_count'], data['avg_response_time'],
                      data['partner_turns_initiated'], data['partner_turns_answered']))

            conn.commit()
            logging.info(f"Employee activity summary updated for {today}. Partner turns today: {partner_turns_today}")

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

        with get_db_connection() as conn: # [1]
            sender_is_employee = is_employee(user.id)
            is_partner_message = not sender_is_employee
            
            # Get the last partner message in this chat (for automatic reply tracking)
            last_partner_msg = None
            if sender_is_employee:
                cursor = conn.execute('''
                    SELECT message_id, user_id, timestamp 
                    FROM messages 
                    WHERE chat_id =?
                    AND is_partner_message = 1 
                    ORDER BY timestamp DESC 
                    LIMIT 1
                ''', (msg.chat_id,))
                last_partner_msg = cursor.fetchone()
                
                if last_partner_msg:
                    logging.info(f"Found last partner message: {last_partner_msg} from user {last_partner_msg[1]}")
            
            # Handle partner messages (create/update conversation turns)
            turn_id = None
            if is_partner_message:
                turn_id = await handle_partner_message(conn, msg, user)

            # Store the message with turn association
            try:
                conn.execute('''INSERT INTO messages
                             (user_id, username, full_name, chat_id,
                              message_id, text, timestamp, replied_to_message_id, is_partner_message, turn_id)
                             VALUES (?,?,?,?,?,?,?,?,?,?)''',
                          (user.id, username, full_name, msg.chat_id, 
                           msg.message_id, msg.text, datetime.now(),
                           msg.reply_to_message.message_id if msg.reply_to_message else None,
                           is_partner_message, turn_id))
            except sqlite3.IntegrityError:
                # Message already exists, skip processing [1]
                return

            # Handle employee messages as replies to last partner message
            if sender_is_employee and last_partner_msg:
                logging.info(f"Employee {user.id} sent message after partner - treating as reply")
                # Treat this as a reply to the last partner message
                await handle_employee_reply_simple(conn, msg, user, last_partner_msg) # [1]
                
                # Track response metrics
                await track_response_metrics_simple(conn, msg, user, last_partner_msg) # [1]
            elif msg.reply_to_message:
                # Still track explicit replies if they exist
                await track_response_metrics(conn, msg, user)

            conn.commit()

    except Exception as e:
        logging.error(f"Tracking error: {e}", exc_info=True)
        if config.ADMIN_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=config.ADMIN_CHAT_ID,
                    text=f"**Tracking Error in chat {msg.chat_id}:** {str(e)}" # [1]
                )
            except Exception as admin_e:
                logging.error(f"Failed to send admin error notification: {admin_e}")

async def handle_partner_message(conn, msg, user):
    """Handle messages from partners (non-employees) and manage conversation turns."""
    try:
        # Check for existing unanswered turn
        cursor = conn.execute('''SELECT turn_id, last_message_timestamp
                                 FROM unanswered_questions
                                 WHERE chat_id =?
                                 AND partner_user_id =?''',
                              (msg.chat_id, user.id))
        
        existing_turn = cursor.fetchone()
        current_time = datetime.now()

        if existing_turn:
            turn_id, last_msg_timestamp_str = existing_turn
            last_msg_timestamp = datetime.strptime(last_msg_timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
            
            # Check if within conversation turn timeout
            if (current_time - last_msg_timestamp).total_seconds() < config.CONVERSATION_TURN_TIMEOUT:
                # Update existing turn
                conn.execute('''UPDATE unanswered_questions
                                 SET last_partner_message_id =?,
                                     last_partner_message_text =?,
                                     last_message_timestamp =?,
                                     reminded = FALSE
                                 WHERE turn_id =?''',
                          (msg.message_id, msg.text, current_time, turn_id))
                logging.info(f"Updated existing partner turn {turn_id} for user {user.id}")
                return turn_id
            else:
                # Replace old turn with new one
                conn.execute('DELETE FROM unanswered_questions WHERE turn_id =?', (turn_id,))
                
        # Create new turn
        cursor = conn.execute('''INSERT INTO unanswered_questions
                     (chat_id, partner_user_id, last_partner_message_id,
                      last_partner_message_text, turn_start_timestamp, last_message_timestamp)
                     VALUES (?,?,?,?,?,?) RETURNING turn_id''',
                  (msg.chat_id, user.id, msg.message_id,
                   msg.text, current_time, current_time))
        new_turn_id = cursor.fetchone()
        logging.info(f"Created new partner turn {new_turn_id} for user {user.id}")
        return new_turn_id
        
    except Exception as e:
        logging.error(f"Error handling partner message: {e}")
        return None

async def handle_employee_reply(conn, msg, user):
    """Handle replies from employees to mark partner turns as answered."""
    try:
        original_msg_id = msg.reply_to_message.message_id
        
        # Get the original message sender
        original_sender_info = conn.execute(
            "SELECT user_id FROM messages WHERE chat_id =? AND message_id =?", 
            (msg.chat_id, original_msg_id)
        ).fetchone()
        
        if original_sender_info:
            original_sender_user_id = original_sender_info
            
            # If replying to a partner (not another employee)
            if not is_employee(original_sender_user_id):
                # Remove the unanswered turn for this partner
                rows_deleted = conn.execute('''DELETE FROM unanswered_questions
                                             WHERE chat_id =? AND partner_user_id =?''',
                                       (msg.chat_id, original_sender_user_id)).rowcount
                
                if rows_deleted > 0:
                    logging.info(f"Employee {user.id} replied to partner {original_sender_user_id}. Turn marked as answered.")
                
    except Exception as e:
        logging.error(f"Error handling employee reply: {e}")

async def handle_employee_reply_simple(conn, msg, user, last_partner_msg):
    """Handle employee messages as replies to the last partner message."""
    try:
        original_sender_user_id = last_partner_msg[1]
        
        # Remove the unanswered turn for this partner
        rows_deleted = conn.execute('''DELETE FROM unanswered_questions
                                     WHERE chat_id =?
                                     AND partner_user_id =?''',
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
            "SELECT user_id FROM messages WHERE chat_id =? AND message_id =?", 
            (msg.chat_id, original_msg_id)
        ).fetchone()
        
        original_sender_user_id = original_sender_info if original_sender_info else None

        # Store response metrics (with conflict handling)
        try:
            conn.execute('''INSERT INTO response_metrics
                         (reply_message_id, original_message_id, responder_user_id,
                          original_sender_user_id, chat_id, response_duration_seconds, timestamp)
                         VALUES (?,?,?,?,?,?,?)''',
                      (msg.message_id, original_msg_id, user.id,
                       original_sender_user_id, msg.chat_id, response_time_seconds, datetime.now()))
        except sqlite3.IntegrityError:
            # Response already tracked [1]
            pass

        # Mark original message as answered
        conn.execute('UPDATE messages SET was_answered = TRUE WHERE chat_id =? AND message_id =?',
                   (msg.chat_id, original_msg_id))

        # Send slow response alert if configured
        if (response_time_seconds > config.RESPONSE_ALERT_THRESHOLD and 
            hasattr(config, 'SEND_SLOW_RESPONSE_ALERTS') and 
            config.SEND_SLOW_RESPONSE_ALERTS):
            await notify_slow_response(msg, response_time_seconds)
            
    except Exception as e:
        logging.error(f"Error tracking response metrics: {e}")

async def track_response_metrics_simple(conn, msg, user, last_partner_msg):
    """Track response time metrics based on last partner message."""
    try:
        original_msg_id = last_partner_msg
        original_sender_user_id = last_partner_msg[1]
        original_timestamp = datetime.strptime(last_partner_msg[2], '%Y-%m-%d %H:%M:%S.%f')
        
        response_time_seconds = (datetime.now() - original_timestamp).total_seconds()

        # Store response metrics
        try:
            conn.execute('''INSERT INTO response_metrics
                         (reply_message_id, original_message_id, responder_user_id,
                          original_sender_user_id, chat_id, response_duration_seconds, timestamp)
                         VALUES (?,?,?,?,?,?,?)''',
                      (msg.message_id, original_msg_id, user.id,
                       original_sender_user_id, msg.chat_id, response_time_seconds, datetime.now()))
            logging.info(f"Tracked response: Employee {user.id} responded to partner {original_sender_user_id} in {response_time_seconds:.1f} seconds")
        except sqlite3.IntegrityError:
            # Response already tracked [1]
            pass

        # Mark original message as answered
        conn.execute('UPDATE messages SET was_answered = TRUE WHERE chat_id =? AND message_id =?',
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
            f"Response took {hours:.1f} hours\n"
            f"Target: <{config.RESPONSE_ALERT_THRESHOLD / 3600:.1f}h",
            reply_to_message_id=message.message_id
        )
    except Exception as e:
        logging.error(f"Failed to send slow response notification: {e}")

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message with available commands."""
    await update.message.reply_text(
        "**Employee Communication Tracker**\n\n"
        "I monitor team communication and partner response times.\n\n"
        "**Available Commands:**\n"
        "• `/stats` - Your personal metrics\n"
        "• `/teamstats` - Team overview (admin only)\n"
        "• `/unanswered` - List pending partner turns\n"
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
        sender_is_employee = is_employee(user.id)

        with get_db_connection() as conn: # [1]
            # Get message statistics
            stats_messages = conn.execute('''SELECT
                                  COUNT(id) as total_messages,
                                  SUM(CASE WHEN is_partner_message THEN 1 ELSE 0 END) as partner_messages
                                  FROM messages
                                  WHERE user_id =?''',
                               (user.id,)).fetchone()

            total_messages = stats_messages if stats_messages else 0
            partner_messages = stats_messages[1] if stats_messages else 0

            # Get response statistics (for employees)
            response_stats = conn.execute('''SELECT
                                           COUNT(reply_message_id) as replies_sent,
                                           AVG(response_duration_seconds) as avg_response_s,
                                           MAX(response_duration_seconds) as max_response_s
                                           FROM response_metrics
                                           WHERE responder_user_id =?''',
                                        (user.id,)).fetchone()

            replies_sent = response_stats if response_stats else 0
            avg_response_s = response_stats[1] if response_stats and response_stats[1] is not None else 0
            max_response_s = response_stats[2] if response_stats and response_stats[2] is not None else 0

            avg_response_h = avg_response_s / 3600
            max_response_h = max_response_s / 3600

            # Get partner turn statistics
            if not sender_is_employee:
                # For partners - show turns initiated
                partner_turns = conn.execute('''SELECT COUNT(turn_id) FROM unanswered_questions
                                         WHERE partner_user_id =?''', (user.id,)).fetchone()
                
                # Check if user is employee
                employee_status = "Partner"
                
                await update.message.reply_text(
                    f"**Your Statistics**\n\n"
                    f"**Status:** {employee_status}\n"
                    f"**Messages Sent:** {total_messages}\n"
                    f"**Conversation Turns:** {partner_turns}\n"
                    f"**Replies Sent:** {replies_sent}\n"
                    f"**Avg Response Time:** {avg_response_h:.1f}h\n"
                    f"**Longest Response:** {max_response_h:.1f}h",
                    parse_mode='Markdown'
                )
            else:
                # For employees - show turns answered
                partner_turns_answered = conn.execute('''SELECT COUNT(reply_message_id) FROM response_metrics
                                                       WHERE responder_user_id =? 
                                                       AND original_sender_user_id NOT IN (SELECT user_id FROM employees)''',
                                                    (user.id,)).fetchone()
                
                employee_status = "Employee"
                
                await update.message.reply_text(
                    f"**Your Statistics**\n\n"
                    f"**Status:** {employee_status}\n"
                    f"**Messages Sent:** {total_messages}\n"
                    f"**Partner Turns Answered:** {partner_turns_answered}\n"
                    f"**Total Replies:** {replies_sent}\n"
                    f"**Avg Response Time:** {avg_response_h:.1f}h\n"
                    f"**Longest Response:** {max_response_h:.1f}h",
                    parse_mode='Markdown'
                )

    except Exception as e:
        logging.error(f"Stats error for user {update.message.from_user.id}: {e}")
        await update.message.reply_text("Error fetching your statistics.")

@is_admin
async def teamstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show team statistics (admin only)."""
    try:
        with get_db_connection() as conn: # [1]
            seven_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
            
            team_summary = conn.execute('''
                SELECT
                    ea.user_id,
                    COALESCE(SUM(ea.message_count), 0) AS total_messages,
                    COALESCE(AVG(ea.avg_response_time), 0) AS average_response_time,
                    COALESCE(SUM(ea.partner_turns_answered), 0) AS total_turns_answered,
                    e.full_name,
                    e.username
                FROM employees e
                LEFT JOIN employee_activity ea ON ea.user_id = e.user_id AND ea.date >=?
                GROUP BY ea.user_id, e.full_name, e.username
                ORDER BY total_messages DESC
            ''', (seven_days_ago,)).fetchall()

            if not team_summary:
                await update.message.reply_text("No employees found or no activity in the last 7 days.")
                return

            response_text = "**Team Performance (Last 7 Days)**\n\n"
            for user_id, msg_count, avg_resp_s, turns_answered, full_name, username in team_summary:
                user_display_name = full_name or username or f"Employee {user_id}"
                avg_resp_h = (avg_resp_s / 3600) if avg_resp_s else 0.0
                
                response_text += (
                    f"**{user_display_name}** (ID: {user_id})\n"
                    f"• Messages: {msg_count}\n"
                    f"• Partner Turns Answered: {turns_answered}\n"
                    f"• Avg Response: {avg_resp_h:.1f}h\n\n"
                )

            await update.message.reply_text(response_text, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Team stats error: {e}")
        await update.message.reply_text("Error fetching team statistics.")

async def unanswered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List pending unanswered partner turns."""
    try:
        with get_db_connection() as conn: # [1]
            now = datetime.now()
            
            cursor = conn.execute('''
                SELECT turn_id, chat_id, partner_user_id, last_partner_message_id, 
                       last_partner_message_text, last_message_timestamp
                FROM unanswered_questions
                WHERE (JULIANDAY(?) - JULIANDAY(last_message_timestamp)) * 86400 >?
                ORDER BY last_message_timestamp ASC
            ''', (now, config.UNANSWERED_ALERT_THRESHOLD))

            unanswered_turns = cursor.fetchall()

            if not unanswered_turns:
                await update.message.reply_text(
                    "No partner turns are currently unanswered past the threshold!"
                )
                return

            response_text = "**Unanswered Partner Turns**\n\n"
            for turn_id, chat_id, partner_user_id, last_msg_id, last_msg_text, last_msg_timestamp_str in unanswered_turns:
                partner_info = conn.execute(
                    "SELECT username, full_name FROM messages WHERE user_id =? ORDER BY timestamp DESC LIMIT 1", 
                    (partner_user_id,)
                ).fetchone()
                
                partner_name = partner_info[1] if partner_info and partner_info[1] else f"Partner {partner_user_id}"

                turn_timestamp = datetime.strptime(last_msg_timestamp_str, '%Y-%m-%d %H:%M:%S.%f')
                hours_since = (now - turn_timestamp).total_seconds() / 3600

                # Truncate long messages
                display_text = last_msg_text[:80] + "..." if len(last_msg_text) > 80 else last_msg_text

                response_text += (
                    f"**{partner_name}** (ID: {partner_user_id})\n"
                    f"• Last Message: \"{display_text}\"\n"
                    f"• Waiting: {hours_since:.1f}h\n"
                    f"• Chat: {chat_id}\n\n"
                )

            await update.message.reply_text(response_text, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Unanswered questions error: {e}")
        await update.message.reply_text("Error fetching unanswered turns.")

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

    target_id_str = context.args
    target_user_id = None
    target_username = None
    target_full_name = "Unknown"

    if target_id_str.startswith('@'):
        target_username = target_id_str[1:]
        with get_db_connection() as conn: # [1]
            cursor = conn.execute(
                "SELECT user_id, full_name FROM messages WHERE username =? ORDER BY timestamp DESC LIMIT 1", 
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
            with get_db_connection() as conn: # [1]
                cursor = conn.execute(
                    "SELECT username, full_name FROM messages WHERE user_id =? ORDER BY timestamp DESC LIMIT 1", 
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
        with get_db_connection() as conn: # [1]
            cursor = conn.cursor()
            cursor.execute('''INSERT OR IGNORE INTO employees
                              (user_id, username, full_name, added_by, added_timestamp)
                              VALUES (?,?,?,?,?)''',
                         (target_user_id, target_username, target_full_name,
                            f"{update.message.from_user.full_name} ({update.message.from_user.id})",
                            datetime.now()))
            conn.commit()

            if cursor.rowcount > 0:
                logging.info(f"Admin {update.message.from_user.id} added employee {target_user_id}")
                await update.message.reply_text(
                    f"Employee **{target_full_name}** (ID: {target_user_id}) has been added.",
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
    if len(context.args)!= 3:
        await update.message.reply_text(
            "**Usage:** `/employee_activity <user_id> <start_date> <end_date>`\n"
            "**Example:** `/employee_activity 123456789 2024-01-01 2024-01-31`",
            parse_mode='Markdown'
        )
        return

    try:
        employee_id = int(context.args)
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
        with get_db_connection() as conn: # [1]
            # Check if user is an employee
            if not is_employee(employee_id):
                await update.message.reply_text(f"User ID {employee_id} is not registered as an employee.")
                return

            # Get employee info
            employee_info = conn.execute(
                "SELECT full_name, username FROM employees WHERE user_id =?", 
                (employee_id,)
            ).fetchone()
            
            employee_display_name = (employee_info if employee_info and employee_info 
                                   else employee_info[1] if employee_info and employee_info[1] 
                                   else f"Employee {employee_id}")

            # Get activity metrics
            total_messages = conn.execute('''
                SELECT COUNT(id) FROM messages
                WHERE user_id =? AND DATE(timestamp) BETWEEN? AND?
            ''', (employee_id, start_date_str, end_date_str)).fetchone() # [1]

            total_replies = conn.execute('''
                SELECT COUNT(reply_message_id) FROM response_metrics
                WHERE responder_user_id =? AND DATE(timestamp) BETWEEN? AND?
            ''', (employee_id, start_date_str, end_date_str)).fetchone() # [1]

            avg_response_s = conn.execute('''
                SELECT AVG(response_duration_seconds) FROM response_metrics
                WHERE responder_user_id =? AND DATE(timestamp) BETWEEN? AND?
            ''', (employee_id, start_date_str, end_date_str)).fetchone() # [1]

            max_response_s = conn.execute('''
                SELECT MAX(response_duration_seconds) FROM response_metrics
                WHERE responder_user_id =? AND DATE(timestamp) BETWEEN? AND?
            ''', (employee_id, start_date_str, end_date_str)).fetchone() # [1]

            avg_response_h = (avg_response_s / 3600) if avg_response_s is not None else 0.0
            max_response_h = (max_response_s / 3600) if max_response_s is not None else 0.0

            # Get partner turn replies
            partner_turns_answered = conn.execute('''
                SELECT COUNT(reply_message_id) FROM response_metrics
                WHERE responder_user_id =?
                AND DATE(timestamp) BETWEEN? AND?
                AND original_sender_user_id NOT IN (SELECT user_id FROM employees)
            ''', (employee_id, start_date_str, end_date_str)).fetchone() # [1]

            response_text = (
                f"**Activity Report: {employee_display_name}**\n"
                f"**Period:** {start_date_str} to {end_date_str}\n\n"
                f"• **Total Messages:** {total_messages}\n"
                f"• **Total Replies:** {total_replies}\n"
                f"• **Partner Turns Answered:** {partner_turns_answered}\n"
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
        with get_db_connection() as conn: # [1]
            employees = conn.execute('''
                SELECT user_id, username, full_name, added_timestamp 
                FROM employees 
                ORDER BY added_timestamp DESC
            ''').fetchall()

            if not employees:
                await update.message.reply_text("No employees registered yet.")
                return

            response_text = "**Registered Employees:**\n\n"
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
        employee_id = int(context.args)
    except ValueError:
        await update.message.reply_text("Invalid user ID. Please provide a numeric user ID.")
        return

    try:
        with get_db_connection() as conn: # [1]
            # Get employee info before deletion
            employee_info = conn.execute(
                "SELECT full_name, username FROM employees WHERE user_id =?", 
                (employee_id,)
            ).fetchone()

            if not employee_info:
                await update.message.reply_text(f"User ID {employee_id} is not registered as an employee.")
                return

            display_name = employee_info or employee_info[1] or f"Employee {employee_id}"

            # Remove employee
            cursor = conn.cursor()
            cursor.execute("DELETE FROM employees WHERE user_id =?", (employee_id,))
            conn.commit()

            if cursor.rowcount > 0:
                logging.info(f"Admin {update.message.from_user.id} removed employee {employee_id}")
                await update.message.reply_text(
                    f"Employee **{display_name}** (ID: {employee_id}) has been removed.",
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
                text=f"**Bot Error**\n\n"
                     f"**Error:** {str(context.error)}\n" # [1]
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
        handlers=
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

    # Configure scheduler
    scheduler = BackgroundScheduler()
    
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
        application.run_polling(allowed_updates=['message', 'edited_message', 'channel_post', 'edited_channel_post']) # [6, 7, 8, 1, 9, 10]
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.error(f"Bot error: {e}")
    finally:
        scheduler.shutdown()
        logging.info("Bot stopped")

if __name__ == "__main__":
    main()