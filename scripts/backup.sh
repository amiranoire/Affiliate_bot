#!/bin/bash
# scripts/backup.sh - Database backup script

echo "💾 Creating database backup..."

BOT_DIR="/opt/telegram-bot"
BACKUP_DIR="$BOT_DIR/backups"
DB_FILE="$BOT_DIR/employee_tracker.db"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/employee_tracker_$TIMESTAMP.db"

mkdir -p "$BACKUP_DIR"

if [ ! -f "$DB_FILE" ]; then
    echo "❌ Database file not found: $DB_FILE"
    exit 1
fi

cp "$DB_FILE" "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    echo "✅ Database backed up to: $BACKUP_FILE"
    find "$BACKUP_DIR" -name "employee_tracker_*.db" -mtime +7 -delete
    echo "🧹 Cleaned up old backups (older than 7 days)"
else
    echo "❌ Backup failed"
    exit 1
fi
