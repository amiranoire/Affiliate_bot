# config.py - Enhanced version of your existing config
import os
from dotenv import load_dotenv
from typing import Optional
import logging

class Config:
    """Centralized configuration management with validation"""
    
    def __init__(self):
        # Load environment variables from .env file
        load_dotenv()
        
        # Core bot configuration
        self.TOKEN: str = self._get_env_var("TELEGRAM_BOT_TOKEN")
        self.ADMIN_CHAT_ID: Optional[int] = self._parse_admin_chat_id()
        
        # Database configuration
        self.DATABASE_NAME: str = os.getenv("DATABASE_NAME", "employee_tracker.db")
        self.DATABASE_TIMEOUT: int = int(os.getenv("DATABASE_TIMEOUT", "30"))
        
        # Tracking thresholds (in seconds)
        self.RESPONSE_ALERT_THRESHOLD: int = int(os.getenv("RESPONSE_ALERT_THRESHOLD", "7200"))  # 2 hours
        self.UNANSWERED_ALERT_THRESHOLD: int = int(os.getenv("UNANSWERED_ALERT_THRESHOLD", "14400"))  # 4 hours
        
        # Additional timing configurations
        self.CONVERSATION_TURN_TIMEOUT: int = int(os.getenv("CONVERSATION_TURN_TIMEOUT", "3600"))  # 1 hour
        self.CHECK_INTERVAL_MINUTES: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))  # 30 minutes
        
        # Feature flags
        self.SEND_SLOW_RESPONSE_ALERTS: bool = os.getenv("SEND_SLOW_RESPONSE_ALERTS", "false").lower() == "true"
        self.DEBUG_MODE: bool = os.getenv("DEBUG_MODE", "false").lower() == "true"
        self.ENABLE_METRICS: bool = os.getenv("ENABLE_METRICS", "true").lower() == "true"
        
        # Logging configuration
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
        self.LOG_FILE: str = os.getenv("LOG_FILE", "bot.log")
        self.LOG_MAX_SIZE_MB: int = int(os.getenv("LOG_MAX_SIZE_MB", "10"))
        self.LOG_BACKUP_COUNT: int = int(os.getenv("LOG_BACKUP_COUNT", "5"))
        
        # Server configuration (for webhooks if needed)
        self.SERVER_HOST: str = os.getenv("SERVER_HOST", "0.0.0.0")
        self.SERVER_PORT: int = int(os.getenv("SERVER_PORT", "8000"))
        self.WEBHOOK_URL: Optional[str] = os.getenv("WEBHOOK_URL")
        
        # Rate limiting
        self.MAX_MESSAGES_PER_MINUTE: int = int(os.getenv("MAX_MESSAGES_PER_MINUTE", "20"))
        self.ADMIN_NOTIFICATION_COOLDOWN: int = int(os.getenv("ADMIN_NOTIFICATION_COOLDOWN", "300"))  # 5 minutes
        
        # Validate configuration on initialization
        self.validate_config()
    
    def _get_env_var(self, var_name: str) -> str:
        """Get required environment variable with validation"""
        value = os.getenv(var_name)
        if not value:
            raise ValueError(f"❌ Missing required environment variable: {var_name}")
        return value
    
    def _parse_admin_chat_id(self) -> Optional[int]:
        """Parse optional admin chat ID"""
        chat_id = os.getenv("ADMIN_CHAT_ID")
        if chat_id:
            try:
                return int(chat_id)
            except ValueError:
                raise ValueError(f"❌ Invalid ADMIN_CHAT_ID: {chat_id}. Must be a number.")
        return None
    
    def validate_config(self) -> bool:
        """Validate all critical configurations"""
        validations = []
        
        # Validate token format
        if not self.TOKEN.startswith(('bot', 'Bot')):
            logging.warning("⚠️  Bot token doesn't start with 'bot' - this might be incorrect")
        
        # Validate thresholds
        if self.RESPONSE_ALERT_THRESHOLD <= 0:
            validations.append("RESPONSE_ALERT_THRESHOLD must be positive")
        
        if self.UNANSWERED_ALERT_THRESHOLD <= 0:
            validations.append("UNANSWERED_ALERT_THRESHOLD must be positive")
        
        if self.CONVERSATION_TURN_TIMEOUT <= 0:
            validations.append("CONVERSATION_TURN_TIMEOUT must be positive")
        
        # Validate log level
        valid_log_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if self.LOG_LEVEL not in valid_log_levels:
            validations.append(f"LOG_LEVEL must be one of: {valid_log_levels}")
        
        # Validate database timeout
        if self.DATABASE_TIMEOUT <= 0:
            validations.append("DATABASE_TIMEOUT must be positive")
        
        if validations:
            raise ValueError(f"❌ Configuration validation failed:\n" + "\n".join(f"  • {v}" for v in validations))
        
        return True
    
    def get_log_config(self) -> dict:
        """Get logging configuration dictionary"""
        return {
            'level': getattr(logging, self.LOG_LEVEL),
            'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            'filename': self.LOG_FILE,
            'max_size_mb': self.LOG_MAX_SIZE_MB,
            'backup_count': self.LOG_BACKUP_COUNT
        }
    
    def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        return self.ADMIN_CHAT_ID is not None and user_id == self.ADMIN_CHAT_ID
    
    def __str__(self) -> str:
        """String representation for debugging (without sensitive data)"""
        return (
            f"Config(\n"
            f"  DATABASE_NAME={self.DATABASE_NAME}\n"
            f"  ADMIN_CHAT_ID={'Set' if self.ADMIN_CHAT_ID else 'Not Set'}\n"
            f"  RESPONSE_ALERT_THRESHOLD={self.RESPONSE_ALERT_THRESHOLD}s\n"
            f"  UNANSWERED_ALERT_THRESHOLD={self.UNANSWERED_ALERT_THRESHOLD}s\n"
            f"  DEBUG_MODE={self.DEBUG_MODE}\n"
            f"  LOG_LEVEL={self.LOG_LEVEL}\n"
            f")"
        )

# Singleton configuration instance
config = Config()

# Backward compatibility exports for existing code
TOKEN = config.TOKEN
ADMIN_CHAT_ID = config.ADMIN_CHAT_ID
DATABASE_NAME = config.DATABASE_NAME
RESPONSE_ALERT_THRESHOLD = config.RESPONSE_ALERT_THRESHOLD
EMPLOYEE_RESPONSE_THRESHOLD_SECONDS = config.UNANSWERED_ALERT_THRESHOLD
CONVERSATION_TURN_TIMEOUT_SECONDS = config.CONVERSATION_TURN_TIMEOUT
SEND_SLOW_RESPONSE_ALERTS = config.SEND_SLOW_RESPONSE_ALERTS