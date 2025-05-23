import os
from dotenv import load_dotenv
from typing import Optional

class Config:
    """Centralized configuration management with validation"""
    
    def __init__(self):
        load_dotenv()  # Load from .env file
        
        # Core bot configuration
        self.TOKEN: str = self._get_env_var("TELEGRAM_BOT_TOKEN")
        self.ADMIN_CHAT_ID: Optional[int] = self._parse_admin_chat_id()
        
        # Database configuration
        self.DATABASE_NAME: str = os.getenv("DATABASE_NAME", "employee_tracker.db")
        self.DATABASE_TIMEOUT: int = int(os.getenv("DATABASE_TIMEOUT", "30"))
        
        # Tracking thresholds (in seconds)
        self.RESPONSE_ALERT_THRESHOLD: int = int(os.getenv("RESPONSE_ALERT_THRESHOLD", "7200"))  # 2 hours
        self.UNANSWERED_ALERT_THRESHOLD: int = int(os.getenv("UNANSWERED_ALERT_THRESHOLD", "14400"))  # 4 hours
    
    def _get_env_var(self, var_name: str) -> str:
        """Get required environment variable with validation"""
        value = os.getenv(var_name)
        if not value:
            raise ValueError(f"❌ Missing required environment variable: {var_name}")
        return value
    
    def _parse_admin_chat_id(self) -> Optional[int]:
        """Parse optional admin chat ID"""
        chat_id = os.getenv("ADMIN_CHAT_ID")
        return int(chat_id) if chat_id else None
    
    def validate_config(self) -> bool:
        """Validate all critical configurations"""
        # Add any additional validation logic here
        return True

# Singleton configuration instance
config = Config()