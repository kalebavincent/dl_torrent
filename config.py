import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    def __init__(self):
        # Session Telegram
        self.SESSION_NAME = os.getenv("SESSION_NAME", "torrent_bot")

        # API Telegram
        self.API_ID = int(os.getenv("API_ID", 0))
        self.API_HASH = os.getenv("API_HASH", "")
        self.BOT_TOKEN = os.getenv("BOT_TOKEN", "")

        # MongoDB
        self.MONGO_URI = os.getenv("MONGO_URI", "")

        # Liste des administrateurs
        admin_ids = os.getenv("ADMIN_IDS", "")
        self.ADMIN_IDS = [int(id) for id in admin_ids.split(",") if id.strip().lstrip('-').isdigit()] if admin_ids else []

        groups = os.getenv("GROUPS", "")
        self.GROUPS = [int(id) for id in groups.split(",") if id.strip().lstrip('-').isdigit()] if groups else []

        # Nombre max de téléchargements actifs selon l'abonnement
        self.MAX_ACTIVE_DOWNLOADS = {
            "free": 3,
            "trial": 5,
            "bronze": 10,
            "silver": 15,
            "gold": 25,
            "platinum": 50,
            "enterprise": 100
        }

        # Mode webhook (True/False)
        self.WEBHOOK = os.getenv("WEBHOOK", "False").lower() == "true"
        self.WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
        self.WEB_PORT = int(os.getenv("WEB_PORT", 8080))

        # Configuration Torrent
        self.TORRENT_CONFIG = {
            "DL_DIR": os.getenv("TORRENT_DL_DIR", "/data/downloads"),
            "MIN_PORT": int(os.getenv("TORRENT_MIN_PORT", 6881)),
            "MAX_PORT": int(os.getenv("TORRENT_MAX_PORT", 6891)),
            "MAX_UPLOAD": int(os.getenv("TORRENT_MAX_UPLOAD", 500)),
            "MAX_DOWNLOAD": int(os.getenv("TORRENT_MAX_DOWNLOAD", -1)),
            "DHT_ENABLED": os.getenv("TORRENT_DHT_ENABLED", "True").lower() == "true",
            "UPNP_ENABLED": os.getenv("TORRENT_UPNP_ENABLED", "True").lower() == "true",
            "NATPMP_ENABLED": os.getenv("TORRENT_NATPMP_ENABLED", "True").lower() == "true",
            "TRACKERS": os.getenv("TORRENT_TRACKERS", "").split(";") if os.getenv("TORRENT_TRACKERS") else [
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
            ],
            "MAX_TORRENTS": int(os.getenv("TORRENT_MAX_TORRENTS", 10)),
            "CACHE_SIZE": int(os.getenv("TORRENT_CACHE_SIZE", 2048)),
            "MAX_HTTP_DOWNLOADS": int(os.getenv("TORRENT_MAX_HTTP_DOWNLOADS", 5)),
            "MAX_YOUTUBE_DL": int(os.getenv("TORRENT_MAX_YOUTUBE_DL", 3)),
            "MAX_ARIA2": int(os.getenv("TORRENT_MAX_ARIA2", 5)),
            "ARIA2_PATH": os.getenv("TORRENT_ARIA2_PATH", "/usr/bin/aria2c"),
            "MAX_TASKS_PER_USER": int(os.getenv("TORRENT_MAX_TASKS_PER_USER", 3))
        }

config = Config()
print(f"Configuration chargée: {config.GROUPS}, {config.ADMIN_IDS}")
print(f"Configuration Torrent: {config.TORRENT_CONFIG}")