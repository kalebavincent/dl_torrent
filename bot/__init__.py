# -*- coding: utf-8 -*-
from typing import TYPE_CHECKING, Optional
from database.user import UserManager
from database.base import MongoDB
from utils.torrent import TorrentClient
from config import Config


if TYPE_CHECKING:
    from bot.bot import Bot
    
class Dependencies:
    
    def __init__(self):
        self.config = Config()
        
        self.mongo = MongoDB(self.config.MONGO_URI, "torrent_bot")
        
        self.user_manager = UserManager(self.mongo)  
        self.torrent_client = TorrentClient()  
        
        self.bot: Optional['Bot'] = None
    
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
        
    
    async def shutdown(self):
        """Nettoie les ressources."""
        if self.bot:
            await self.bot.stop()
        await self.mongo.disconnect()
