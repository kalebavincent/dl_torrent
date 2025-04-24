from typing import Optional, List, Dict, Any, AsyncGenerator
import motor.motor_asyncio
from motor.core import AgnosticClient, AgnosticDatabase, AgnosticCollection
from pymongo.errors import PyMongoError
from pymongo import IndexModel
import logging

log = logging.getLogger(__name__)

class MongoDB:
    def __init__(self, uri: str, db_name: str, max_pool: int = 100, min_pool: int = 10):
        self.uri = uri
        self.db_name = db_name
        self.max_pool = max_pool
        self.min_pool = min_pool
        self._client: Optional[AgnosticClient] = None
        self._database: Optional[AgnosticDatabase] = None

    async def connect(self) -> None:
        """Établit la connexion à MongoDB"""
        if self._client is not None:
            return

        try:
            self._client = motor.motor_asyncio.AsyncIOMotorClient(
                self.uri,
                maxPoolSize=self.max_pool,
                minPoolSize=self.min_pool,
                connectTimeoutMS=5000,
                serverSelectionTimeoutMS=5000
            )
            self._database = self._client[self.db_name]
            await self._client.admin.command('ping')
            log.info(f"Connected to MongoDB database '{self.db_name}'")
        except PyMongoError as e:
            log.error(f"MongoDB connection failed: {e}")
            self._client = None
            self._database = None
            raise ConnectionError(f"Could not connect to MongoDB: {e}")

    async def disconnect(self) -> None:
        """Ferme la connexion à MongoDB"""
        if self._client is not None:
            try:
                self._client.close()
                log.info("MongoDB connection closed")
            except Exception as e:
                log.error(f"Error closing MongoDB connection: {e}")
            finally:
                self._client = None
                self._database = None

    async def is_connected(self) -> bool:
        """Vérifie si la connexion est active"""
        try:
            if self._client is None:
                return False
            await self._client.admin.command('ping')
            return True
        except PyMongoError:
            return False

    def get_collection(self, collection_name: str) -> AgnosticCollection:
        """Récupère une collection MongoDB"""
        if self._database is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._database[collection_name]

    async def create_indexes(self, collection_name: str, indexes: List[IndexModel]) -> None:
        """Crée des index sur une collection"""
        try:
            collection = self.get_collection(collection_name)
            await collection.create_indexes(indexes)
            log.debug(f"Created indexes on collection '{collection_name}'")
        except PyMongoError as e:
            log.error(f"Failed to create indexes: {e}")
            raise

    async def insert_document(self, collection_name: str, document: Dict) -> str:
        """Insère un document et retourne son ID"""
        try:
            result = await self.get_collection(collection_name).insert_one(document)
            return str(result.inserted_id)
        except PyMongoError as e:
            log.error(f"Failed to insert document: {e}")
            raise

    async def find_document(self, collection_name: str, query: Dict, **kwargs) -> Optional[Dict]:
        """Trouve un document unique"""
        try:
            return await self.get_collection(collection_name).find_one(query, **kwargs)
        except PyMongoError as e:
            log.error(f"Failed to find document: {e}")
            raise

    async def find_many_documents(
        self, 
        collection_name: str, 
        query: Dict, 
        limit: int = 100,
        sort: Optional[List[tuple]] = None, 
        **kwargs
    ) -> AsyncGenerator[Dict, None]:
        """Trouve plusieurs documents avec pagination"""
        try:
            cursor = self.get_collection(collection_name).find(query, **kwargs).limit(limit)
            if sort is not None:
                cursor = cursor.sort(sort)
            async for document in cursor:
                yield document
        except PyMongoError as e:
            log.error(f"Failed to find documents: {e}")
            raise

    async def update_document(
        self,
        collection_name: str,
        query: Dict,
        update_data: Dict,
        upsert: bool = False,
        **kwargs
    ) -> bool:
        """Met à jour un document"""
        try:
            result = await self.get_collection(collection_name).update_one(
                query,
                {"$set": update_data},
                upsert=upsert,
                **kwargs
            )
            return result.modified_count > 0
        except PyMongoError as e:
            log.error(f"Failed to update document: {e}")
            raise

    async def delete_document(self, collection_name: str, query: Dict, **kwargs) -> bool:
        """Supprime un document"""
        try:
            result = await self.get_collection(collection_name).delete_one(query, **kwargs)
            return result.deleted_count > 0
        except PyMongoError as e:
            log.error(f"Failed to delete document: {e}")
            raise

    async def count_documents(self, collection_name: str, query: Dict, **kwargs) -> int:
        """Compte les documents correspondants"""
        try:
            return await self.get_collection(collection_name).count_documents(query, **kwargs)
        except PyMongoError as e:
            log.error(f"Failed to count documents: {e}")
            raise

    async def aggregate(self, collection_name: str, pipeline: List[Dict], **kwargs) -> List[Dict]:
        """Exécute un pipeline d'aggregation"""
        try:
            cursor = self.get_collection(collection_name).aggregate(pipeline, **kwargs)
            return await cursor.to_list(length=None)
        except PyMongoError as e:
            log.error(f"Failed to execute aggregation: {e}")
            raise

    async def execute_transaction(self, callback: callable, **kwargs) -> Any:
        """Exécute une transaction MongoDB"""
        if self._client is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        
        async with await self._client.start_session() as session:
            try:
                async with session.start_transaction():
                    return await callback(session=session, **kwargs)
            except PyMongoError as e:
                log.error(f"Transaction failed: {e}")
                await session.abort_transaction()
                raise