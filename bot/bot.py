from pyrogram import Client
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import Config
    from database.base import MongoDB
    from database.user import UserManager
    from utils.torrent import TorrentClient

log = logging.getLogger(__name__)

class Bot:
    def __init__(
        self,
        mongo: 'MongoDB',
        config: 'Config',
        torrent: 'TorrentClient',
        usermanager: 'UserManager',
        session_name: str = "my_bot"
    ):
        """
        Initialise le bot Telegram avec ses dépendances principales.
        
        Args:
            mongo: Instance MongoDB pour la base de données
            config: Configuration de l'application
            torrent: Client Torrent pour les téléchargements
            usermanager: Gestionnaire des utilisateurs
            session_name: Nom de la session Pyrogram
        """
        self.usermanager = usermanager
        self.mongo = mongo
        self.config = config
        self.torrent = torrent
        
        self.client = Client(
            session_name,
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            plugins={"root": "plugins"}
        )

    async def start(self) -> None:
        """Démarre le bot et établit les connexions."""
        try:
            await self.mongo.connect()
            await self.client.start()
            
            me = await self.client.get_me()
            log.info(f"Bot démarré: @{me.username} (ID: {me.id})")
        except Exception as e:
            log.error(f"Erreur au démarrage: {e}")
            raise

    async def stop(self) -> None:
        """Arrête le bot proprement."""
        try:
            await self.client.stop()
            await self.mongo.disconnect()
            log.info("Bot arrêté proprement")
        except Exception as e:
            log.error(f"Erreur à l'arrêt: {e}")
            raise

    async def idle(self) -> None:
        """Maintient le bot en fonctionnement."""
        from pyrogram import idle
        await idle()