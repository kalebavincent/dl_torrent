import os
import shutil
import tarfile
import time
import zipfile
import aiohttp
import asyncio
import hashlib
import logging
import logging.handlers
import signal
import sys
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Callable, Union, Any
import libtorrent as lt
import psutil
import requests
import urllib
import yt_dlp
import re

# Configuration logging
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

class DownloadType(Enum):
    TORRENT = auto()
    HTTP = auto()
    YOUTUBE_DL = auto()
    ARIA2 = auto()

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
class DownloadTask:
    type: DownloadType
    id: str
    handle: Optional[lt.torrent_handle] = None
    http_task: Optional[aiohttp.ClientResponse] = None
    progress: float = 0.0
    state: TorrentState = TorrentState.IDLE
    downloaded: float = 0  # MB
    total_size: float = 0  # MB
    speed: float = 0  # kB/s
    path: Optional[Path] = None
    ydl_info: dict = field(default_factory=dict)
    aria2_process: Optional[Any] = None
    user_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)

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
    user_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)

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
        cache: int = 1024,    # MB
        max_http_downloads: int = 3,
        max_youtube_dl_downloads: int = 3,
        max_aria2_downloads: int = 3,
        aria2_path: str = "aria2c",
        max_tasks_per_user: int = 10
    ):
        self.dl_dir = Path(dl_dir).absolute()
        self.dl_dir.mkdir(parents=True, exist_ok=True)
        self.ports = ports
        self.max_torrents = max_torrents
        self.max_http_downloads = max_http_downloads
        self.max_youtube_dl_downloads = max_youtube_dl_downloads
        self.max_aria2_downloads = max_aria2_downloads
        self.aria2_path = aria2_path
        self.max_tasks_per_user = max_tasks_per_user
        self.executor = ThreadPoolExecutor(8)
        self.handles: Dict[str, lt.torrent_handle] = {}
        self.download_tasks: Dict[str, DownloadTask] = {}
        self.trackers = trackers or self._default_trackers()
        self.http_session: Optional[aiohttp.ClientSession] = None
        self._init_session(max_up, max_dl, dht, upnp, natpmp, cache)
        self._setup_signals()
        self.user_tasks: Dict[str, List[str]] = {}
        log.info(f"Client initialisé avec support multi-sources: {self.dl_dir}")

    def _setup_signals(self):
        if sys.platform == 'win32':
            try:
                import win32api
                win32api.SetConsoleCtrlHandler(lambda _: asyncio.create_task(self.close()), True)
            except ImportError:
                log.warning("Signal handling not available on Windows")
        else:
            try:
                loop = asyncio.get_event_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, lambda: asyncio.create_task(self.close()))
            except Exception as e:
                log.warning(f"Signal error: {e}")

    @staticmethod
    def _default_trackers() -> List[str]:
        return  [
            # ➤ Trackers existants
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://open.tracker.cl:1337/announce",
            "udp://9.rarbg.com:2810/announce",
            "udp://tracker.openbittorrent.com:6969/announce",

            # ➤ Trackers Nyaa
            "https://nyaa.tracker.wf:443/announce",
            "https://tracker.nyaa.si:443/announce",

            # ➤ Trackers publics actifs 2025
            "udp://tracker.internetwarriors.net:1337/announce",
            "udp://open.stealth.si:80/announce",
            "udp://tracker.torrent.eu.org:451/announce",
            "udp://exodus.desync.com:6969/announce",
            "udp://tracker.leechers-paradise.org:6969/announce",
            "udp://tracker.coppersurfer.tk:6969/announce",
            "udp://tracker.moeking.me:6969/announce",
            "udp://tracker.dler.org:6969/announce",
            "udp://tracker.cyberia.is:6969/announce",
            "udp://ipv4.tracker.harry.lu:80/announce",
            "udp://bt.xxx-tracker.com:2710/announce",
            "udp://tracker.bitsearch.to:1337/announce",
            "udp://retracker.lanta-net.ru:2710/announce",
            "udp://tracker.bittor.pw:1337/announce",
            "udp://opentracker.i2p.rocks:6969/announce",

            # ➤ Autres trackers recommandés
            "udp://tracker.tiny-vps.com:6969/announce",
            "udp://tracker.army:6969/announce",
            "udp://tracker.filemail.com:6969/announce",
            "udp://tracker.srv00.com:6969/announce",
            "udp://tracker.port443.xyz:6969/announce",
            "udp://open.acgnxtracker.com:80/announce",
            "udp://tracker.bittorrent.am:6881/announce",
            "udp://tracker1.bt.moack.co.kr:80/announce",
            "udp://torrentclub.tech:6969/announce",
        ]

    def _init_session(self, max_up, max_dl, dht, upnp, natpmp, cache):
        self.session = lt.session()
        self.session.listen_on(*self.ports)
        settings = {
            "upload_rate_limit": max_up * 1024,
            "download_rate_limit": max_dl * 1024,
            "enable_dht": dht,
            "enable_upnp": upnp,
            "enable_natpmp": natpmp,
            "alert_mask": lt.alert.category_t.all_categories,
            "active_downloads": self.max_torrents,
            "active_seeds": self.max_torrents * 2,
            "active_limit": self.max_torrents * 3,
            "cache_size": cache,
            "connections_limit": 500,
            "file_pool_size": 100,
            "allow_multiple_connections_per_ip": True
        }
        self.session.apply_settings(settings)

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
            log.error(f"Torrent error {source}: {e}")
            return None

    async def check_connection(self) -> bool:
        """Vérifie si le client torrent est connecté"""
        try:
            if not self.session.is_listening():
                log.error("Client torrent not listening")
                return False
            log.info("Client torrent is listening")
            return True
        except Exception as e:
            log.error(f"Connection check error: {e}")
            return False

    async def add(self, source: str, path: Optional[Path] = None,
                paused=False, cb=None, user_id: str = None,
                download_type: str = None) -> Optional[str]:
        """Ajoute un téléchargement avec détection automatique du type"""
        # Vérification quota utilisateur
        if user_id and user_id in self.user_tasks and len(self.user_tasks[user_id]) >= self.max_tasks_per_user:
            log.warning(f"User {user_id} reached task limit ({self.max_tasks_per_user})")
            return None

        # Détection automatique du type
        if not download_type:
            if source.endswith('.torrent') or source.startswith('magnet:'):
                download_type = "torrent"
            elif self._is_youtube_url(source):
                download_type = "youtube_dl"
            elif source.startswith(('http://', 'https://')):
                download_type = "http"
            else:
                download_type = "aria2"

        # Vérification des limites globales
        type_counts = {
            DownloadType.TORRENT: len([t for t in self.download_tasks.values() if t.type == DownloadType.TORRENT]),
            DownloadType.HTTP: len([t for t in self.download_tasks.values() if t.type == DownloadType.HTTP]),
            DownloadType.YOUTUBE_DL: len([t for t in self.download_tasks.values() if t.type == DownloadType.YOUTUBE_DL]),
            DownloadType.ARIA2: len([t for t in self.download_tasks.values() if t.type == DownloadType.ARIA2])
        }

        if download_type == "torrent" and type_counts[DownloadType.TORRENT] >= self.max_torrents:
            log.warning("Max torrents reached")
            return None
        elif download_type == "http" and type_counts[DownloadType.HTTP] >= self.max_http_downloads:
            log.warning("Max HTTP downloads reached")
            return None
        elif download_type == "youtube_dl" and type_counts[DownloadType.YOUTUBE_DL] >= self.max_youtube_dl_downloads:
            log.warning("Max YouTube-DL downloads reached")
            return None
        elif download_type == "aria2" and type_counts[DownloadType.ARIA2] >= self.max_aria2_downloads:
            log.warning("Max Aria2 downloads reached")
            return None

        # Ajout de la tâche
        task_id = None
        if download_type == "torrent":
            task_id = await self._add_torrent(source, path, paused, cb, user_id)
        elif download_type == "http":
            task_id = await self._add_http_download(source, path, cb, user_id)
        elif download_type == "youtube_dl":
            task_id = await self._add_youtube_dl_download(source, path, cb, user_id)
        elif download_type == "aria2":
            task_id = await self._add_aria2_download(source, path, cb, user_id)

        # Enregistrement pour l'utilisateur
        if task_id and user_id:
            if user_id not in self.user_tasks:
                self.user_tasks[user_id] = []
            self.user_tasks[user_id].append(task_id)
            self.download_tasks[task_id].user_id = user_id

        return task_id

    def _is_youtube_url(self, url: str) -> bool:
        patterns = [
            r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+",
            r"(https?://)?(www\.)?(dailymotion\.com|dai\.ly)/.+",
            r"(https?://)?(www\.)?vimeo\.com/.+",
            r"(https?://)?(www\.)?twitch\.tv/.+"
        ]
        return any(re.match(pattern, url) for pattern in patterns)

    async def _add_http_download(self, url: str, path: Optional[Path],
                               cb: Optional[Callable], user_id: str) -> Optional[str]:
        """Téléchargement HTTP standard"""
        try:
            if not self.http_session:
                self.http_session = aiohttp.ClientSession()

            parsed_url = urllib.parse.urlparse(url)
            filename = os.path.basename(parsed_url.path)

            # Gestion spéciale pour certains sites
            if "cloudconvert.com" in parsed_url.netloc:
                query_params = urllib.parse.parse_qs(parsed_url.query)
                if 'response-content-disposition' in query_params:
                    disp = query_params['response-content-disposition'][0]
                    if 'filename=' in disp:
                        filename = disp.split('filename=')[1].strip('"')

            dest_path = (path or self.dl_dir) / filename
            task_id = hashlib.sha256(f"{url}{user_id}".encode()).hexdigest()[:16]

            async with self.http_session.head(url) as resp:
                total_size = int(resp.headers.get('content-length', 0))
                if total_size > 0 and not self._disk_space(total_size):
                    raise RuntimeError("Disk space insufficient")

                task = DownloadTask(
                    type=DownloadType.HTTP,
                    id=task_id,
                    state=TorrentState.DOWNLOADING,
                    total_size=total_size / (1024 * 1024),
                    path=dest_path,
                    user_id=user_id
                )
                self.download_tasks[task_id] = task

            asyncio.create_task(self._download_http_file(task_id, url, dest_path, cb))
            return task_id
        except Exception as e:
            log.error(f"HTTP add error: {e}")
            return None

    async def _download_http_file(self, task_id: str, url: str, dest: Path, cb: Optional[Callable]):
        task = self.download_tasks.get(task_id)
        if not task:
            return

        try:
            headers = {}
            if "freeconvert.com" in url:
                headers.update({"Referer": "https://www.freeconvert.com/"})

            async with self.http_session.get(url, headers=headers) as resp:
                task.http_task = resp
                total_size = int(resp.headers.get('content-length', 0))
                task.total_size = total_size / (1024 * 1024)

                downloaded = 0
                last_time = asyncio.get_event_loop().time()

                dest.parent.mkdir(parents=True, exist_ok=True)

                with open(dest, 'wb') as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        if task.state == TorrentState.ERROR:
                            break

                        f.write(chunk)
                        downloaded += len(chunk)

                        now = asyncio.get_event_loop().time()
                        elapsed = now - last_time
                        last_time = now

                        current_speed = (len(chunk) / max(elapsed, 0.001)) / 1024

                        task.downloaded = downloaded / (1024 * 1024)
                        task.progress = (downloaded / total_size) * 100 if total_size > 0 else 0
                        task.speed = current_speed

                        if cb:
                            cb(self._create_http_stats(task))

                if downloaded >= total_size * 0.95:
                    task.state = TorrentState.COMPLETED
                else:
                    task.state = TorrentState.ERROR

        except Exception as e:
            log.error(f"HTTP download error: {e}")
            task.state = TorrentState.ERROR
        finally:
            if task.http_task:
                task.http_task = None
            if cb:
                cb(self._create_http_stats(task))

    async def _add_youtube_dl_download(self, url: str, path: Optional[Path],
                                    cb: Optional[Callable], user_id: str) -> Optional[str]:
        """Téléchargement avec youtube-dl"""
        try:
            task_id = hashlib.sha256(f"{url}{user_id}".encode()).hexdigest()[:16]
            dest_dir = str(path or self.dl_dir)

            task = DownloadTask(
                type=DownloadType.YOUTUBE_DL,
                id=task_id,
                state=TorrentState.DOWNLOADING,
                path=Path(dest_dir),
                user_id=user_id
            )
            self.download_tasks[task_id] = task

            # Démarrer dans un thread séparé
            asyncio.get_event_loop().run_in_executor(
                self.executor,
                self._download_with_ytdlp,
                url,
                dest_dir,
                task_id,
                cb
            )
            return task_id
        except Exception as e:
            log.error(f"YouTube-DL add error: {e}")
            return None

    def _download_with_ytdlp(self, url: str, dest_dir: str, task_id: str, cb: Optional[Callable]):
        """Fonction de téléchargement youtube-dl exécutée dans un thread"""
        task = self.download_tasks.get(task_id)
        if not task:
            return

        def progress_hook(d):
            if d['status'] == 'downloading':
                task.downloaded = d.get('downloaded_bytes', 0) / (1024 * 1024)
                task.total_size = d.get('total_bytes', 0) / (1024 * 1024)
                task.speed = d.get('speed', 0) / 1024  # kB/s
                task.progress = d.get('downloaded_bytes', 0) / d['total_bytes'] * 100 if d['total_bytes'] else 0
                task.ydl_info = d

                if cb:
                    stats = self._create_ytdlp_stats(task)
                    asyncio.run_coroutine_threadsafe(cb(stats), asyncio.get_event_loop())

            elif d['status'] == 'finished':
                task.state = TorrentState.COMPLETED
                task.progress = 100
                task.speed = 0
                if 'filename' in d:
                    task.path = Path(d['filename'])

                if cb:
                    stats = self._create_ytdlp_stats(task)
                    asyncio.run_coroutine_threadsafe(cb(stats), asyncio.get_event_loop())

        ydl_opts = {
            'outtmpl': os.path.join(dest_dir, '%(title)s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'quiet': True,
            'noplaylist': True,
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4'
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                task.metadata = {
                    'title': info.get('title', ''),
                    'duration': info.get('duration', 0),
                    'thumbnail': info.get('thumbnail', ''),
                    'uploader': info.get('uploader', '')
                }
        except Exception as e:
            log.error(f"YouTube-DL error: {e}")
            task.state = TorrentState.ERROR
            if cb:
                stats = self._create_ytdlp_stats(task)
                asyncio.run_coroutine_threadsafe(cb(stats), asyncio.get_event_loop())

    def _create_ytdlp_stats(self, task: DownloadTask) -> TorrentStats:
        eta = 0
        if task.speed > 0 and task.total_size > task.downloaded:
            eta = (task.total_size - task.downloaded) * 1024 / task.speed

        files = []
        if task.path and task.path.exists():
            files.append({
                'path': task.path.name,
                'size': task.total_size,
                'progress': 100 if task.state == TorrentState.COMPLETED else task.progress
            })

        return TorrentStats(
            progress=task.progress,
            dl_rate=task.speed,
            ul_rate=0,
            speed=task.speed / 1024,
            eta=eta,
            peers=0,
            state=task.state,
            wanted=task.total_size,
            done=task.downloaded,
            downloaded=task.downloaded,
            uploaded=0,
            files=files,
            disk=self._get_disk_usage(),
            user_id=task.user_id,
            metadata=task.metadata
        )

    async def _add_aria2_download(self, url: str, path: Optional[Path],
                                cb: Optional[Callable], user_id: str) -> Optional[str]:
        """Téléchargement avec Aria2"""
        try:
            dest_dir = str(path or self.dl_dir)
            filename = os.path.basename(urllib.parse.urlparse(url).path)
            output_path = Path(dest_dir) / filename

            task_id = hashlib.sha256(f"{url}{user_id}".encode()).hexdigest()[:16]

            task = DownloadTask(
                type=DownloadType.ARIA2,
                id=task_id,
                state=TorrentState.DOWNLOADING,
                path=output_path,
                user_id=user_id
            )
            self.download_tasks[task_id] = task

            # Démarrer le processus aria2
            command = [
                self.aria2_path,
                url,
                f"--dir={dest_dir}",
                "--file-allocation=none",
                "--max-connection-per-server=16",
                "--split=16",
                "--quiet",
                "--enable-rpc=false",
                "--summary-interval=1"
            ]

            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            task.aria2_process = process
            asyncio.create_task(self._monitor_aria2_process(task_id, process, cb))

            return task_id
        except Exception as e:
            log.error(f"Aria2 add error: {e}")
            return None

    async def _monitor_aria2_process(self, task_id: str, process: Any, cb: Optional[Callable]):
        """Surveillance du processus aria2"""
        task = self.download_tasks.get(task_id)
        if not task:
            return

        last_size = 0
        last_time = asyncio.get_event_loop().time()

        while True:
            await asyncio.sleep(1)

            if process.returncode is not None:
                if process.returncode == 0:
                    task.state = TorrentState.COMPLETED
                    task.progress = 100
                else:
                    task.state = TorrentState.ERROR

                if cb:
                    cb(self._create_aria2_stats(task))
                break

            # Calculer la vitesse de téléchargement
            if task.path and task.path.exists():
                current_size = task.path.stat().st_size
                current_time = asyncio.get_event_loop().time()

                if current_size > 0:
                    task.downloaded = current_size / (1024 * 1024)

                    if last_size > 0:
                        size_diff = current_size - last_size
                        time_diff = current_time - last_time

                        if time_diff > 0:
                            task.speed = (size_diff / time_diff) / 1024  # kB/s

                last_size = current_size
                last_time = current_time

            if cb:
                cb(self._create_aria2_stats(task))

    def _create_aria2_stats(self, task: DownloadTask) -> TorrentStats:
        progress = 100 if task.state == TorrentState.COMPLETED else 0
        if task.total_size > 0 and task.downloaded > 0:
            progress = (task.downloaded / task.total_size) * 100

        return TorrentStats(
            progress=progress,
            dl_rate=task.speed,
            ul_rate=0,
            speed=task.speed / 1024,
            eta=0,
            peers=0,
            state=task.state,
            wanted=task.total_size,
            done=task.downloaded,
            downloaded=task.downloaded,
            uploaded=0,
            files=[{
                'path': task.path.name if task.path else "unknown",
                'size': task.total_size,
                'progress': progress
            }],
            disk=self._get_disk_usage(),
            user_id=task.user_id
        )

    async def _add_torrent(self, source: str, path: Optional[Path],
                         paused: bool, cb: Optional[Callable], user_id: str) -> Optional[str]:
            """Ajout de torrent avec gestion multi-utilisateurs"""
            try:
                p = Path(path) if path else self.dl_dir
                p = p.absolute()
                p.mkdir(parents=True, exist_ok=True)

                # Vérification espace disque pour les torrents non-magnet
                if not source.startswith('magnet:'):
                    info = await asyncio.get_event_loop().run_in_executor(
                        self.executor, self._get_info, source)
                    if info and not self._disk_space(info.total_size()):
                        raise RuntimeError("Espace disque insuffisant")

                params = {
                    'save_path': str(p),
                    'storage_mode': lt.storage_mode_t.storage_mode_sparse,
                    'trackers': self.trackers
                }

                # Ajout basé sur le type de source
                if source.startswith('magnet:'):
                    h = lt.add_magnet_uri(self.session, source, params)
                else:
                    if not info: return None
                    params['ti'] = info
                    h = self.session.add_torrent(params)

                tid = hashlib.sha256(h.info_hash().to_bytes()).hexdigest()[:16]
                self.handles[tid] = h
                self.download_tasks[tid] = DownloadTask(
                    type=DownloadType.TORRENT,
                    id=tid,
                    handle=h,
                    state=TorrentState.DOWNLOADING,
                    user_id=user_id
                )
                log.info(f"Torrent ajouté {tid}: {h.name()}")

                # Démarrer le suivi de progression
                if cb and not paused:
                    asyncio.create_task(self._progress(tid, cb))
                return tid
            except Exception as e:
                log.error(f"Erreur ajout torrent: {e}", exc_info=True)
                return None

    async def _progress(self, tid: str, cb: Callable, interval=5):
        """Suivi de progression pour les torrents"""
        while tid in self.handles:
            stats = await self.stats(tid)
            if stats:
                try:
                    cb(stats)
                    if stats.state in (TorrentState.COMPLETED, TorrentState.ERROR):
                        break
                except Exception as e:
                    log.error(f"Erreur callback: {e}")
            await asyncio.sleep(interval)

    async def stats(self, task_id: str) -> Optional[TorrentStats]:
        """Récupère les statistiques pour une tâche"""
        task = self.download_tasks.get(task_id)
        if not task:
            return None

        if task.type == DownloadType.TORRENT:
            return await self._get_torrent_stats(task_id)
        elif task.type == DownloadType.HTTP:
            return self._create_http_stats(task)
        elif task.type == DownloadType.YOUTUBE_DL:
            return self._create_ytdlp_stats(task)
        elif task.type == DownloadType.ARIA2:
            return self._create_aria2_stats(task)
        return None

    def _create_http_stats(self, task: DownloadTask) -> TorrentStats:
        """Crée les stats pour un téléchargement HTTP"""
        # Vérifier si le téléchargement est complet
        if task.path and task.path.exists():
            file_size = task.path.stat().st_size
            expected_size = task.total_size * 1024 * 1024
            if file_size >= expected_size * 0.95:
                task.state = TorrentState.COMPLETED

        # Calculer ETA
        eta = 0
        if task.speed > 0 and task.total_size > task.downloaded:
            eta = ((task.total_size - task.downloaded) * 1024) / max(task.speed, 0.001)

        return TorrentStats(
            progress=100 if task.state == TorrentState.COMPLETED else task.progress,
            dl_rate=task.speed,
            ul_rate=0,
            speed=task.speed / 1024,
            eta=eta,
            peers=0,
            state=task.state,
            wanted=task.total_size,
            done=task.total_size if task.state == TorrentState.COMPLETED else task.downloaded,
            downloaded=task.total_size if task.state == TorrentState.COMPLETED else task.downloaded,
            uploaded=0,
            files=[{
                'path': task.path.name if task.path else "unknown",
                'size': task.total_size,
                'progress': 100 if task.state == TorrentState.COMPLETED else task.progress
            }],
            disk=self._get_disk_usage(),
            user_id=task.user_id
        )

    async def _get_torrent_stats(self, tid: str) -> Optional[TorrentStats]:
        """Récupère les statistiques détaillées pour un torrent"""
        if tid not in self.handles:
            return None

        try:
            h = self.handles[tid]
            s = h.status()

            # Mapper les états libtorrent vers nos états
            state_map = {
                lt.torrent_status.states.downloading_metadata: TorrentState.METADATA,
                lt.torrent_status.states.checking_files: TorrentState.CHECKING,
                lt.torrent_status.states.downloading: TorrentState.DOWNLOADING,
                lt.torrent_status.states.finished: TorrentState.COMPLETED,
                lt.torrent_status.states.seeding: TorrentState.SEEDING
            }
            state = state_map.get(s.state, TorrentState.PAUSED if s.paused else TorrentState.ERROR)

            # Récupérer les informations sur les fichiers
            files = []
            if h.has_metadata():
                info = h.get_torrent_info()
                for idx in range(info.num_files()):
                    f = info.file_at(idx)
                    files.append({
                        'path': f.path,
                        'size': f.size / (1024*1024),  # MB
                        'priority': h.file_priority(idx),
                        'progress': h.file_progress(idx) * 100
                    })

            # Calculer l'ETA
            eta = float('inf')
            if s.download_rate > 0:
                remaining = s.total_wanted - s.total_wanted_done
                eta = remaining / s.download_rate

            return TorrentStats(
                progress=s.progress * 100,
                dl_rate=s.download_rate / 1024,  # kB/s
                ul_rate=s.upload_rate / 1024,    # kB/s
                speed=s.download_rate / (1024*1024),  # MB/s
                eta=eta,
                peers=s.num_peers,
                state=state,
                wanted=s.total_wanted / (1024*1024),  # MB
                done=s.total_wanted_done / (1024*1024),  # MB
                downloaded=s.total_payload_download / (1024*1024),  # MB
                uploaded=s.all_time_upload / (1024*1024),  # MB
                files=files,
                disk=self._get_disk_usage(),
                user_id=self.download_tasks[tid].user_id
            )
        except Exception as e:
            log.error(f"Erreur stats torrent: {e}")
            return None

    def _get_disk_usage(self) -> Optional[Dict]:
        """Retourne les statistiques du disque"""
        try:
            disk = psutil.disk_usage(str(self.dl_dir))
            return {
                'total': disk.total / (1024**3),  # GB
                'used': disk.used / (1024**3),    # GB
                'percent': disk.percent
            }
        except Exception:
            return None

    async def remove(self, task_id: str, delete_data: bool = True,
                   wait_resume_data: bool = False) -> bool:
        """Supprime une tâche et ses données associées"""
        task = self.download_tasks.get(task_id)
        if not task:
            return False

        # Suppression spécifique au type
        result = False
        if task.type == DownloadType.TORRENT:
            result = await self._remove_torrent(task_id, delete_data, wait_resume_data)
        elif task.type == DownloadType.HTTP:
            result = await self._cancel_http_download(task_id, delete_data)
        elif task.type == DownloadType.YOUTUBE_DL:
            result = await self._cancel_youtube_dl_download(task_id, delete_data)
        elif task.type == DownloadType.ARIA2:
            result = await self._cancel_aria2_download(task_id, delete_data)

        # Nettoyage de la gestion utilisateur
        if result and task.user_id:
            if task.user_id in self.user_tasks:
                if task_id in self.user_tasks[task.user_id]:
                    self.user_tasks[task.user_id].remove(task_id)
                if not self.user_tasks[task.user_id]:
                    del self.user_tasks[task.user_id]

        return result

    async def _remove_torrent(self, tid: str, delete_data: bool, wait_resume_data: bool) -> bool:
        """Supprime un torrent"""
        if tid not in self.handles:
            return False

        try:
            h = self.handles[tid]

            # Sauvegarder les données de reprise si nécessaire
            if wait_resume_data and not delete_data:
                h.save_resume_data()
                await asyncio.sleep(1)

            # Supprimer le torrent
            self.session.remove_torrent(h, int(delete_data))

            # Nettoyer les références
            del self.handles[tid]
            if tid in self.download_tasks:
                del self.download_tasks[tid]

            log.info(f"Torrent {tid} supprimé (fichiers: {'oui' if delete_data else 'non'})")
            return True

        except Exception as e:
            log.error(f"Échec suppression {tid}: {str(e)}", exc_info=True)
            return False

    async def _cancel_http_download(self, task_id: str, delete_file: bool) -> bool:
        """Annule un téléchargement HTTP"""
        try:
            task = self.download_tasks.get(task_id)
            if not task:
                return False

            # Fermer la connexion si active
            if task.http_task:
                task.http_task.close()
                task.http_task = None

            # Supprimer le fichier si demandé
            if delete_file and task.path and task.path.exists():
                task.path.unlink()
                log.info(f"Fichier HTTP supprimé: {task.path}")

            # Supprimer la tâche
            if task_id in self.download_tasks:
                del self.download_tasks[task_id]

            return True
        except Exception as e:
            log.error(f"Erreur annulation HTTP: {e}")
            return False

    async def _cancel_youtube_dl_download(self, task_id: str, delete_file: bool) -> bool:
        """Annule un téléchargement YouTube-DL"""
        try:
            task = self.download_tasks.get(task_id)
            if not task:
                return False

            # Marquer comme annulé
            task.state = TorrentState.ERROR

            # Supprimer les fichiers incomplets
            if delete_file and task.path and task.path.exists():
                # Supprimer le fichier principal
                if task.path.is_file():
                    task.path.unlink()
                # Supprimer les fichiers partiels
                elif task.path.is_dir():
                    for f in task.path.glob('*.part'):
                        f.unlink()

            # Supprimer la tâche
            if task_id in self.download_tasks:
                del self.download_tasks[task_id]

            return True
        except Exception as e:
            log.error(f"Erreur annulation YouTube-DL: {e}")
            return False

    async def _cancel_aria2_download(self, task_id: str, delete_file: bool) -> bool:
        """Annule un téléchargement Aria2"""
        try:
            task = self.download_tasks.get(task_id)
            if not task or not task.aria2_process:
                return False

            # Terminer le processus
            if task.aria2_process.returncode is None:
                task.aria2_process.terminate()
                try:
                    await asyncio.wait_for(task.aria2_process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    task.aria2_process.kill()

            # Supprimer les fichiers
            if delete_file:
                # Fichier principal
                if task.path and task.path.exists():
                    task.path.unlink()

                # Fichier de contrôle aria2
                aria2_file = task.path.with_suffix(task.path.suffix + ".aria2")
                if aria2_file.exists():
                    aria2_file.unlink()

                # Fichiers partiels
                part_files = task.path.parent.glob(f"{task.path.name}.*.part")
                for part in part_files:
                    part.unlink()

            # Supprimer la tâche
            if task_id in self.download_tasks:
                del self.download_tasks[task_id]

            return True
        except Exception as e:
            log.error(f"Erreur annulation Aria2: {e}")
            return False

    async def get_user_tasks(self, user_id: str) -> List[TorrentStats]:
        """Récupère toutes les tâches d'un utilisateur"""
        tasks = []
        for task_id in self.user_tasks.get(user_id, []):
            if task_id in self.download_tasks:
                stats = await self.stats(task_id)
                if stats:
                    tasks.append(stats)
        return tasks

    async def get_global_stats(self) -> Dict[str, Any]:
        """Récupère les statistiques globales du client"""
        stats = {
            "total_tasks": len(self.download_tasks),
            "total_download_speed": 0,
            "total_upload_speed": 0,
            "disk": self._get_disk_usage(),
            "active_tasks": [],
            "completed_tasks": [],
            "users": {}
        }

        # Parcourir toutes les tâches
        for task_id, task in self.download_tasks.items():
            task_stats = await self.stats(task_id)
            if not task_stats:
                continue

            # Ajouter aux statistiques globales
            stats["total_download_speed"] += task_stats.dl_rate
            stats["total_upload_speed"] += task_stats.ul_rate

            # Catégoriser par état
            if task_stats.state in (TorrentState.COMPLETED, TorrentState.SEEDING):
                stats["completed_tasks"].append(task_stats)
            else:
                stats["active_tasks"].append(task_stats)

            # Statistiques par utilisateur
            if task.user_id:
                if task.user_id not in stats["users"]:
                    stats["users"][task.user_id] = {
                        "download_speed": 0,
                        "upload_speed": 0,
                        "active_tasks": 0,
                        "completed_tasks": 0
                    }
                user_stats = stats["users"][task.user_id]
                user_stats["download_speed"] += task_stats.dl_rate
                user_stats["upload_speed"] += task_stats.ul_rate

                if task_stats.state in (TorrentState.COMPLETED, TorrentState.SEEDING):
                    user_stats["completed_tasks"] += 1
                else:
                    user_stats["active_tasks"] += 1

        return stats

    async def pause_task(self, task_id: str) -> bool:
        """Met en pause une tâche"""
        task = self.download_tasks.get(task_id)
        if not task:
            return False

        if task.type == DownloadType.TORRENT and task_id in self.handles:
            self.handles[task_id].pause()
            task.state = TorrentState.PAUSED
            return True
        elif task.type == DownloadType.HTTP and task.http_task:
            task.http_task.close()
            task.http_task = None
            task.state = TorrentState.PAUSED
            return True
        elif task.type == DownloadType.ARIA2 and task.aria2_process:
            task.aria2_process.terminate()
            task.state = TorrentState.PAUSED
            return True
        elif task.type == DownloadType.YOUTUBE_DL:
            task.state = TorrentState.PAUSED
            return True

        return False

    async def resume_task(self, task_id: str) -> bool:
        """Reprend une tâche en pause"""
        task = self.download_tasks.get(task_id)
        if not task or task.state != TorrentState.PAUSED:
            return False

        if task.type == DownloadType.TORRENT and task_id in self.handles:
            self.handles[task_id].resume()
            task.state = TorrentState.DOWNLOADING
            return True
        elif task.type == DownloadType.HTTP and task.path:
            # Redémarrer le téléchargement HTTP
            task.state = TorrentState.DOWNLOADING
            asyncio.create_task(self._download_http_file(
                task_id,
                task.path.name,  # Note: devrait être l'URL originale
                task.path,
                None  # Callback à récupérer
            ))
            return True
        elif task.type == DownloadType.ARIA2 and task.path:
            # Redémarrer Aria2
            task.state = TorrentState.DOWNLOADING
            asyncio.create_task(self._add_aria2_download(
                task.path.name,  # Note: devrait être l'URL originale
                task.path.parent,
                None,  # Callback
                task.user_id
            ))
            return True
        elif task.type == DownloadType.YOUTUBE_DL:
            task.state = TorrentState.DOWNLOADING
            # Redémarrer dans un thread
            asyncio.get_event_loop().run_in_executor(
                self.executor,
                self._download_with_ytdlp,
                task.path.name,  # Note: devrait être l'URL originale
                str(task.path.parent),
                task_id,
                None  # Callback
            )
            return True

        return False

    async def get_task_details(self, task_id: str) -> Optional[Dict]:
        """Récupère les détails complets d'une tâche"""
        task = self.download_tasks.get(task_id)
        if not task:
            return None

        stats = await self.stats(task_id)
        if not stats:
            return None

        details = {
            "id": task_id,
            "type": task.type.name,
            "state": task.state.name,
            "progress": stats.progress,
            "download_speed": stats.dl_rate,
            "upload_speed": stats.ul_rate,
            "downloaded": stats.downloaded,
            "uploaded": stats.uploaded,
            "size": stats.wanted,
            "eta": stats.eta,
            "files": stats.files,
            "path": str(task.path) if task.path else "",
            "user_id": task.user_id,
            "metadata": task.metadata,
            "created_at": task.metadata.get("created_at", ""),
            "started_at": task.metadata.get("started_at", ""),
            "completed_at": task.metadata.get("completed_at", ""),
            "error": task.metadata.get("error", "")
        }

        # Ajouter des détails spécifiques au type
        if task.type == DownloadType.TORRENT:
            if task_id in self.handles:
                h = self.handles[task_id]
                s = h.status()
                details.update({
                    "hash": str(h.info_hash()),
                    "name": h.name(),
                    "seeds": s.num_seeds,
                    "peers": s.num_peers,
                    "trackers": [t.url for t in h.trackers()],
                    "save_path": h.save_path()
                })

        elif task.type == DownloadType.YOUTUBE_DL:
            details.update({
                "format": task.ydl_info.get("format", ""),
                "duration": task.ydl_info.get("duration", 0),
                "thumbnail": task.ydl_info.get("thumbnail", ""),
                "resolution": task.ydl_info.get("resolution", "")
            })

        elif task.type == DownloadType.ARIA2:
            if task.aria2_process:
                details["pid"] = task.aria2_process.pid

        return details

    async def clean_completed_tasks(self, max_age_hours: int = 24):
        """Nettoie les tâches complétées plus anciennes que max_age_hours"""
        now = time.time()
        tasks_to_remove = []

        for task_id, task in self.download_tasks.items():
            if task.state not in (TorrentState.COMPLETED, TorrentState.ERROR):
                continue

            completed_time = task.metadata.get("completed_at", 0)
            if now - completed_time > max_age_hours * 3600:
                tasks_to_remove.append(task_id)

        for task_id in tasks_to_remove:
            await self.remove(task_id, delete_data=False)

    async def move_task_files(self, task_id: str, new_path: Union[str, Path]) -> bool:
        """Déplace les fichiers d'une tâche vers un nouvel emplacement"""
        task = self.download_tasks.get(task_id)
        if not task or not task.path:
            return False

        new_path = Path(new_path)
        if not new_path.exists():
            new_path.mkdir(parents=True, exist_ok=True)

        try:
            # Pour les torrents avec fichiers multiples
            if task.type == DownloadType.TORRENT and task_id in self.handles:
                h = self.handles[task_id]
                if h.has_metadata():
                    info = h.get_torrent_info()
                    for idx in range(info.num_files()):
                        f = info.file_at(idx)
                        src = Path(h.save_path()) / f.path
                        dest = new_path / f.path
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(src), str(dest))

                    # Mettre à jour le chemin de sauvegarde
                    h.move_storage(str(new_path))
                    task.path = new_path
                    return True

            # Pour les tâches avec un seul fichier
            if task.path.exists():
                dest = new_path / task.path.name
                shutil.move(str(task.path), str(dest))
                task.path = dest
                return True

        except Exception as e:
            log.error(f"Erreur déplacement fichiers {task_id}: {e}")

        return False

    async def prioritize_files(self, task_id: str, file_indices: List[int], priority: int = 7):
        """Priorise certains fichiers dans un torrent"""
        if task_id not in self.handles:
            return False

        h = self.handles[task_id]
        if not h.has_metadata():
            return False

        # Définir la priorité pour chaque fichier
        for idx in file_indices:
            try:
                h.file_priority(idx, priority)
            except Exception:
                pass

        # Forcer le torrent à re-vérifier les priorités
        h.force_recheck()
        return True

    async def export_torrent_file(self, task_id: str, output_path: Union[str, Path]) -> bool:
        """Exporte le fichier .torrent pour une tâche torrent"""
        if task_id not in self.handles:
            return False

        h = self.handles[task_id]
        if not h.has_metadata():
            return False

        try:
            info = h.get_torrent_info()
            torrent_file = lt.create_torrent(info)
            with open(output_path, "wb") as f:
                f.write(lt.bencode(torrent_file.generate()))
            return True
        except Exception as e:
            log.error(f"Erreur export torrent {task_id}: {e}")
            return False

    async def add_torrent_from_resume_data(self, resume_data: bytes, save_path: str, user_id: str = None) -> Optional[str]:
        """Ajoute un torrent à partir de données de reprise"""
        try:
            atp = lt.add_torrent_params()
            atp.resume_data = resume_data
            atp.save_path = save_path

            h = self.session.add_torrent(atp)
            tid = hashlib.sha256(h.info_hash().to_bytes()).hexdigest()[:16]

            self.handles[tid] = h
            self.download_tasks[tid] = DownloadTask(
                type=DownloadType.TORRENT,
                id=tid,
                handle=h,
                state=TorrentState.PAUSED,
                user_id=user_id
            )

            # Enregistrement pour l'utilisateur
            if user_id:
                if user_id not in self.user_tasks:
                    self.user_tasks[user_id] = []
                self.user_tasks[user_id].append(tid)

            log.info(f"Torrent repris {tid}: {h.name()}")
            return tid
        except Exception as e:
            log.error(f"Erreur reprise torrent: {e}")
            return None

    async def generate_resume_data(self, task_id: str) -> Optional[bytes]:
        """Génère les données de reprise pour un torrent"""
        if task_id not in self.handles:
            return None

        h = self.handles[task_id]
        h.save_resume_data()

        # Attendre que les données soient générées
        for _ in range(10):
            if h.need_save_resume_data():
                await asyncio.sleep(1)
            else:
                break

        return h.write_resume_data()

    async def optimize_torrent_settings(self):
        """Optimise dynamiquement les paramètres des torrents"""
        # Analyser les statistiques globales
        global_stats = await self.get_global_stats()
        total_dl = global_stats["total_download_speed"]
        total_ul = global_stats["total_upload_speed"]

        # Adapter les paramètres en fonction de la charge
        new_settings = {}

        # Si le débit descendant est faible, augmenter les connexions
        if total_dl < 1000:  # kB/s
            new_settings["connections_limit"] = 1000
            new_settings["active_seeds"] = len(self.handles) * 2
        else:
            new_settings["connections_limit"] = 500
            new_settings["active_seeds"] = len(self.handles)

        # Ajuster le cache en fonction de l'utilisation mémoire
        mem = psutil.virtual_memory()
        if mem.available > 2 * 1024 * 1024 * 1024:  # > 2GB
            new_settings["cache_size"] = 2048  # MB
        else:
            new_settings["cache_size"] = 512

        # Appliquer les nouveaux paramètres
        if new_settings:
            self.session.apply_settings(new_settings)
            log.info(f"Paramètres optimisés: {new_settings}")

    async def check_disk_space(self) -> Dict:
        """Vérifie l'espace disque pour tous les chemins utilisés"""
        paths = {str(self.dl_dir)}

        # Ajouter les chemins spécifiques des tâches
        for task in self.download_tasks.values():
            if task.path:
                paths.add(str(task.path.parent))

        # Analyser chaque chemin
        results = {}
        for path in paths:
            try:
                usage = psutil.disk_usage(path)
                results[path] = {
                    "total": usage.total,
                    "used": usage.used,
                    "free": usage.free,
                    "percent": usage.percent
                }
            except Exception as e:
                results[path] = {"error": str(e)}

        return results

    async def get_bandwidth_usage(self, period_hours: int = 24) -> Dict:
        """Rapport d'utilisation de la bande passante"""
        now = time.time()
        cutoff = now - (period_hours * 3600)

        report = {
            "total_download": 0,
            "total_upload": 0,
            "by_user": {},
            "by_type": {
                "TORRENT": {"download": 0, "upload": 0},
                "HTTP": {"download": 0, "upload": 0},
                "YOUTUBE_DL": {"download": 0, "upload": 0},
                "ARIA2": {"download": 0, "upload": 0}
            }
        }

        for task in self.download_tasks.values():
            # Vérifier si la tâche était active pendant la période
            created = task.metadata.get("created_at", now)
            completed = task.metadata.get("completed_at", now)

            if completed < cutoff or created > now:
                continue

            # Calculer le pourcentage de la période couvert par la tâche
            active_start = max(created, cutoff)
            active_end = min(completed, now)
            active_duration = active_end - active_start
            period_ratio = active_duration / (now - cutoff)

            # Estimer l'utilisation (approximatif)
            dl_estimate = task.downloaded * period_ratio
            ul_estimate = getattr(task, "uploaded", 0) * period_ratio

            # Mettre à jour le rapport
            report["total_download"] += dl_estimate
            report["total_upload"] += ul_estimate

            # Par type
            type_key = task.type.name
            report["by_type"][type_key]["download"] += dl_estimate
            report["by_type"][type_key]["upload"] += ul_estimate

            # Par utilisateur
            if task.user_id:
                if task.user_id not in report["by_user"]:
                    report["by_user"][task.user_id] = {
                        "download": 0,
                        "upload": 0
                    }
                report["by_user"][task.user_id]["download"] += dl_estimate
                report["by_user"][task.user_id]["upload"] += ul_estimate

        return report

    async def get_system_stats(self) -> Dict:
        """Récupère les statistiques système"""
        cpu_percent = psutil.cpu_percent(interval=1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(str(self.dl_dir))
        net = psutil.net_io_counters()
        processes = len(psutil.pids())

        return {
            "cpu": {
                "percent": cpu_percent,
                "cores": psutil.cpu_count(logical=False),
                "threads": psutil.cpu_count(logical=True)
            },
            "memory": {
                "total": mem.total,
                "available": mem.available,
                "used": mem.used,
                "percent": mem.percent
            },
            "disk": {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent
            },
            "network": {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv
            },
            "processes": processes,
            "uptime": time.time() - psutil.boot_time()
        }

    async def get_active_connections(self) -> List[Dict]:
        """Récupère les connexions actives pour les torrents"""
        connections = []
        for tid, h in self.handles.items():
            s = h.status()
            for peer in h.get_peer_info():
                connections.append({
                    "task_id": tid,
                    "ip": peer.ip[0],
                    "port": peer.ip[1],
                    "client": peer.client,
                    "flags": peer.flags,
                    "download_speed": peer.down_speed,
                    "upload_speed": peer.up_speed,
                    "progress": peer.progress * 100,
                    "is_seed": bool(peer.flags & 0x4)  # seed flag
                })
        return connections

    async def get_performance_metrics(self) -> Dict:
        """Retourne des métriques de performance pour le monitoring"""
        return {
            "tasks": {
                "total": len(self.download_tasks),
                "active": sum(1 for t in self.download_tasks.values()
                             if t.state in (TorrentState.DOWNLOADING, TorrentState.SEEDING)),
                "completed": sum(1 for t in self.download_tasks.values()
                                if t.state == TorrentState.COMPLETED),
                "errors": sum(1 for t in self.download_tasks.values()
                             if t.state == TorrentState.ERROR)
            },
            "throughput": {
                "download": sum(t.speed for t in self.download_tasks.values()
                               if t.state == TorrentState.DOWNLOADING),
                "upload": sum(getattr(t, "upload_speed", 0)
                             for t in self.download_tasks.values()
                             if t.state == TorrentState.SEEDING)
            },
            "resources": {
                "threads": self.executor._max_workers,
                "memory": psutil.Process().memory_info().rss,
                "open_files": len(psutil.Process().open_files())
            }
        }

    async def close(self):
        """Nettoyage des ressources avec sauvegarde d'état"""
        log.info("Début de la procédure d'arrêt...")

        # Sauvegarder l'état des torrents
        for tid in list(self.handles):
            try:
                h = self.handles[tid]
                if h.is_valid() and h.status().has_metadata:
                    resume_data = h.write_resume_data()
                    self.download_tasks[tid].metadata["resume_data"] = resume_data
            except Exception as e:
                log.error(f"Erreur sauvegarde état torrent {tid}: {e}")

        # Arrêter tous les processus actifs
        for task_id in list(self.download_tasks.keys()):
            try:
                task = self.download_tasks[task_id]

                # Marquer l'heure de fin
                task.metadata["closed_at"] = time.time()

                if task.type == DownloadType.ARIA2 and task.aria2_process:
                    if task.aria2_process.returncode is None:
                        task.aria2_process.terminate()
                        await asyncio.sleep(1)
                        if task.aria2_process.returncode is None:
                            task.aria2_process.kill()

                elif task.type == DownloadType.HTTP and task.http_task:
                    task.http_task.close()
            except Exception as e:
                log.error(f"Erreur fermeture tâche {task_id}: {e}")

        # Fermer les sessions
        self.session.pause()
        if self.http_session:
            await self.http_session.close()

        # Arrêter l'executor
        self.executor.shutdown(wait=True)

        # Sauvegarder l'état global
        await self.save_state()

        log.info("Client arrêté proprement")

    async def save_state(self):
        """Sauvegarde l'état du client dans un fichier"""
        state = {
            "version": "1.0",
            "timestamp": time.time(),
            "tasks": [],
            "user_tasks": self.user_tasks,
            "settings": {
                "dl_dir": str(self.dl_dir),
                "ports": self.ports,
                "max_torrents": self.max_torrents,
                "max_http_downloads": self.max_http_downloads,
                "max_youtube_dl_downloads": self.max_youtube_dl_downloads,
                "max_aria2_downloads": self.max_aria2_downloads
            }
        }

        for task_id, task in self.download_tasks.items():
            task_state = {
                "id": task_id,
                "type": task.type.name,
                "state": task.state.name,
                "progress": task.progress,
                "downloaded": task.downloaded,
                "total_size": task.total_size,
                "path": str(task.path) if task.path else None,
                "user_id": task.user_id,
                "metadata": task.metadata
            }

            # Sauvegarder les données de reprise pour les torrents
            if task.type == DownloadType.TORRENT and task_id in self.handles:
                if self.handles[task_id].is_valid():
                    task_state["resume_data"] = self.handles[task_id].write_resume_data()

            state["tasks"].append(task_state)

        with open("client_state.json", "w") as f:
            json.dump(state, f, indent=2)

        log.info("État du client sauvegardé")

    async def load_state(self, state_file: str = "client_state.json") -> bool:
        """Charge l'état du client depuis un fichier"""
        if not os.path.exists(state_file):
            return False

        try:
            with open(state_file, "r") as f:
                state = json.load(f)

            # Restaurer les tâches
            for task_state in state.get("tasks", []):
                try:
                    task_type = DownloadType[task_state["type"]]
                    user_id = task_state.get("user_id")

                    # Recréer la tâche
                    if task_type == DownloadType.TORRENT:
                        resume_data = task_state.get("resume_data")
                        if resume_data:
                            await self.add_torrent_from_resume_data(
                                resume_data,
                                task_state.get("path", str(self.dl_dir)),
                                user_id
                            )

                    # Pour les autres types, simplement enregistrer l'état
                    else:
                        task = DownloadTask(
                            type=task_type,
                            id=task_state["id"],
                            progress=task_state["progress"],
                            state=TorrentState[task_state["state"]],
                            downloaded=task_state["downloaded"],
                            total_size=task_state["total_size"],
                            path=Path(task_state["path"]) if task_state["path"] else None,
                            user_id=user_id,
                            metadata=task_state.get("metadata", {})
                        )
                        self.download_tasks[task.id] = task

                        # Enregistrement utilisateur
                        if user_id:
                            if user_id not in self.user_tasks:
                                self.user_tasks[user_id] = []
                            self.user_tasks[user_id].append(task.id)

                except Exception as e:
                    log.error(f"Erreur reprise tâche: {e}")

            log.info(f"État chargé: {len(state['tasks'])} tâches restaurées")
            return True

        except Exception as e:
            log.error(f"Erreur chargement état: {e}")
            return False

    async def stream_file(self, task_id: str, file_index: int,
                        range_header: str) -> Tuple[Optional[bytes], int, int, int]:
        """Stream un fichier partiellement téléchargé (torrent seulement)"""
        if task_id not in self.handles:
            return None, 0, 0, 404

        try:
            h = self.handles[task_id]
            if not h.has_metadata():
                return None, 0, 0, 404

            info = h.get_torrent_info()
            if file_index >= info.num_files():
                return None, 0, 0, 404

            file_entry = info.file_at(file_index)
            file_size = file_entry.size
            file_path = Path(h.save_path()) / file_entry.path

            # Vérifier si le fichier existe et est suffisamment téléchargé
            if not file_path.exists():
                return None, 0, 0, 404

            # Calculer la plage demandée
            start, end = 0, file_size - 1
            if range_header:
                range_match = re.search(r"bytes=(\d+)-(\d+)?", range_header)
                if range_match:
                    start = int(range_match.group(1))
                    end = int(range_match.group(2)) if range_match.group(2) else file_size - 1

            # Lire le segment demandé
            with open(file_path, "rb") as f:
                f.seek(start)
                data = f.read(end - start + 1)

            return data, start, end, file_size

        except Exception as e:
            log.error(f"Erreur streaming {task_id}: {e}")
            return None, 0, 0, 500

    async def convert_file_format(self, task_id: str, output_format: str,
                                quality: str = "medium") -> Optional[str]:
        """Convertit un fichier téléchargé dans un autre format"""
        task = self.download_tasks.get(task_id)
        if not task or not task.path or not task.path.exists():
            return None

        try:
            # Identifier le type de fichier
            input_file = task.path
            output_file = input_file.with_suffix(f".{output_format}")

            # Créer une nouvelle tâche pour suivre la conversion
            convert_task_id = hashlib.sha256(f"convert_{task_id}_{output_format}".encode()).hexdigest()[:16]
            convert_task = DownloadTask(
                type=DownloadType.HTTP,  # Utilisation fictive pour le suivi
                id=convert_task_id,
                state=TorrentState.DOWNLOADING,
                user_id=task.user_id,
                metadata={
                    "source_task": task_id,
                    "operation": "conversion",
                    "format": output_format,
                    "quality": quality
                }
            )
            self.download_tasks[convert_task_id] = convert_task

            # Exécuter la conversion dans un thread séparé
            def _run_conversion():
                try:
                    # FFmpeg pour les conversions vidéo/audio
                    if output_format in ['mp4', 'avi', 'mov', 'mkv', 'mp3', 'flac']:
                        cmd = [
                            "ffmpeg", "-i", str(input_file),
                            "-c:v", "libx264" if output_format in ['mp4', 'mkv'] else "copy",
                            "-crf", "23" if quality == "medium" else "28" if quality == "low" else "18",
                            "-c:a", "aac" if output_format in ['mp4', 'mkv'] else "copy",
                            "-y", str(output_file)
                        ]
                        subprocess.run(cmd, check=True, capture_output=True)

                    # ImageMagick pour les images
                    elif output_format in ['jpg', 'png', 'webp']:
                        from PIL import Image
                        img = Image.open(input_file)
                        img.save(output_file)

                    # Autres conversions
                    else:
                        log.error(f"Format non supporté: {output_format}")
                        return

                    # Mise à jour de la tâche
                    convert_task.state = TorrentState.COMPLETED
                    convert_task.path = output_file
                    convert_task.total_size = output_file.stat().st_size / (1024 * 1024)

                except Exception as e:
                    log.error(f"Erreur conversion: {e}")
                    convert_task.state = TorrentState.ERROR

            asyncio.get_event_loop().run_in_executor(
                self.executor,
                _run_conversion
            )

            return convert_task_id

        except Exception as e:
            log.error(f"Erreur lancement conversion: {e}")
            return None

    async def generate_thumbnail(self, task_id: str, time_offset: str = "00:00:05") -> Optional[Path]:
        """Génère une miniature pour un fichier vidéo"""
        task = self.download_tasks.get(task_id)
        if not task or not task.path or not task.path.exists():
            return None

        try:
            output_path = task.path.with_suffix(".jpg")
            cmd = [
                "ffmpeg", "-i", str(task.path),
                "-ss", time_offset,
                "-vframes", "1",
                "-q:v", "2",
                "-y", str(output_path)
            ]

            await asyncio.create_subprocess_exec(*cmd)
            return output_path

        except Exception as e:
            log.error(f"Erreur génération thumbnail: {e}")
            return None

    async def extract_metadata(self, task_id: str) -> Dict:
        """Extrait les métadonnées d'un fichier"""
        task = self.download_tasks.get(task_id)
        if not task or not task.path or not task.path.exists():
            return {}

        try:
            metadata = {}

            # Vidéo avec FFprobe
            if task.path.suffix.lower() in ['.mp4', '.avi', '.mkv', '.mov']:
                cmd = [
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-show_format", "-show_streams",
                    str(task.path)
                ]
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                if stdout:
                    metadata = json.loads(stdout.decode())

            # Images avec PIL
            elif task.path.suffix.lower() in ['.jpg', '.jpeg', '.png', '.webp']:
                from PIL import Image
                from PIL.ExifTags import TAGS

                img = Image.open(task.path)
                metadata = {
                    "format": img.format,
                    "size": img.size,
                    "mode": img.mode
                }

                # Exif pour les photos
                if hasattr(img, '_getexif') and img._getexif():
                    exif = {
                        TAGS.get(tag, tag): value
                        for tag, value in img._getexif().items()
                    }
                    metadata["exif"] = exif

            # Documents avec python-magic
            elif task.path.suffix.lower() in ['.pdf', '.docx', '.pptx', '.xlsx']:
                import magic
                mime = magic.Magic()
                metadata = {
                    "mime_type": mime.from_file(str(task.path)),
                    "size": task.path.stat().st_size
                }

            # Mettre à jour la tâche
            if metadata:
                task.metadata["file_metadata"] = metadata

            return metadata

        except Exception as e:
            log.error(f"Erreur extraction métadonnées: {e}")
            return {}

    async def search_subtitles(self, task_id: str, language: str = "fr") -> List[Dict]:
        """Recherche des sous-titres pour un fichier vidéo"""
        task = self.download_tasks.get(task_id)
        if not task or not task.path or not task.path.exists():
            return []

        try:
            # Utiliser OpenSubtitles API
            url = "https://api.opensubtitles.com/api/v1/subtitles"
            params = {
                "languages": language,
                "query": task.path.stem
            }
            headers = {
                "Api-Key": "YOUR_OPENSUBTITLES_API_KEY",
                "Content-Type": "application/json"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status != 200:
                        return []

                    data = await resp.json()
                    return [
                        {
                            "id": sub["id"],
                            "language": sub["attributes"]["language"],
                            "release": sub["attributes"]["release"],
                            "rating": sub["attributes"]["ratings"],
                            "download_count": sub["attributes"]["download_count"],
                            "url": sub["attributes"]["url"]
                        }
                        for sub in data["data"]
                    ]

        except Exception as e:
            log.error(f"Erreur recherche sous-titres: {e}")
            return []

    async def download_subtitle(self, task_id: str, subtitle_id: str) -> Optional[Path]:
        """Télécharge un sous-titre depuis OpenSubtitles"""
        task = self.download_tasks.get(task_id)
        if not task or not task.path:
            return None

        try:
            # Récupérer l'URL de téléchargement
            url = f"https://api.opensubtitles.com/api/v1/download"
            headers = {
                "Api-Key": "YOUR_OPENSUBTITLES_API_KEY",
                "Content-Type": "application/json"
            }
            payload = {
                "file_id": subtitle_id
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        return None

                    data = await resp.json()
                    download_url = data["link"]

                    # Télécharger le fichier
                    subtitle_path = task.path.with_suffix(".srt")
                    async with session.get(download_url) as sub_resp:
                        with open(subtitle_path, "wb") as f:
                            while True:
                                chunk = await sub_resp.content.read(1024)
                                if not chunk:
                                    break
                                f.write(chunk)

                    return subtitle_path

        except Exception as e:
            log.error(f"Erreur téléchargement sous-titre: {e}")
            return None

    async def create_download_archive(self, task_ids: List[str],
                                    output_path: Union[str, Path],
                                    archive_format: str = "zip") -> bool:
        """Crée une archive de plusieurs tâches"""
        try:
            output_path = Path(output_path)
            files_to_archive = []

            # Collecter les fichiers
            for task_id in task_ids:
                task = self.download_tasks.get(task_id)
                if not task or not task.path:
                    continue

                if task.type == DownloadType.TORRENT and task_id in self.handles:
                    # Pour les torrents, ajouter tous les fichiers
                    h = self.handles[task_id]
                    if h.has_metadata():
                        info = h.get_torrent_info()
                        for idx in range(info.num_files()):
                            f = info.file_at(idx)
                            file_path = Path(h.save_path()) / f.path
                            if file_path.exists():
                                files_to_archive.append(file_path)
                elif task.path.exists():
                    files_to_archive.append(task.path)

            # Créer l'archive
            if archive_format == "zip":
                with zipfile.ZipFile(output_path, 'w') as zipf:
                    for file in files_to_archive:
                        zipf.write(file, arcname=file.name)
            elif archive_format == "tar":
                with tarfile.open(output_path, 'w') as tar:
                    for file in files_to_archive:
                        tar.add(file, arcname=file.name)
            elif archive_format == "tar.gz":
                with tarfile.open(output_path, 'w:gz') as tar:
                    for file in files_to_archive:
                        tar.add(file, arcname=file.name)
            else:
                log.error(f"Format d'archive non supporté: {archive_format}")
                return False

            return True

        except Exception as e:
            log.error(f"Erreur création archive: {e}")
            return False

    async def share_to_cloud(self, task_id: str,
                           service: str = "google_drive") -> Optional[str]:
        """Partage un fichier sur un service cloud"""
        task = self.download_tasks.get(task_id)
        if not task or not task.path or not task.path.exists():
            return None

        try:
            # Google Drive
            if service == "google_drive":
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
                from googleapiclient.http import MediaFileUpload

                # Configuration (à remplacer par vos credentials)
                SCOPES = ['https://www.googleapis.com/auth/drive']
                SERVICE_ACCOUNT_FILE = 'service-account.json'

                credentials = service_account.Credentials.from_service_account_file(
                    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
                service = build('drive', 'v3', credentials=credentials)

                file_metadata = {
                    'name': task.path.name,
                    'parents': ['YOUR_FOLDER_ID']
                }
                media = MediaFileUpload(task.path,
                                       mimetype='application/octet-stream',
                                       resumable=True)

                file = service.files().create(body=file_metadata,
                                            media_body=media,
                                            fields='id, webViewLink').execute()

                return file.get('webViewLink')

            # Dropbox
            elif service == "dropbox":
                import dropbox
                from dropbox.files import WriteMode

                dbx = dropbox.Dropbox('YOUR_DROPBOX_TOKEN')

                with open(task.path, 'rb') as f:
                    file_path = f"/{task.path.name}"
                    dbx.files_upload(f.read(), file_path, mode=WriteMode('overwrite'))

                shared_link = dbx.sharing_create_shared_link(file_path).url
                return shared_link.replace("?dl=0", "?dl=1")


            return None

        except Exception as e:
            log.error(f"Erreur partage cloud ({service}): {e}")
            return None

    async def transcode_file(self, task_id: str,
                           output_format: str,
                           preset: str = "h264_720p") -> Optional[str]:
        """Transcode un fichier vidéo avec des paramètres professionnels"""
        task = self.download_tasks.get(task_id)
        if not task or not task.path or not task.path.exists():
            return None

        try:
            # Définir les paramètres de transcodage
            presets = {
                "h264_720p": {
                    "vcodec": "libx264",
                    "crf": 23,
                    "preset": "medium",
                    "s": "1280x720",
                    "acodec": "aac",
                    "b:a": "128k"
                },
                "h264_1080p": {
                    "vcodec": "libx264",
                    "crf": 23,
                    "preset": "medium",
                    "s": "1920x1080",
                    "acodec": "aac",
                    "b:a": "192k"
                },
                "hevc_4k": {
                    "vcodec": "libx265",
                    "crf": 28,
                    "preset": "slow",
                    "s": "3840x2160",
                    "acodec": "aac",
                    "b:a": "256k"
                },
                "web_optimized": {
                    "vcodec": "libvpx-vp9",
                    "crf": 30,
                    "b:v": "1M",
                    "acodec": "libopus",
                    "b:a": "128k",
                    "movflags": "faststart"
                }
            }

            if preset not in presets:
                return None

            config = presets[preset]
            output_file = task.path.with_suffix(f".{output_format}")

            # Construire la commande FFmpeg
            cmd = ["ffmpeg", "-i", str(task.path)]

            # Options vidéo
            cmd.extend(["-c:v", config["vcodec"]])
            if "crf" in config:
                cmd.extend(["-crf", str(config["crf"])])
            if "preset" in config:
                cmd.extend(["-preset", config["preset"]])
            if "s" in config:
                cmd.extend(["-s", config["s"]])
            if "b:v" in config:
                cmd.extend(["-b:v", config["b:v"]])

            # Options audio
            cmd.extend(["-c:a", config["acodec"]])
            if "b:a" in config:
                cmd.extend(["-b:a", config["b:a"]])

            # Options supplémentaires
            if "movflags" in config:
                cmd.extend(["-movflags", config["movflags"]])

            cmd.append(str(output_file))

            # Créer une tâche de suivi
            transcode_task_id = hashlib.sha256(f"transcode_{task_id}_{preset}".encode()).hexdigest()[:16]
            transcode_task = DownloadTask(
                type=DownloadType.HTTP,  # Utilisation fictive
                id=transcode_task_id,
                state=TorrentState.DOWNLOADING,
                user_id=task.user_id,
                metadata={
                    "source_task": task_id,
                    "operation": "transcoding",
                    "preset": preset,
                    "format": output_format
                }
            )
            self.download_tasks[transcode_task_id] = transcode_task

            # Exécuter la commande dans un thread
            def _run_transcode():
                try:
                    # Exécuter la commande
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        universal_newlines=True
                    )

                    # Analyser la progression
                    duration_pattern = re.compile(r"Duration: (\d+):(\d+):(\d+).\d+")
                    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+).\d+")
                    duration = None

                    for line in process.stdout:
                        # Détecter la durée totale
                        if not duration:
                            match = duration_pattern.search(line)
                            if match:
                                hours, minutes, seconds = map(int, match.groups())
                                duration = hours * 3600 + minutes * 60 + seconds
                                transcode_task.total_size = duration

                        # Détecter la progression
                        match = time_pattern.search(line)
                        if match and duration:
                            hours, minutes, seconds = map(int, match.groups())
                            current_time = hours * 3600 + minutes * 60 + seconds
                            progress = (current_time / duration) * 100
                            transcode_task.progress = progress

                    process.wait()

                    if process.returncode == 0:
                        transcode_task.state = TorrentState.COMPLETED
                        transcode_task.path = output_file
                        transcode_task.total_size = output_file.stat().st_size / (1024 * 1024)
                    else:
                        transcode_task.state = TorrentState.ERROR

                except Exception as e:
                    log.error(f"Erreur transcodage: {e}")
                    transcode_task.state = TorrentState.ERROR

            asyncio.get_event_loop().run_in_executor(
                self.executor,
                _run_transcode
            )

            return transcode_task_id

        except Exception as e:
            log.error(f"Erreur lancement transcodage: {e}")
            return None

    async def analyze_video_quality(self, task_id: str) -> Dict:
        """Analyse la qualité d'une vidéo avec FFprobe"""
        task = self.download_tasks.get(task_id)
        if not task or not task.path or not task.path.exists():
            return {}

        try:
            # Analyse vidéo avec FFprobe
            cmd = [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,bit_rate,avg_frame_rate,codec_name",
                "-show_entries", "format=bit_rate,duration",
                "-of", "json",
                str(task.path)
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                return {}

            data = json.loads(stdout.decode())

            # Analyse supplémentaire avec FFmpeg
            cmd = [
                "ffmpeg", "-i", str(task.path),
                "-vf", "signalstats,metadata=print:key=lavfi.signalstats.*",
                "-f", "null", "-"
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            # Extraire les métriques qualité
            metrics = {}
            for line in stderr.decode().split('\n'):
                if "lavfi." in line:
                    parts = line.split('=')[1].strip().split(' ')
                    for part in parts:
                        if "lavfi." in part:
                            key, value = part.split('=')
                            metrics[key] = value

            # Combiner les résultats
            result = {
                "technical": data,
                "quality_metrics": metrics
            }

            return result

        except Exception as e:
            log.error(f"Erreur analyse qualité vidéo: {e}")
            return {}

    async def cleanup_stalled_downloads(self):
        """Nettoie les téléchargements bloqués ou en erreur"""
        log.info("Début du nettoyage des téléchargements bloqués...")

        for task_id, task in list(self.download_tasks.items()):
            if task.state in (TorrentState.ERROR, TorrentState.CANCELLED):
                log.warning(f"Suppression tâche bloquée: {task_id} ({task.state.name})")
                del self.download_tasks[task_id]

                # Supprimer le fichier associé
                if task.path and task.path.exists():
                    try:
                        task.path.unlink()
                        log.info(f"Fichier supprimé: {task.path}")
                    except Exception as e:
                        log.error(f"Erreur suppression fichier {task.path}: {e}")

        log.info("Nettoyage terminé")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        if exc_type:
            log.error(f"Erreur dans le contexte: {exc_type} {exc_val}")
