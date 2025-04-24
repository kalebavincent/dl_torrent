# -*- coding: utf-8 -*-
import asyncio
from bot import Dependencies
from route import web_server
from aiohttp import web

async def main():
    deps = Dependencies()
    
    try:
        await deps.startup()
        bot = deps.initialize_bot()
        await bot.start()
        if deps.config.WEBHOOK:
            app = web.AppRunner(await web_server())
            await app.setup()       
            await web.TCPSite(app, "0.0.0.0", 8080).start()     
        await bot.idle()
    finally:
        await deps.shutdown()

if __name__ == "__main__":
    asyncio.run(main())