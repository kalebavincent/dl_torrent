import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Session Telegram
    SESSION_NAME = os.getenv("SESSION_NAME", "torrent_bot")

    # API Telegram
    API_ID = int(os.getenv("API_ID", 0))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")

    # MongoDB
    MONGO_URI = os.getenv("MONGO_URI", "")

    # Liste des administrateurs
    ADMIN_IDS = [int(id) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip().lstrip('-').isdigit()]
    GROUPS = [int(id) for id in os.getenv("GROUPS", "").split(",") if id.strip().lstrip('-').isdigit()]

    # Nombre max de téléchargements actifs selon l'abonnement
    MAX_ACTIVE_DOWNLOADS = {
        "free": 3,
        "trial": 5,
        "bronze": 10,
        "silver": 15,
        "gold": 25,
        "platinum": 50,
        "enterprise": 100
    }

    # Mode webhook (True/False)
    WEBHOOK = os.getenv("WEBHOOK", "False").lower() == "true"
    WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
    WEB_PORT = int(os.getenv("WEB_PORT", 8080))


config = Config()
print(f"Configuration chargée: {config.GROUPS}, {config.ADMIN_IDS}")