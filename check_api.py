import asyncio
from datetime import datetime

import aiohttp

from bot import PriceClient, RateError, TIMEZONE, format_rates
from config import load_settings


async def main():
    settings = load_settings()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
        data = await PriceClient(session, settings.coingecko_api_key).fetch()
    print(format_rates(data, datetime.now(TIMEZONE)))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (ValueError, RateError) as exc:
        raise SystemExit(str(exc)) from None
