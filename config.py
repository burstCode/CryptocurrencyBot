import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    token: str
    coingecko_api_key: str
    chat_id: int | None
    admin_ids: frozenset[int] = frozenset()
    interval_minutes: int = 60


def load_settings() -> Settings:
    load_dotenv(Path(__file__).with_name(".env"))
    token = os.getenv("TOKEN", "").strip()
    key = os.getenv("COINGECKO_API_KEY", "").strip()
    if not token or not key:
        raise ValueError("Заполните TOKEN и COINGECKO_API_KEY в .env")
    raw_chat_id = os.getenv("CHAT_ID", "").strip()
    try:
        chat_id = int(raw_chat_id) if raw_chat_id else None
    except ValueError:
        raise ValueError("CHAT_ID должен быть целым числом или пустым") from None
    try:
        admin_ids = frozenset(int(value.strip()) for value in os.getenv("ADMIN_IDS", "").split(",") if value.strip())
        if any(value <= 0 for value in admin_ids):
            raise ValueError
    except ValueError:
        raise ValueError("ADMIN_IDS: укажите положительные ID пользователей через запятую") from None
    interval = validate_interval(os.getenv("BROADCAST_INTERVAL_MINUTES", "60"))
    return Settings(token, key, chat_id, admin_ids, interval)


def validate_interval(value) -> int:
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
        raise ValueError("Интервал должен быть целым числом минут от 1 до 10080")
    minutes = int(value)
    if not 1 <= minutes <= 10080:
        raise ValueError("Интервал должен быть от 1 до 10080 минут (7 дней)")
    return minutes
