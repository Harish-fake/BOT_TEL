import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS: list[int] = field(
        default_factory=lambda: [
            int(x.strip())
            for x in os.getenv("ADMIN_IDS", "").split(",")
            if x.strip()
        ] if os.getenv("ADMIN_IDS") else []
    )
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "database/bot.db")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "2048"))
    MAX_ZIP_ENTRIES: int = int(os.getenv("MAX_ZIP_ENTRIES", "10000"))
    CONNECT_TIMEOUT: float = float(os.getenv("CONNECT_TIMEOUT", "60.0"))
    READ_TIMEOUT: float = float(os.getenv("READ_TIMEOUT", "60.0"))
    WRITE_TIMEOUT: float = float(os.getenv("WRITE_TIMEOUT", "60.0"))


config = Config()
