# -*- coding: utf-8 -*-
import asyncio
from bot import Dependencies

async def main():
    deps = Dependencies()
    
    try:
        await deps.startup()
        bot = deps.initialize_bot()
        await bot.start()
        await bot.idle()
    finally:
        await deps.shutdown()

if __name__ == "__main__":
    asyncio.run(main())