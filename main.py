# -*- coding: utf-8 -*-
import asyncio
from bot import Dependencies
from route import web_server
from aiohttp import web
import logging
from pyrogram import idle
from pathlib import Path
import signal

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

async def graceful_shutdown(deps: Dependencies, bot):
    """Gère l'arrêt propre de l'application"""
    logger.info("Début du shutdown gracieux...")

    try:
        await bot.stop()
    except Exception as e:
        logger.error(f"Erreur lors de l'arrêt du bot: {e}")

    try:
        await deps.shutdown()
    except Exception as e:
        logger.error(f"Erreur lors du shutdown des dépendances: {e}")

    logger.info("Bot arrêté avec succès")

async def main():
    """Point d'entrée principal de l'application"""
    deps = Dependencies()
    bot = None

    try:
        # Initialisation des dépendances
        await deps.startup()

        # Vérification des dossiers nécessaires
        Path("downloads").mkdir(exist_ok=True)
        Path("temp").mkdir(exist_ok=True)

        # Initialisation du bot
        bot = deps.initialize_bot()
        await bot.start()
        logger.info("Bot démarré avec succès")

        # Configuration des handlers de signal
        loop = asyncio.get_running_loop()

        def shutdown_handler():
            asyncio.create_task(graceful_shutdown(deps, bot))

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, shutdown_handler)

        # Démarrage du serveur web si configuré
        if deps.config.WEBHOOK:
            app = web.AppRunner(await web_server())
            await app.setup()

            site = web.TCPSite(app, deps.config.WEB_HOST, deps.config.WEB_PORT)
            await site.start()
            logger.info(f"Serveur web démarré sur {deps.config.WEB_HOST}:{deps.config.WEB_PORT}")

        # Nettoyage périodique
        async def periodic_tasks():
            while True:
                await asyncio.sleep(3600)  # Toutes les heures

                try:
                    await deps.torrent_client.cleanup_stalled_downloads()
                    logger.info("Nettoyage périodique effectué")
                except Exception as e:
                    logger.error(f"Erreur nettoyage périodique: {e}")

        asyncio.create_task(periodic_tasks())

        # Attente active
        await idle()

    except Exception as e:
        logger.critical(f"Erreur critique: {e}", exc_info=True)
    finally:
        if bot:
            await graceful_shutdown(deps, bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Arrêt demandé par l'utilisateur")
    except Exception as e:
        logger.critical(f"Erreur non gérée: {e}", exc_info=True)