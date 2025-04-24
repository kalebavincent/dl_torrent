import asyncio
import io
import logging
import logging.handlers
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Callable, Union, Any
import libtorrent as lt
import requests
import hashlib
import zipfile
import psutil

# Configuration logging simplifiée
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.handlers.RotatingFileHandler(
            'torrent.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

class TorrentState(Enum):
    IDLE, METADATA, CHECKING, DOWNLOADING, SEEDING, PAUSED, COMPLETED, ERROR = range(8)
    
    def __str__(self):
        states = {
            0: "En attente", 1: "Métadonnées", 2: "Vérification",
            3: "Téléchargement", 4: "Partage", 5: "Pause",
            6: "Terminé", 7: "Erreur"
        }
        return states.get(self.value, "Inconnu")

@dataclass
class TorrentStats:
    progress: float
    dl_rate: float  # kB/s
    ul_rate: float  # kB/s
    speed: float    # MB/s
    eta: float
    peers: int
    state: TorrentState
    wanted: float   # MB
    done: float     # MB
    downloaded: float  # MB
    uploaded: float    # MB
    files: List[Dict] = field(default_factory=list)
    disk: Optional[Dict] = None

    def __str__(self):
        disk = f"\nDisque: {self.disk['used']:.1f}/{self.disk['total']:.1f}GB ({self.disk['percent']}%)" if self.disk else ""
        return (f"Progression: {self.progress:.1f}%\n"
                f"Vitesse: ↓{self.dl_rate:.1f}kB/s / ↑{self.ul_rate:.1f}kB/s\n"
                f"Débit: {self.speed:.2f}MB/s\nPairs: {self.peers}\n"
                f"État: {self.state}\nETA: {self.eta:.0f}s\n"
                f"Taille: {self.done:.1f}/{self.wanted:.1f}MB{disk}")

class TorrentClient:
    def __init__(
        self,
        dl_dir: Union[str, Path] = "./downloads",
        ports: Tuple[int, int] = (6881, 6891),
        max_up: int = 1000,  # kB/s
        max_dl: int = -1,     # kB/s
        dht: bool = True,
        upnp: bool = True,
        natpmp: bool = True,
        trackers: Optional[List[str]] = None,
        max_torrents: int = 5,
        cache: int = 1024    # MB
    ):
        self.dl_dir = Path(dl_dir).absolute()
        self.dl_dir.mkdir(parents=True, exist_ok=True)
        self.ports = ports
        self.max_torrents = max_torrents
        self.executor = ThreadPoolExecutor(4)
        self.handles: Dict[str, lt.torrent_handle] = {}
        self.trackers = trackers or self._default_trackers()
        self._init_session(max_up, max_dl, dht, upnp, natpmp, cache)
        self._setup_signals()
        log.info(f"Client initialisé: {self.dl_dir}")

    def _setup_signals(self):
        if sys.platform == 'win32':
            try:
                import win32api
                win32api.SetConsoleCtrlHandler(lambda _: asyncio.create_task(self.close()), True)
            except ImportError:
                log.warning("Pas de gestion de signal sous Windows")
        else:
            try:
                loop = asyncio.get_event_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, lambda: asyncio.create_task(self.close()))
            except Exception as e:
                log.warning(f"Erreur signal: {e}")

    @staticmethod
    def _default_trackers() -> List[str]:
        return [
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://open.tracker.cl:1337/announce",
            "udp://9.rarbg.com:2810/announce",
            "udp://tracker.openbittorrent.com:6969/announce"
        ]

    def _init_session(self, max_up, max_dl, dht, upnp, natpmp, cache):
        self.session = lt.session()
        self.session.listen_on(*self.ports)
        self.session.apply_settings({
            "upload_rate_limit": max_up * 1024,
            "download_rate_limit": max_dl * 1024,
            "enable_dht": dht,
            "enable_upnp": upnp,
            "enable_natpmp": natpmp,
            "alert_mask": lt.alert.category_t.all_categories,
            "active_downloads": self.max_torrents,
            "cache_size": cache
        })

    def _disk_space(self, size: int) -> bool:
        return psutil.disk_usage(str(self.dl_dir)).free > size * 1.2

    def _get_info(self, source: str) -> Optional[lt.torrent_info]:
        try:
            if source.startswith(('http://', 'https://')):
                r = requests.get(source, timeout=10)
                r.raise_for_status()
                return lt.torrent_info(lt.bdecode(r.content))
            return lt.torrent_info(source)
        except Exception as e:
            log.error(f"Erreur torrent {source}: {e}")
            return None

    async def add(self, source: str, path: Optional[Path] = None, paused=False, cb=None) -> Optional[str]:
        try:
            p = Path(path) if path else self.dl_dir
            p = p.absolute()
            p.mkdir(parents=True, exist_ok=True)

            if not source.startswith('magnet:'):
                info = await asyncio.get_event_loop().run_in_executor(self.executor, self._get_info, source)
                if info and not self._disk_space(info.total_size()):
                    raise RuntimeError("Espace disque insuffisant")

            params = {
                'save_path': str(p),
                # 'paused': paused,
                'storage_mode': lt.storage_mode_t.storage_mode_sparse,
                'trackers': self.trackers
            }

            if source.startswith('magnet:'):
                h = lt.add_magnet_uri(self.session, source, params)
            else:
                if not info: return None
                params['ti'] = info
                h = self.session.add_torrent(params)

            tid = hashlib.sha256(h.info_hash().to_bytes()).hexdigest()[:16]
            self.handles[tid] = h
            log.info(f"Torrent ajouté {tid}: {h.name()}")

            if cb and not paused:
                asyncio.create_task(self._progress(tid, cb))
            return tid
        except Exception as e:
            log.error(f"Erreur ajout: {e}", exc_info=True)
            raise

    async def _progress(self, tid: str, cb: Callable, interval=5):
        while tid in self.handles:
            stats = await self.stats(tid)
            if stats:
                try: cb(stats)
                except Exception as e: log.error(f"Erreur callback: {e}")
                if stats.state in (TorrentState.COMPLETED, TorrentState.ERROR): break
            await asyncio.sleep(interval)

    async def stats(self, tid: str) -> Optional[TorrentStats]:
        if tid not in self.handles: return None
        
        try:
            h, s = self.handles[tid], self.handles[tid].status()
            state_map = {
                lt.torrent_status.states.downloading_metadata: TorrentState.METADATA,
                lt.torrent_status.states.checking_files: TorrentState.CHECKING,
                lt.torrent_status.states.downloading: TorrentState.DOWNLOADING,
                lt.torrent_status.states.finished: TorrentState.COMPLETED,
                lt.torrent_status.states.seeding: TorrentState.SEEDING
            }
            state = state_map.get(s.state, TorrentState.PAUSED if s.paused else TorrentState.ERROR)

            files = []
            if h.has_metadata():
                info = h.get_torrent_info()
                for idx in range(info.num_files()):
                    f = info.file_at(idx)
                    files.append({
                        'path': f.path,
                        'size': f.size / (1024*1024),
                        'priority': h.file_priority(idx),
                        'progress': h.file_progress(idx) * 100
                    })

            try:
                disk = psutil.disk_usage(str(self.dl_dir))
                disk = {
                    'total': disk.total / (1024**3),
                    'used': disk.used / (1024**3),
                    'percent': disk.percent
                }
            except Exception: disk = None

            return TorrentStats(
                progress=s.progress * 100,
                dl_rate=s.download_rate / 1024,  
                ul_rate=s.upload_rate / 1024,     
                speed=(s.download_rate / 1024) / 1024,
                eta=((s.total_wanted - s.total_wanted_done) / s.download_rate) if s.download_rate > 0 else float('inf'),
                peers=s.num_peers,
                state=state,
                wanted=s.total_wanted / (1024*1024),      
                done=s.total_wanted_done / (1024*1024),
                downloaded=s.total_payload_download / (1024*1024),
                uploaded=s.all_time_upload / (1024*1024),  
                files=files,
                disk=disk
            )
        except Exception as e:
            log.error(f"Erreur stats: {e}")
            return None
    
    async def remove(self, tid: str, delete_data: bool = True, wait_resume_data: bool = False):
        """Supprime un torrent 
        
        Args:
            tid: ID du torrent
            delete_data: Si True, supprime les fichiers téléchargés
            wait_resume_data: Si True, attend la sauvegarde des données de reprise
        """
        if tid not in self.handles:
            log.warning(f"Torrent {tid} introuvable pour suppression")
            return False

        try:
            h = self.handles[tid]
            
            if wait_resume_data and not delete_data:
                h.save_resume_data()
                await asyncio.sleep(1)  

            self.session.remove_torrent(h, int(delete_data))
            
            del self.handles[tid]
            log.info(f"Torrent {tid} supprimé (fichiers: {'oui' if delete_data else 'non'})")
            return True
            
        except Exception as e:
            log.error(f"Échec suppression {tid}: {str(e)}", exc_info=True)
            try:
                if tid in self.handles:
                    del self.handles[tid]
            except:
                pass
            return False

    async def close(self):
        for tid in list(self.handles):
            try:
                self.session.remove_torrent(self.handles[tid], False)
                del self.handles[tid]
            except Exception: pass
        self.session.pause()
        self.executor.shutdown()

    async def __aenter__(self): return self
    async def __aexit__(self, *exc): await self.close()