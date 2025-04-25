from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, GetCoreSchemaHandler, validator
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema
from uuid import UUID, uuid4
from bson import ObjectId
from pymongo import IndexModel, ASCENDING

class PyObjId(ObjectId):
    
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler: GetCoreSchemaHandler):
        return core_schema.json_or_python_schema(
            json_schema=core_schema.str_schema(),
            python_schema=core_schema.no_info_after_validator_function(cls.validate, core_schema.any_schema()),
        )

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return v
        if isinstance(v, str):
            return ObjectId(v)
        raise TypeError("Invalid ObjectId")

    @classmethod
    def __get_pydantic_json_schema__(cls, schema, handler):
        return {"type": "string"}

class MongoModel(BaseModel):
    id: PyObjId = Field(default_factory=PyObjId, alias="_id")

    class Config:
        json_encoders = {ObjectId: str}
        validate_by_name = True
        arbitrary_types_allowed = True

class Role(str, Enum):
    USER = "user"
    VIP = "vip"
    ADMIN = "admin"
    SUPER = "super"

class SubTier(str, Enum):
    FREE = "free"
    TRIAL = "trial"
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD = "gold"
    PLAT = "platinum"
    ENTERPRISE = "enterprise"

class DLStatus(str, Enum):
    QUEUE = "queued"
    DL = "downloading"
    SEED = "seeding"
    DONE = "completed"
    PAUSE = "paused"
    FAIL = "failed"

class TorrentFile(MongoModel):
    fid: UUID = Field(default_factory=uuid4)
    path: str
    size: float = Field(..., gt=0)
    prio: int = Field(4, ge=1, le=7)
    sel: bool = True

class DLProgress(MongoModel):
    did: PyObjId = Field(default_factory=PyObjId, alias="_id")
    magnet: Optional[str] = None
    torrent: Optional[str] = None
    name: str = "Unnamed"
    status: DLStatus = DLStatus.QUEUE
    progress: float = Field(0.0, ge=0, le=100)
    speed: float = Field(0.0, ge=0)
    size: float = Field(0.0, ge=0)
    eta: float = Field(0.0, ge=0)
    files: List[TorrentFile] = []
    created: datetime = Field(default_factory=datetime.now)
    updated: datetime = Field(default_factory=datetime.now)
    
    class Config:
        extra = 'ignore'

    @validator('name', pre=True)
    def set_name(cls, v, values):
        if not v:
            if values.get('magnet'):
                return next(
                    (p[3:] for p in values['magnet'].split('&') 
                    if p.startswith('dn=')),
                    "Unnamed"
                )
            if values.get('torrent'):
                return Path(values['torrent']).stem
        return v or "Unnamed"

class Quotas(MongoModel):
    max_dls: int = Field(3, ge=1)
    dl_speed: Optional[float] = Field(None, ge=0)
    up_speed: Optional[float] = Field(None, ge=0)
    space: Optional[float] = Field(None, ge=0)
    bw: Optional[float] = Field(None, ge=0)

class Stats(MongoModel):
    dls: int = Field(0, ge=0)
    up: float = Field(0.0, ge=0)
    down: float = Field(0.0, ge=0)
    avg_dl: float = Field(0.0, ge=0)
    avg_up: float = Field(0.0, ge=0)
    last_active: Optional[datetime] = None

class Settings(MongoModel):
    dark: bool = False
    notifs: bool = True
    dl_path: str = "downloads"
    auto_del: bool = False
    max_parallel: int = Field(3, ge=1)

class UserCreate(MongoModel):
    uid: int = Field(..., unique=True)
    uname: Optional[str] = Field(None, min_length=3)
    first: Optional[str] = None
    last: Optional[str] = None
    lang_code: Optional[str] = None
    sub: SubTier = SubTier.FREE
    role: Role = Role.USER

class UserUpdate(MongoModel):
    uname: Optional[str] = Field(None, min_length=3)
    sub: Optional[SubTier] = None
    settings: Optional[Settings] = None
    role: Optional[Role] = None
    updated: Optional[datetime] = Field(default_factory=datetime.now)

class UserDB(MongoModel):
    uid: int = Field(..., unique=True)
    uname: Optional[str] = Field(None, min_length=3)
    first: Optional[str] = None
    last: Optional[str] = None
    lang_code: Optional[str] = None
    role: Role = Role.USER
    sub: SubTier = SubTier.FREE
    quotas: Quotas = Field(default_factory=Quotas)
    stats: Stats = Field(default_factory=Stats)
    dl_active: List[DLProgress] = []
    dl_done: List[DLProgress] = []
    settings: Settings = Field(default_factory=Settings)
    active: List[DLProgress] = []
    done: List[DLProgress] = []
    created: datetime = Field(default_factory=datetime.now)
    updated: datetime = Field(default_factory=datetime.now)

    def can_add_dl(self) -> bool:
        return len(self.active) < self.quotas.max_dls

    def add_dl(self, dl: DLProgress) -> bool:
        if not self.can_add_dl():
            return False
        self.active.append(dl)
        self.updated = datetime.now()
        self.stats.last_active = datetime.now()
        return True