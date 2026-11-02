import asyncio
import json
import logging
from pathlib import Path

from config import validate_interval

STATE_PATH = Path(__file__).with_name("broadcast_settings.json")


class BroadcastSettings:
    def __init__(self, default_minutes=60, admin_ids=frozenset(), path=STATE_PATH):
        self.path = path
        self.admin_ids = admin_ids
        self.minutes = validate_interval(default_minutes)
        self.changed = asyncio.Event()
        try:
            saved = json.loads(path.read_text())
            self.minutes = validate_interval(saved["interval_minutes"])
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError):
            logging.getLogger(__name__).warning("Не удалось прочитать сохранённый интервал; используется конфигурация .env")

    def update(self, user_id: int | None, value):
        if user_id is None or user_id not in self.admin_ids:
            raise PermissionError("Менять интервал может только администратор из ADMIN_IDS")
        minutes = validate_interval(value)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps({"interval_minutes": minutes}) + "\n")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.minutes = minutes
        self.changed.set()

    async def wait(self):
        while True:
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=self.minutes * 60)
            except asyncio.TimeoutError:
                return
