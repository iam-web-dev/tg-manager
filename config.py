import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Bot API
    BOT_TOKEN: str = Field(default="", description="Telegram Bot Token")

    # MTProto Userbot API (my.telegram.org)
    API_ID: Optional[int] = Field(default=None, description="Telegram App API ID")
    API_HASH: Optional[str] = Field(default=None, description="Telegram App API Hash")
    SESSION_NAME: str = Field(default="tg_session", description="Telethon session file name")

    # Xavfsizlik uchun Admin ID
    ADMIN_ID: Optional[int] = Field(default=None, description="Admin Telegram ID")

    # AI (Gemini) sozlamalari
    GEMINI_API_KEY: str = Field(default="", description="Google Gemini API Key")
    GEMINI_MODEL: str = Field(default="gemini-3.6-flash", description="Gemini Model Name")

    # Database
    DATABASE_URL: str = Field(
        default="",
        description="Database connection URL"
    )

    def get_db_url(self) -> str:
        if self.DATABASE_URL:
            db_url = self.DATABASE_URL
            if db_url.startswith("sqlite+aiosqlite:///"):
                path = db_url.replace("sqlite+aiosqlite:///", "")
                if not os.path.isabs(path):
                    return f"sqlite+aiosqlite:///{BASE_DIR / path}"
            return db_url
        return f"sqlite+aiosqlite:///{BASE_DIR / 'tg_manager.db'}"

    def get_session_path(self) -> str:
        if not os.path.isabs(self.SESSION_NAME):
            return str(BASE_DIR / self.SESSION_NAME)
        return self.SESSION_NAME

settings = Settings()
