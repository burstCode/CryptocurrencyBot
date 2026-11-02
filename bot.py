import asyncio
import logging
import math
import re
from html import escape
from time import monotonic
from contextlib import suppress
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message
from aiogram.utils.markdown import hbold

from config import load_settings
from broadcast import BroadcastSettings

API_URL = "https://api.coingecko.com/api/v3/simple/price"
TIMEZONE = ZoneInfo("Europe/Moscow")
COINS = {"bitcoin": "Биткоин", "ethereum": "Эфириум", "litecoin": "Лайткоин"}
CURRENCIES = {"usd": "$", "eur": "€", "rub": "₽"}
logger = logging.getLogger(__name__)


class RateError(Exception):
    """Не удалось получить полный набор котировок."""


class PriceClient:
    def __init__(self, session: aiohttp.ClientSession, api_key: str):
        self.session = session
        self.api_key = api_key
        self._coins = None
        self._coins_until = 0.0
        self._coins_lock = asyncio.Lock()

    async def request(self, path: str, params: dict | None = None):
        try:
            async with self.session.get(
                "https://api.coingecko.com/api/v3/" + path,
                params=params,
                headers={"x-cg-demo-api-key": self.api_key},
            ) as response:
                if response.status != 200:
                    raise RateError(f"CoinGecko вернул HTTP {response.status}")
                data = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise RateError("Ошибка соединения или некорректный ответ CoinGecko") from None
        return data

    async def fetch(self, coin_ids=None) -> dict:
        coin_ids = tuple(COINS) if coin_ids is None else tuple(coin_ids)
        data = await self.request("simple/price", {
            "ids": ",".join(coin_ids), "vs_currencies": ",".join(CURRENCIES),
            "precision": "full",
        })
        if not isinstance(data, dict):
            raise RateError("Неожиданный формат ответа CoinGecko")
        for coin in coin_ids:
            values = data.get(coin)
            if not isinstance(values, dict) or any(
                type(values.get(currency)) not in (int, float)
                or not math.isfinite(values[currency]) for currency in CURRENCIES
            ):
                raise RateError("В ответе CoinGecko отсутствуют нужные котировки")
        return {coin: {currency: data[coin][currency] for currency in CURRENCIES} for coin in coin_ids}


    async def coins(self) -> list[dict]:
        async with self._coins_lock:
            if self._coins is not None and monotonic() < self._coins_until:
                return self._coins
            data = await self.request("coins/list")
            if not isinstance(data, list) or not data or any(
                not isinstance(coin, dict) or any(
                    not isinstance(coin.get(key), str) or (key == "id" and not coin[key])
                    for key in ("id", "name", "symbol")
                ) for coin in data
            ):
                raise RateError("Неожиданный формат списка монет")
            self._coins = sorted(data, key=lambda coin: coin["id"])
            self._coins_until = monotonic() + 3600
            return self._coins


def coin_page(coins: list[dict], args: str | None) -> str:
    parts = (args or "").split()
    page = int(parts.pop()) if parts and parts[-1].isascii() and parts[-1].isdigit() else 1
    query = " ".join(parts).casefold()
    matches = [coin for coin in coins if any(
        query in coin[key].casefold() for key in ("id", "name", "symbol")
    )]
    if not matches:
        return "Монеты не найдены. Попробуйте /coins bitcoin или /coins solana."
    if query:
        matches.sort(key=lambda coin: (
            0 if coin["id"].casefold() == query else
            1 if coin["symbol"].casefold() == query else
            2 if coin["name"].casefold() == query else 3,
            coin["id"],
        ))
    pages = (len(matches) + 9) // 10
    if not 1 <= page <= pages:
        return f"Номер страницы должен быть от 1 до {pages}."
    lines = [f"Монеты: {len(matches)}. Страница {page}/{pages}.", ""]
    for coin in matches[(page - 1) * 10:page * 10]:
        name, symbol, coin_id = (escape(coin[key][:80]) for key in ("name", "symbol", "id"))
        lines.append(f"{name} ({symbol}) – <code>{coin_id}</code>")
    lines.extend(["", "Курс: /rate ID (например, /rate bitcoin)."])
    if page < pages:
        lines.append(f"Следующая страница: /coins {escape(query[:100])} {page + 1}")
    return "\n".join(lines)


def format_coin_rate(coin_id: str, data: dict) -> str:
    now = datetime.now(TIMEZONE)
    lines = [f"<b>{escape(coin_id)}</b> на {now:%d.%m.%Y %H:%M} (МСК):"]
    for currency, symbol in CURRENCIES.items():
        value = data[coin_id][currency]
        # Significant digits keep tiny token prices visible instead of rounding to zero.
        lines.append(f"{currency.upper()}: {value:,.10g} {symbol}")
    return "\n".join(lines)


def format_rates(data: dict, now: datetime, previous=None) -> str:
    lines = [f"Курс криптовалют на {now:%d.%m.%Y %H:%M} (МСК):", ""]
    for coin, name in COINS.items():
        values = ", ".join(f"{data[coin][currency]:,.2f} {symbol}" for currency, symbol in CURRENCIES.items())
        lines.append(f"{name}: {values}")
    if previous is not None:
        old_data, old_time = previous
        lines.extend(["", f"Изменение с {old_time:%d.%m.%Y %H:%M} (МСК):"])
        for coin, name in COINS.items():
            changes = ", ".join(
                f"{data[coin][currency] - old_data[coin][currency]:+,.2f} {symbol}"
                for currency, symbol in CURRENCIES.items()
            )
            lines.append(f"{name}: {changes}")
    return "\n".join(lines)


class RateSender:
    def __init__(self, client: PriceClient):
        self.client = client
        self.history: dict[int, tuple[dict, datetime]] = {}
        self.lock = asyncio.Lock()

    async def send(self, bot: Bot, chat_id: int):
        async with self.lock:
            data = await self.client.fetch()
            now = datetime.now(TIMEZONE)
            await bot.send_message(chat_id, format_rates(data, now, self.history.get(chat_id)))
            self.history[chat_id] = (data, now)


async def send_periodically(bot: Bot, sender: RateSender, chat_id: int, broadcast: BroadcastSettings):
    while True:
        await broadcast.wait()
        try:
            await sender.send(bot, chat_id)
        except (RateError, TelegramAPIError) as exc:
            logger.warning("Рассылка не выполнена: %s", type(exc).__name__)


def create_dispatcher(sender: RateSender, broadcast: BroadcastSettings | None = None) -> Dispatcher:
    if broadcast is None:
        broadcast = BroadcastSettings()
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start(message: Message):
        name = message.from_user.full_name if message.from_user else "друг"
        await message.answer(
            f"Приветствую, {hbold(name)}!\n"
            "Курсы: /cryptocurrency_rate\nМонеты: /coins\nКурс монеты: /rate bitcoin\nО боте: /about\nID чата: /chat_id\nМой ID: /my_id\nИнтервал рассылки: /interval"
        )

    @dp.message(Command("about"))
    async def about(message: Message):
        await message.answer(
            "Показываю курсы BTC, ETH и LTC в USD, EUR и RUB по данным CoinGecko.\n"
            "Текущие курсы: /cryptocurrency_rate\n"
            "Поиск монет: /coins solana; курс по CoinGecko ID: /rate solana\n"
            "Периодическая рассылка отправляется в чат, если она настроена.\n"
            "Интервал рассылки по умолчанию: 2 часа."
        )

    @dp.message(Command("coins"))
    async def coins(message: Message, command: CommandObject):
        if command.args and len(command.args) > 120:
            await message.answer("Слишком длинный запрос. Пример: /coins bitcoin 1")
            return
        try:
            await message.answer(coin_page(await sender.client.coins(), command.args))
        except RateError as exc:
            logger.warning("Список монет недоступен: %s", exc)
            await message.answer("Не удалось загрузить список монет. Попробуйте позже.")

    @dp.message(Command("rate"))
    async def coin_rate(message: Message, command: CommandObject):
        coin_id = (command.args or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,199}", coin_id):
            await message.answer("Используйте /rate ID, например /rate solana. Найти ID: /coins solana")
            return
        try:
            data = await sender.client.fetch([coin_id])
            await message.answer(format_coin_rate(coin_id, data))
        except RateError as exc:
            logger.warning("Курс монеты недоступен: %s", exc)
            await message.answer(
                "Курс недоступен: проверьте ID через /coins или повторите запрос позже."
            )

    @dp.message(Command("my_id"))
    async def my_id(message: Message):
        if message.from_user:
            await message.answer(f"Ваш ID: <code>{message.from_user.id}</code>")

    @dp.message(Command("interval"))
    async def interval(message: Message, command: CommandObject):
        if not command.args:
            await message.answer(
                f"Интервал рассылки: {broadcast.minutes} мин.\n"
                "Администратор может изменить: /interval 30"
            )
            return
        user_id = message.from_user.id if message.from_user and not message.sender_chat else None
        try:
            broadcast.update(user_id, command.args.strip())
        except (PermissionError, ValueError) as exc:
            await message.answer(str(exc))
            return
        except OSError:
            logger.warning("Не удалось сохранить интервал рассылки")
            await message.answer("Не удалось сохранить настройку. Интервал не изменён.")
            return
        await message.answer(
            f"Интервал сохранён: {broadcast.minutes} мин.\n"
            "Отсчёт начнётся заново.."
        )

    @dp.message(Command("chat_id"))
    async def chat_id(message: Message):
        await message.answer(f"ID этого чата: <code>{message.chat.id}</code>")

    @dp.message(Command("cryptocurrency_rate"))
    async def rate(message: Message, bot: Bot):
        try:
            await sender.send(bot, message.chat.id)
        except RateError as exc:
            logger.warning("Запрос котировок не выполнен: %s", exc)
            await message.answer("Не удалось получить курсы. Попробуйте немного позже.")

    return dp


async def main():
    settings = load_settings()
    broadcast = BroadcastSettings(settings.interval_minutes, settings.admin_ids)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        sender = RateSender(PriceClient(session, settings.coingecko_api_key))
        dp = create_dispatcher(sender, broadcast)
        async with Bot(settings.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) as bot:
            task = None
            if settings.chat_id is not None:
                task = asyncio.create_task(send_periodically(bot, sender, settings.chat_id, broadcast))
                logger.info("Рассылка включена, интервал %s мин.", broadcast.minutes)
            else:
                logger.info("CHAT_ID не задан: доступны только команды")
            try:
                await dp.start_polling(bot, close_bot_session=False)
            finally:
                if task is not None:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except ValueError as exc:
        logger.error("Ошибка конфигурации: %s", exc)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass
