# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, Optional
import logging
from config import Config
from database.base import MongoDB
from database.user import UserManager
from utils.torrent import TorrentClient

if TYPE_CHECKING:
    from bot.bot import Bot

logger = logging.getLogger(__name__)

class Dependencies:

    def __init__(self):
        self.config = Config()
        self.mongo = MongoDB(self.config.MONGO_URI, "torrent_bot")
        self.user_manager = UserManager(self.mongo)

        # Initialisation différée du client torrent
        self.torrent_client: Optional[TorrentClient] = None
        self.bot: Optional['Bot'] = None

    async def initialize_torrent_client(self):
        try:
            logger.info("Initialisation du client torrent...")

            config = self.config.TORRENT_CONFIG

            self.torrent_client = TorrentClient(
                dl_dir=config.get("DL_DIR", "./data/downloads"),
                ports=(
                    config.get("MIN_PORT", 6881),
                    config.get("MAX_PORT", 6891)
                ),
                max_up=config.get("MAX_UPLOAD", 1000),
                max_dl=config.get("MAX_DOWNLOAD", -1),
                dht=config.get("DHT_ENABLED", True),
                upnp=config.get("UPNP_ENABLED", True),
                natpmp=config.get("NATPMP_ENABLED", True),
                trackers=config.get("TRACKERS", None),
                max_torrents=config.get("MAX_TORRENTS", 5),
                cache=config.get("CACHE_SIZE", 1024),
                max_http_downloads=config.get("MAX_HTTP_DOWNLOADS", 3),
                max_youtube_dl_downloads=config.get("MAX_YOUTUBE_DL", 3),
                max_aria2_downloads=config.get("MAX_ARIA2", 3),
                aria2_path=config.get("ARIA2_PATH", "aria2c"),
                max_tasks_per_user=config.get("MAX_TASKS_PER_USER", 10)
            )

            if not await self.torrent_client.check_connection():
                logger.error("Échec de la connexion au client torrent")
                raise ConnectionError("Impossible de se connecter au backend torrent")

            logger.info("Client torrent initialisé avec succès")
            return True
        except Exception as e:
            logger.exception("Erreur critique lors de l'initialisation du client torrent")
            self.torrent_client = None
            return False

    def initialize_bot(self) -> 'Bot':
        from bot.bot import Bot

        self.bot = Bot(
            mongo=self.mongo,
            config=self.config,
            torrent=self.torrent_client,
            usermanager=self.user_manager,
            session_name=self.config.SESSION_NAME
        )
        return self.bot

    async def startup(self):
        await self.mongo.connect()
        logger.info("Connecté à MongoDB")

        torrent_success = await self.initialize_torrent_client()
        if not torrent_success:
            logger.critical("Échec de l'initialisation du client torrent - Arrêt du système")
            raise SystemExit("Client torrent indisponible")

        self.initialize_bot()
        logger.info("Toutes les dépendances sont initialisées")

    async def shutdown(self):
        logger.info("Début de la procédure d'arrêt...")

        if self.bot:
            await self.bot.stop()
            logger.info("Bot arrêté")

        if self.torrent_client:
            await self.torrent_client.shutdown()
            logger.info("Client torrent arrêté")

        await self.mongo.disconnect()
        logger.info("Déconnecté de MongoDB")

        logger.info("Arrêt complet réussi")


# Singleton global
deps: Optional[Dependencies] = None

def get_deps() -> Dependencies:
    global deps
    if deps is None:
        deps = Dependencies()
    return deps
