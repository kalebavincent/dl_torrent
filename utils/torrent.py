import os
import aiohttp
import asyncio
import hashlib
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
import psutil
import requests
import urllib

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
        cache: int = 1024,    # MB
        max_http_downloads: int = 3
    ):
        self.dl_dir = Path(dl_dir).absolute()
        self.dl_dir.mkdir(parents=True, exist_ok=True)
        self.ports = ports
        self.max_torrents = max_torrents
        self.max_http_downloads = max_http_downloads
        self.executor = ThreadPoolExecutor(4)
        self.handles: Dict[str, lt.torrent_handle] = {}
        self.download_tasks: Dict[str, DownloadTask] = {}
        self.trackers = trackers or self._default_trackers()
        self.http_session: Optional[aiohttp.ClientSession] = None
        self._init_session(max_up, max_dl, dht, upnp, natpmp, cache)
        self._setup_signals()
        log.info(f"Client initialisé (Torrent + HTTP): {self.dl_dir}")

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
        """Ajoute un torrent ou un téléchargement HTTP."""
        if source.startswith(('http://', 'https://')) and not source.endswith('.torrent'):
            if len([t for t in self.download_tasks.values() if t.type == DownloadType.HTTP]) >= self.max_http_downloads:
                log.warning("Nombre maximum de téléchargements HTTP atteint")
                return None
            return await self._add_http_download(source, path, cb)
        else:
            return await self._add_torrent(source, path, paused, cb)

    async def _add_http_download(self, url: str, path: Optional[Path], cb: Optional[Callable]) -> Optional[str]:
        """Ajoute un téléchargement HTTP(S) avec support des URLs complexes."""
        try:
            if not self.http_session:
                self.http_session = aiohttp.ClientSession()

            # Extraire le nom de fichier de l'URL (en prenant en compte les paramètres)
            parsed_url = urllib.parse.urlparse(url)
            filename = os.path.basename(parsed_url.path)

            # Pour CloudConvert, utiliser le filename du paramètre response-content-disposition si présent
            if "cloudconvert.com" in parsed_url.netloc:
                query_params = urllib.parse.parse_qs(parsed_url.query)
                if 'response-content-disposition' in query_params:
                    disp = query_params['response-content-disposition'][0]
                    if 'filename=' in disp:
                        filename = disp.split('filename=')[1].strip('"')

            dest_path = (path or self.dl_dir) / filename
            task_id = hashlib.sha256(url.encode()).hexdigest()[:16]

            # Vérification de l'espace disque avec HEAD request
            async with self.http_session.head(url) as resp:
                total_size = int(resp.headers.get('content-length', 0))
                if not self._disk_space(total_size):
                    raise RuntimeError("Espace disque insuffisant")

                task = DownloadTask(
                    type=DownloadType.HTTP,
                    id=task_id,
                    state=TorrentState.DOWNLOADING,
                    total_size=total_size / (1024 * 1024),
                    path=dest_path
                )
                self.download_tasks[task_id] = task

            # Démarrer le téléchargement
            asyncio.create_task(self._download_http_file(task_id, url, dest_path, cb))
            return task_id
        except Exception as e:
            log.error(f"Erreur ajout HTTP: {e}")
            return None

    async def _download_http_file(self, task_id: str, url: str, dest: Path, cb: Optional[Callable]):
        task = self.download_tasks.get(task_id)
        if not task:
            return

        try:
            headers = {}
            # Ajouter des en-têtes spécifiques si nécessaire
            if "freeconvert.com" in url:
                headers.update({"Referer": "https://www.freeconvert.com/"})

            async with self.http_session.get(url, headers=headers) as resp:
                task.http_task = resp
                total_size = int(resp.headers.get('content-length', 0))
                task.total_size = total_size / (1024 * 1024)

                downloaded = 0
                last_time = asyncio.get_event_loop().time()

                # Créer le répertoire parent si nécessaire
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

                # Vérification finale
                if downloaded >= total_size * 0.95:  # Tolérance de 5%
                    task.state = TorrentState.COMPLETED
                else:
                    task.state = TorrentState.ERROR

        except Exception as e:
            log.error(f"Erreur téléchargement HTTP: {e}")
            task.state = TorrentState.ERROR
        finally:
            if task.http_task:
                task.http_task = None
            if cb:
                cb(self._create_http_stats(task))

    def _verify_download(self, file_path: Path, expected_size: int) -> bool:
        """Vérifie si le fichier est complet"""
        if not file_path.exists():
            return False
        return file_path.stat().st_size >= expected_size * 0.95  # Tolère 5% de différence

    def _create_http_stats(self, task: DownloadTask) -> TorrentStats:
        # Vérifie si le fichier existe malgré l'erreur
        is_complete = self._verify_download(task.path, task.total_size * 1024 * 1024)

        return TorrentStats(
            progress=100 if is_complete else task.progress,
            dl_rate=task.speed,
            ul_rate=0,
            speed=task.speed / 1024,
            eta=0 if is_complete else ((task.total_size - task.downloaded) * 1024) / max(task.speed, 0.001),
            peers=0,
            state=TorrentState.COMPLETED if is_complete else task.state,
            wanted=task.total_size,
            done=task.total_size if is_complete else task.downloaded,
            downloaded=task.total_size if is_complete else task.downloaded,
            uploaded=0,
            files=[{
                'path': task.path.name,
                'size': task.total_size,
                'progress': 100 if is_complete else task.progress
            }],
            disk=self._get_disk_usage()
        )

    async def _add_torrent(self, source: str, path: Optional[Path], paused: bool, cb: Optional[Callable]) -> Optional[str]:
        """Logique originale d'ajout de torrent."""
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
            self.download_tasks[tid] = DownloadTask(
                type=DownloadType.TORRENT,
                id=tid,
                handle=h,
                state=TorrentState.DOWNLOADING
            )
            log.info(f"Torrent ajouté {tid}: {h.name()}")

            if cb and not paused:
                asyncio.create_task(self._progress(tid, cb))
            return tid
        except Exception as e:
            log.error(f"Erreur ajout torrent: {e}", exc_info=True)
            raise

    async def _progress(self, tid: str, cb: Callable, interval=5):
        """Suivi de progression pour les torrents."""
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
        """Retourne les statistiques pour un torrent ou un téléchargement HTTP."""
        if task_id in self.handles:
            return await self._get_torrent_stats(task_id)
        elif task_id in self.download_tasks:
            return self._get_http_stats(task_id)
        return None

    async def _get_torrent_stats(self, tid: str) -> Optional[TorrentStats]:
        """Logique originale de récupération des stats des torrents."""
        if tid not in self.handles:
            return None

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
                disk=self._get_disk_usage()
            )
        except Exception as e:
            log.error(f"Erreur stats torrent: {e}")
            return None

    def _get_http_stats(self, task_id: str) -> Optional[TorrentStats]:
        """Retourne les statistiques pour un téléchargement HTTP."""
        task = self.download_tasks.get(task_id)
        if not task:
            return None

        return self._create_http_stats(task)

    def _get_disk_usage(self) -> Optional[Dict]:
        """Retourne les statistiques du disque."""
        try:
            disk = psutil.disk_usage(str(self.dl_dir))
            return {
                'total': disk.total / (1024**3),
                'used': disk.used / (1024**3),
                'percent': disk.percent
            }
        except Exception:
            return None

    async def remove(self, task_id: str, delete_data: bool = True, wait_resume_data: bool = False) -> bool:
        """Supprime un torrent ou un téléchargement HTTP."""
        if task_id in self.handles:
            return await self._remove_torrent(task_id, delete_data, wait_resume_data)
        elif task_id in self.download_tasks:
            return await self._cancel_http_download(task_id, delete_data)
        return False

    async def _remove_torrent(self, tid: str, delete_data: bool, wait_resume_data: bool) -> bool:
        """Logique originale de suppression des torrents."""
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
            self.download_tasks.pop(tid, None)
            log.info(f"Torrent {tid} supprimé (fichiers: {'oui' if delete_data else 'non'})")
            return True

        except Exception as e:
            log.error(f"Échec suppression {tid}: {str(e)}", exc_info=True)
            try:
                if tid in self.handles:
                    del self.handles[tid]
                self.download_tasks.pop(tid, None)
            except:
                pass
            return False

    async def _cancel_http_download(self, task_id: str, delete_file: bool) -> bool:
        """Annule un téléchargement HTTP."""
        try:
            task = self.download_tasks.get(task_id)
            if not task:
                return False

            if task.http_task:
                task.http_task.close()
                task.http_task = None

            if delete_file and task.path and task.path.exists():
                task.path.unlink()
                log.info(f"Fichier HTTP supprimé: {task.path}")

            self.download_tasks.pop(task_id, None)
            return True
        except Exception as e:
            log.error(f"Erreur annulation HTTP: {e}")
            return False

    async def close(self):
        """Nettoie toutes les ressources."""
        # Fermeture des torrents
        for tid in list(self.handles):
            try:
                self.session.remove_torrent(self.handles[tid], False)
                del self.handles[tid]
            except Exception:
                pass

        # Annulation des téléchargements HTTP
        for task_id in list(self.download_tasks.keys()):
            await self._cancel_http_download(task_id, False)

        # Nettoyage final
        self.session.pause()
        if self.http_session:
            await self.http_session.close()
        self.executor.shutdown()
        log.info("Client arrêté proprement")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.close()