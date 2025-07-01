import os
import sys
import time
import subprocess
import shutil
import tarfile
import zipfile
import aiohttp
import asyncio
import hashlib
import logging
import logging.handlers
import signal
import json
import re
import math
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
from urllib.parse import urlparse
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from pyrogram.enums import ParseMode, ChatType, ChatMemberStatus
from bot import get_deps
from model.user import Role, SubTier, UserCreate
from utils.torrent import DownloadType, TorrentClient, TorrentStats

deps = get_deps()
logger = logging.getLogger(__name__)

# Configuration des expressions régulières
TORRENT_REGEX = r"^.*\.(torrent)$"
MAGNET_REGEX = r"^magnet:\?xt=urn:btih:[a-zA-Z0-9]{32,40}.*$"
YOUTUBE_REGEX = r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+"
ALLOWED_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".mp3", ".zip", ".rar", ".pdf",
    ".docx", ".xlsx", ".pptx", ".txt", ".cbz", ".cb7", ".cbr", ".cbt", ".cb7z", ".torrent"
}
DIRECT_LINK_REGEX = r"https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"

# Configuration des seuils
CHUNK_SIZE = 1.9 * 1024 * 1024 * 1024  # 1.9GB (juste en dessous de la limite Telegram)
LARGE_FILE_THRESHOLD = 10 * 1024 * 1024 * 1024  # 10GB


class Messages:
    """Classe contenant tous les templates de messages"""

    WELCOME_NEW = """
✨ <b>Bienvenue sur {bot_name} !</b> ✨

🌟 <b>Service Premium de Téléchargement</b>
• 🧲 Prise en charge des liens magnet
• 📥 Gestion des fichiers torrent
• ⚡ Vitesses optimisées
• 🎬 Support YouTube
• 🚀 Envoi de gros fichiers par morceaux

📌 Pour commencer, consultez notre <b>Tutoriel de démarrage</b> ci-dessous.
"""

    WELCOME_RETURNING = """
👋 <b>Bon retour, {user_name} !</b>

🎯 <b>Que souhaitez-vous faire ?</b>
• 📥 Gérer vos téléchargements
• ⚙️ Configurer vos préférences
• 🔍 Découvrir les nouveautés
"""

    MAGNET_DETECTED = """
🧲 <b>Lien Magnet Détecté</b>

🔍 <b>Analyse en cours...</b>
📌 <i>Veuillez patienter pendant la vérification</i>
"""

    DIRECT_LINK_DETECTED = """
📥 <b>Lien Direct Détecté</b>

🔍 <b>Analyse en cours...</b>
📌 <i>Veuillez patienter pendant la vérification</i>
"""

    YOUTUBE_LINK_DETECTED = """
🎬 <b>Lien YouTube Détecté</b>

🔍 <b>Analyse de la vidéo en cours...</b>
📌 <i>Veuillez patienter pendant la vérification</i>
"""

    TORRENT_RECEIVED = """
📂 <b>Fichier Torrent Reçu</b>

🔍 <b>Analyse en cours...</b>
📌 <i>Veuillez patienter pendant la vérification</i>
"""

    QUOTA_EXCEEDED = """
⚠️ <b>Limite de Téléchargements Atteinte</b>

📊 Vous avez trop de téléchargements en cours.
🔄 Veuillez attendre leur achèvement ou
⚡ passer à un abonnement supérieur.
"""

    DOWNLOAD_ERROR = """
❌ <b>Erreur de Téléchargement</b>

🔧 Type: {download_type}
⚠️ Erreur: {error}

📌 Veuillez réessayer ou contacter le support.
"""

    PROGRESS_TEMPLATE = """
📊 <b>Progression du Téléchargement</b>

🏷️ <code>{name}</code>
{progress_bar}
⚡ Vitesse: {speed}
👥 Pairs: {peers}
⏳ Temps restant: {eta}
📦 Taille: {done}|{total}
{file_progress}
"""

    COMPLETED_TEMPLATE = """
✅ <b>Téléchargement Terminé !</b>

🏷️ <code>{name}</code>
📦 Taille totale: {size}
⏱️ Durée: {duration}
⚡ Vitesse moyenne: {avg_speed}

🚀 Démarrage de l'envoi des fichiers...
"""

    TRANSFER_COMPLETE = """
🎉 <b>Transfert Terminé avec Succès !</b>

🏷️ <code>{name}</code>
📦 Fichiers envoyés: {sent}|{total}
⏱️ Durée totale: {duration}

{additional_info}
"""

    CANCELLED = """
❌ <b>Téléchargement Annulé</b>

🏷️ <code>{name}</code>
🛑 Progression: {progress}%
⏱️ Durée: {duration}
"""

    PRIVATE_ACCESS_DENIED = """
🔒 <b>Accès refusé en message privé</b>

🚫 Ce bot ne peut être utilisé en privé que par les administrateurs.
👥 Veuillez utiliser le bot dans l'un de nos groupes autorisés.

📌 Groupes disponibles:
{allowed_groups}
"""

    GROUP_INVITE = """
📢 <b>Rejoignez notre groupe officiel pour utiliser le bot</b>

🌟 Accédez à toutes les fonctionnalités dans notre communauté :
👉 {group_link}
"""

    INVITE_LINK_CREATED = """
🔗 <b>Lien d'invitation créé</b>

📌 Ce lien est valable pour 1 utilisation et sera révoqué automatiquement.
⚠️ Ne partagez ce lien avec personne.

👉 Lien: {invite_link}
"""

    INVITE_LINK_REVOKED = """
🛑 <b>Lien d'invitation révoqué</b>

Le lien d'invitation a été automatiquement révoqué après utilisation.
"""

    NEW_MEMBER_WELCOME = """
👋 <b>Bienvenue {user_mention} dans notre communauté !</b>

📌 Veuillez lire les règles du groupe avant de commencer.
🚀 Pour utiliser le bot, envoyez simplement un lien magnet ou un fichier torrent.
"""

    LARGE_FILE_WARNING = """
⚠️ <b>Fichier volumineux détecté</b>

📁 Fichier: <code>{filename}</code>
📦 Taille: {size}

⚙️ Ce fichier sera envoyé en morceaux de 2GB.
⏳ Veuillez patienter pendant la préparation...
"""

    FILE_CHUNK_SENT = """
✅ <b>Morceau envoyé</b>

📁 Fichier: <code>{filename}</code>
📦 Morceau: {part}/{total_parts}
📊 Progression: {progress}%
"""
    SENDING_TEMPLATE = """
📤 <b>Envoi des fichiers en cours...</b>

🏷️ <code>{name}</code>
{progress_bar}
📦 Fichiers: {sent}/{total} ({progress}%)
⏱ Temps écoulé: {elapsed_time}
⏳ Temps estimé restant: {eta}
📄 Dernier fichier: {last_file}
📊 Vitesse moyenne: {avg_speed}/s
"""

    FILE_CHUNK_PROGRESS = """
📤 <b>Envoi de morceau en cours...</b>

📁 Fichier: <code>{filename}</code>
{progress_bar}
📦 Morceau: {current_chunk}/{total_chunks} ({progress}%)
📊 Taille: {current_size}/{total_size}
⏱ Temps écoulé: {elapsed_time}
⏳ Temps estimé restant: {eta}
"""

def format_message(template: str, **kwargs) -> str:
    return template.format(**kwargs).strip()

def is_valid_direct_link(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        path = Path(parsed.path)
        return path.suffix.lower() in ALLOWED_EXTENSIONS
    except:
        return False

def extract_direct_link(text: str) -> Optional[str]:
    match = re.search(DIRECT_LINK_REGEX, text)
    return match.group(0) if match and is_valid_direct_link(match.group(0)) else None

def extract_magnet_link(text: str) -> Optional[str]:
    match = re.search(MAGNET_REGEX, text)
    return match.group(0) if match else None

def extract_youtube_link(text: str) -> Optional[str]:
    match = re.search(YOUTUBE_REGEX, text)
    return match.group(0) if match else None

def is_torrent_file(filename: str) -> bool:
    return bool(re.match(TORRENT_REGEX, filename, re.IGNORECASE))

async def get_download_type(source: str) -> str:
    if extract_magnet_link(source):
        return DownloadType.TORRENT
    elif extract_direct_link(source):
        return DownloadType.HTTP
    elif extract_youtube_link(source):
        return DownloadType.YOUTUBE_DL
    elif is_torrent_file(source):
        return DownloadType.TORRENT
    return DownloadType.ARIA2

active_downloads: Dict[str, Dict[str, Any]] = {}
file_chunk_status: Dict[str, Dict[str, Any]] = {}

async def validate_user_quota(user_id: int) -> bool:
    user = await deps.user_manager.get_user(user_id)
    if not user:
        return False
    active_count = len([d for d in active_downloads.values() if d["user_id"] == user_id])
    return active_count < user.settings.max_parallel

def format_speed(speed: float) -> str:
    if speed < 1024:
        return f"{speed:.1f} B/s"
    elif speed < 1024 * 1024:
        return f"{speed/1024:.1f} KB/s"
    else:
        return f"{speed/(1024*1024):.1f} MB/s"

def format_size(size_bytes: float) -> str:
    """Formate une taille en octets en chaîne lisible"""
    if size_bytes < 1024:
        return f"{size_bytes:.0f} B"
    elif size_bytes < 1024*1024:
        return f"{size_bytes/1024:.1f} KB"
    elif size_bytes < 1024*1024*1024:
        return f"{size_bytes/(1024*1024):.1f} MB"
    else:
        return f"{size_bytes/(1024*1024*1024):.1f} GB"

def format_time(seconds: float) -> str:
    if seconds == float("inf"):
        return "∞"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"

def create_progress_bar(progress: float, width: int = 10) -> str:
    progress = min(100, max(0, progress))
    filled = math.ceil(width * progress / 100)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}] {progress:.1f}%"

def group_or_admin_filter():
    async def func(_, client, message: Message):
        if message.chat.id in deps.config.GROUPS:
            return True
        if message.chat.type == "private":
            return message.from_user.id in deps.config.ADMIN_IDS
        return False
    return filters.create(func)

def admin_only_filter():
    async def func(_, client, message: Message):
        return message.from_user.id in deps.config.ADMIN_IDS
    return filters.create(func)

group_or_admin = group_or_admin_filter()
admin_only = admin_only_filter()

@Client.on_callback_query(filters.regex(r"^open_([a-zA-Z0-9]+)$"))
async def handle_open_download(client: Client, callback_query: CallbackQuery):
    try:
        download_id = callback_query.matches[0].group(1)
        if download_id not in active_downloads:
            await callback_query.answer("❌ Téléchargement introuvable", show_alert=True)
            return

        # Vérifier les permissions
        download_info = active_downloads[download_id]
        user_id = callback_query.from_user.id
        if user_id != download_info["user_id"] and user_id not in deps.config.ADMIN_IDS:
            await callback_query.answer("🔒 Accès refusé : Vous n'êtes pas propriétaire de ce téléchargement", show_alert=True)
            return

        dl_path = Path(download_info["dl_path"])
        if not dl_path.exists() or not dl_path.is_dir():
            await callback_query.answer("❌ Dossier introuvable", show_alert=True)
            return
        if os.name == "nt":
            os.startfile(dl_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", str(dl_path)])
        else:
            subprocess.run(["xdg-open", str(dl_path)])
        await callback_query.answer(f"📁 Dossier ouvert: {dl_path.name}")
    except Exception as e:
        logger.error(f"Erreur ouverture dossier: {e}")
        await callback_query.answer("❌ Impossible d'ouvrir le dossier", show_alert=True)

@Client.on_callback_query(filters.regex(r"^cancel_([a-zA-Z0-9]+)$"))
async def handle_cancel_download(client: Client, callback_query: CallbackQuery):
    try:
        download_id = callback_query.matches[0].group(1)
        if download_id not in active_downloads:
            await callback_query.answer("❌ Téléchargement introuvable", show_alert=True)
            return

        # Vérifier les permissions
        download_info = active_downloads[download_id]
        user_id = callback_query.from_user.id
        if user_id != download_info["user_id"] and user_id not in deps.config.ADMIN_IDS:
            await callback_query.answer("🔒 Accès refusé : Vous n'êtes pas propriétaire de ce téléchargement", show_alert=True)
            return

        stats = await deps.torrent_client.stats(download_id)
        progress = stats.progress if stats else 0
        duration = format_time(active_downloads[download_id].get("duration", 0))
        success = await deps.torrent_client.remove(download_id, delete_data=True)
        if success:
            if download_id in active_downloads:
                dl_info = active_downloads[download_id]
                try:
                    dl_path = Path(dl_info["dl_path"])
                    if dl_path.exists() and dl_path.is_dir():
                        shutil.rmtree(dl_path, ignore_errors=True)
                except Exception as e:
                    logger.error(f"Erreur nettoyage dossier: {e}")
                if "temp_path" in dl_info:
                    try:
                        temp_path = Path(dl_info["temp_path"])
                        if temp_path.exists():
                            temp_path.unlink(missing_ok=True)
                    except Exception as e:
                        logger.error(f"Erreur suppression temp: {e}")
                name = dl_info.get("name", "Inconnu")
                del active_downloads[download_id]
            await callback_query.message.edit_text(
                format_message(
                    Messages.CANCELLED, name=name, progress=progress, duration=duration
                ),
                parse_mode=ParseMode.HTML,
            )
            await callback_query.answer("✅ Téléchargement annulé", show_alert=True)
        else:
            await callback_query.answer("❌ Échec de l'annulation", show_alert=True)
    except Exception as e:
        logger.error(f"Erreur annulation: {e}")
        await callback_query.answer("❌ Erreur lors de l'annulation", show_alert=True)

@Client.on_message(filters.command("start", prefixes=["/", "!"]) & filters.group)
async def start_groupe(client: Client, message: Message):
    await deps.startup()
    user = message.from_user
    user_data = UserCreate(
        uid=user.id,
        uname=user.username,
        first=user.first_name or "",
        last=user.last_name or "",
        lang_code=user.language_code or "fr",
        sub=SubTier.FREE,
        role=Role.USER,
    )
    bot_info = await client.get_me()
    existing_user = await deps.user_manager.get_user(user.id)
    if not existing_user:
        await deps.user_manager.create_user(user_data)
        await message.reply_text(
            format_message(Messages.WELCOME_NEW, bot_name=bot_info.first_name),
            reply_markup=get_main_keyboard(is_new_user=True),
            parse_mode=ParseMode.HTML,
        )
    else:
        await message.reply_text(
            format_message(Messages.WELCOME_RETURNING, user_name=user.first_name),
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML,
        )

@Client.on_message(filters.command("start", prefixes=["/", "!"]) & filters.private)
async def start_command(client: Client, message: Message):
    try:
        await deps.startup()
        bot_info = await client.get_me()
        user = message.from_user
        if not user:
            await message.reply_text("🔴 Erreur : Impossible d'identifier votre compte.")
            return
        if user.id not in deps.config.ADMIN_IDS:
            if not deps.config.GROUPS:
                await message.reply_text(
                    "🚫 Ce bot ne peut être utilisé qu'en groupe. Aucun groupe autorisé n'est configuré.",
                    parse_mode=ParseMode.HTML
                )
                return
            group_id = deps.config.GROUPS[0]
            try:
                invite_link = await client.create_chat_invite_link(
                    chat_id=group_id,
                    name=f"Invite_{user.id}",
                    member_limit=1,
                    creates_join_request=False
                )
                if not hasattr(deps, "active_invite_links"):
                    deps.active_invite_links = {}
                deps.active_invite_links[invite_link.invite_link] = {
                    "chat_id": group_id,
                    "created_at": time.time(),
                    "creator": "auto_system",
                    "user_id": user.id
                }
                await message.reply_text(
                    "👋 <b>Bienvenue !</b>\n\n"
                    "Ce bot ne peut être utilisé que dans notre groupe.\n\n"
                    f"🔗 Lien d'invitation: {invite_link.invite_link}\n"
                    "⚠️ Valable pour 1 seule utilisation\n\n"
                    "Cliquez ci-dessous pour rejoindre :",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🎯 Rejoindre le groupe", url=invite_link.invite_link)]
                    ])
                )
                return
            except Exception as e:
                logger.error(f"Erreur création lien: {e}")
                group_link = f"https://t.me/c/{str(group_id)[4:] if str(group_id).startswith('-100') else group_id}"
                await message.reply_text(
                    "👋 <b>Bienvenue !</b>\n\n"
                    "Rejoignez notre groupe pour utiliser le bot:\n"
                    f"👉 {group_link}",
                    parse_mode=ParseMode.HTML
                )
                return
        user_data = UserCreate(
            uid=user.id,
            uname=user.username,
            first=user.first_name or "",
            last=user.last_name or "",
            lang_code=user.language_code or "fr",
            sub=SubTier.FREE,
            role=Role.USER,
        )
        existing_user = await deps.user_manager.get_user(user.id)
        if not existing_user:
            await deps.user_manager.create_user(user_data)
            await message.reply_text(
                format_message(Messages.WELCOME_NEW, bot_name=bot_info.first_name),
                reply_markup=get_main_keyboard(is_new_user=True),
                parse_mode=ParseMode.HTML,
            )
        else:
            await message.reply_text(
                format_message(Messages.WELCOME_RETURNING, user_name=user.first_name),
                reply_markup=get_main_keyboard(),
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        logger.error(f"Erreur commande /start: {e}", exc_info=True)
        await message.reply_text("⚠️ <b>Service temporairement indisponible</b>", parse_mode=ParseMode.HTML)

def get_main_keyboard(is_new_user: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("📖 Guide", callback_data="help"),
            InlineKeyboardButton("⚙️ Préférences", callback_data="settings"),
        ],
    ]
    if is_new_user:
        buttons.append([InlineKeyboardButton("🎬 Tutoriel", callback_data="tutorial")])
    buttons.extend([
        [InlineKeyboardButton("📌 À propos", callback_data="about")],
        [InlineKeyboardButton("🔄 Mises à jour", callback_data="update")],
    ])
    return InlineKeyboardMarkup(buttons)

def get_download_keyboard(download_id: str) -> InlineKeyboardMarkup:
    """Retourne un clavier simplifié sans boutons Actualiser/Envoyer"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📂 Ouvrir", callback_data=f"open_{download_id}"),
            InlineKeyboardButton("❌ Annuler", callback_data=f"cancel_{download_id}")
        ]
    ])

@Client.on_message(filters.text & group_or_admin)
async def handle_download_requests(client: Client, message: Message):
    user = message.from_user
    text = message.text.strip()
    download_type = await get_download_type(text)
    if download_type not in [DownloadType.TORRENT, DownloadType.HTTP, DownloadType.YOUTUBE_DL]:
        return
    if not await validate_user_quota(user.id):
        await message.reply_text(
            format_message(Messages.QUOTA_EXCEEDED),
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.id,
        )
        return
    try:
        # Créer un dossier unique avec timestamp
        timestamp = int(time.time())
        dl_path = Path("downloads") / f"{user.id}_{timestamp}"
        dl_path.mkdir(parents=True, exist_ok=True)

        # Déterminer le type de téléchargement
        if download_type == DownloadType.TORRENT:
            start_msg = format_message(Messages.MAGNET_DETECTED)
        elif download_type == DownloadType.HTTP:
            start_msg = format_message(Messages.DIRECT_LINK_DETECTED)
        elif download_type == DownloadType.YOUTUBE_DL:
            start_msg = format_message(Messages.YOUTUBE_LINK_DETECTED)

        # Ajouter le téléchargement
        download_id = await deps.torrent_client.add(
            source=text,
            path=dl_path,
            download_type=download_type,
            user_id=str(user.id)
        )

        if not download_id:
            raise ValueError("Échec de l'ajout du téléchargement")

        # Enregistrer le téléchargement
        active_downloads[download_id] = {
            "user_id": user.id,
            "type": download_type,
            "dl_path": str(dl_path),
            "start_time": asyncio.get_event_loop().time(),
            "name": text[:50] + ("..." if len(text) > 50 else ""),
            "source": text,
            "completed_files": []
        }

        # Envoyer la réponse
        response = await message.reply_text(
            start_msg,
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.id,
            reply_markup=get_download_keyboard(download_id)
        )

        # Démarrer le suivi de progression
        asyncio.create_task(
            send_progress_update(client, user.id, download_id, response)
        )
    except Exception as e:
        logger.error(f"Erreur téléchargement: {str(e)}", exc_info=True)
        await message.reply_text(
            format_message(
                Messages.DOWNLOAD_ERROR,
                download_type=download_type.name,
                error=str(e),
            ),
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.id,
        )

@Client.on_message(filters.document & group_or_admin)
async def handle_torrent_files(client: Client, message: Message):
    if not is_torrent_file(message.document.file_name):
        return
    user = message.from_user
    if not await validate_user_quota(user.id):
        await message.reply_text(
            format_message(Messages.QUOTA_EXCEEDED),
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.id,
        )
        return
    try:
        # Créer un dossier temporaire
        temp_path = Path(f"temp/{user.id}_{int(time.time())}_{message.document.file_name}")
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        await message.download(file_name=str(temp_path))

        # Créer un dossier de destination unique
        timestamp = int(time.time())
        dl_path = Path("downloads") / f"{user.id}_{timestamp}"
        dl_path.mkdir(parents=True, exist_ok=True)

        # Ajouter le torrent
        download_id = await deps.torrent_client.add(str(temp_path), dl_path)
        if not download_id:
            raise ValueError("Échec de l'ajout du torrent")

        # Enregistrer le téléchargement
        active_downloads[download_id] = {
            "user_id": user.id,
            "type": "torrent",
            "dl_path": str(dl_path),
            "start_time": asyncio.get_event_loop().time(),
            "name": message.document.file_name,
            "temp_path": str(temp_path),
            "completed_files": []
        }

        # Envoyer la réponse
        response = await message.reply_text(
            format_message(Messages.TORRENT_RECEIVED),
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.id,
            reply_markup=get_download_keyboard(download_id)
        )

        # Démarrer le suivi de progression
        asyncio.create_task(
            send_progress_update(client, user.id, download_id, response)
        )
    except Exception as e:
        logger.error(f"Torrent error: {e}")
        await message.reply_text(
            format_message(
                Messages.DOWNLOAD_ERROR, download_type="Torrent", error=str(e)
            ),
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.id,
        )
        if "temp_path" in locals() and temp_path.exists():
            temp_path.unlink(missing_ok=True)

async def send_progress_update(client: Client, user_id: int, download_id: str, msg: Message):
    start_time = asyncio.get_event_loop().time()
    last_progress = 0
    last_update = time.time()
    while download_id in active_downloads:
        try:
            stats = await deps.torrent_client.stats(download_id)
            if not stats:
                break

            current_time = asyncio.get_event_loop().time()
            active_downloads[download_id]["duration"] = current_time - start_time

            # Formatage des données de progression
            progress_data = {
                "name": active_downloads[download_id]["name"],
                "progress_bar": create_progress_bar(stats.progress),
                "speed": format_speed(stats.dl_rate * 1024),
                "peers": stats.peers,
                "eta": format_time(stats.eta),
                "done": format_size(stats.done * 1024 * 1024),
                "total": format_size(stats.wanted * 1024 * 1024),
                "file_progress": ""
            }

            # Affichage du fichier courant
            if stats.current_file:
                progress_data["file_progress"] = (
                    f"\n📄 Fichier actuel: {stats.current_file['name']}\n"
                    f"{create_progress_bar(stats.current_file['progress'])}"
                    f"\n{format_size(stats.current_file['downloaded'])}/{format_size(stats.current_file['size'])}"
                )

            # Informations spécifiques à YouTube
            if active_downloads[download_id].get("type") == DownloadType.YOUTUBE_DL:
                progress_data["name"] = "Vidéo YouTube"
                if active_downloads[download_id].get("metadata"):
                    meta = active_downloads[download_id]["metadata"]
                    progress_data["name"] = meta.get("title", "Vidéo YouTube")

                    # Formatage de la durée
                    duration = meta.get("duration", 0)
                    minutes, seconds = divmod(duration, 60)
                    formatted_duration = f"{minutes}min{seconds:02d}s"

                    # Ajout des métadonnées
                    progress_data["file_progress"] += (
                        f"\n🎬 Chaîne: {meta.get('uploader', 'Inconnu')}"
                        f"\n⏱ Durée: {formatted_duration}"
                    )

            # Mise à jour du message toutes les 5 secondes ou quand le téléchargement est presque fini
            if time.time() - last_update > 5 or stats.progress >= 99.9:
                await msg.edit_text(
                    format_message(Messages.PROGRESS_TEMPLATE, **progress_data),
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_download_keyboard(download_id)
                )
                last_update = time.time()

            # Fin du téléchargement
            if stats.progress >= 99.9:
                await handle_download_complete(client, user_id, download_id, msg)
                break

            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Erreur mise à jour: {str(e)}", exc_info=True)
            break

async def split_large_file(file_path: Path, chunk_size: int = CHUNK_SIZE) -> Tuple[List[Path], int]:
    """Divise un fichier volumineux en morceaux"""
    chunks = []
    part_num = 1
    total_size = file_path.stat().st_size
    total_parts = (total_size + chunk_size - 1) // chunk_size

    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            chunk_path = file_path.with_name(f"{file_path.stem}.part{part_num:03d}")
            with open(chunk_path, 'wb') as chunk_file:
                chunk_file.write(chunk)
            chunks.append(chunk_path)
            part_num += 1

    return chunks, total_parts

async def handle_download_complete(client: Client, user_id: int, download_id: str, msg: Message):
    """Gère la complétion d'un téléchargement et démarre automatiquement l'envoi des fichiers"""
    if download_id not in active_downloads:
        return
    download_info = active_downloads[download_id]
    try:
        stats = await deps.torrent_client.stats(download_id)
        if not stats:
            raise ValueError("Aucune statistique disponible")

        duration = download_info.get("duration", 0)
        completed_data = {
            "name": download_info["name"],
            "size": format_size(stats.wanted * 1024 * 1024),
            "duration": format_time(duration),
            "avg_speed": format_speed((stats.wanted * 1024 * 1024) / max(1, duration)),
        }

        # Retirer la tâche du client torrent (conserver les données)
        await deps.torrent_client.remove(download_id, delete_data=False)

        # Mettre à jour le message pour indiquer que l'envoi démarre
        await msg.edit_text(
            format_message(Messages.COMPLETED_TEMPLATE, **completed_data),
            parse_mode=ParseMode.HTML
        )

        # Démarrer l'envoi automatique des fichiers
        await send_files_automatically(client, user_id, download_id, msg)

    except Exception as e:
        logger.error(f"Erreur complétion: {str(e)}")
        await msg.edit_text(f"❌ <b>Erreur lors du transfert</b>\n\n{str(e)}", parse_mode=ParseMode.HTML)

async def send_files_automatically(client: Client, user_id: int, download_id: str, msg: Message):
    """Envoie automatiquement les fichiers après téléchargement complet avec progression"""
    if download_id not in active_downloads:
        await msg.edit_text("❌ Téléchargement introuvable")
        return

    download_info = active_downloads[download_id]
    dl_path = Path(download_info['dl_path'])
    if not dl_path.exists() or not dl_path.is_dir():
        await msg.edit_text("❌ Dossier de téléchargement introuvable")
        return

    # Lister tous les fichiers
    files = [f for f in dl_path.rglob('*') if f.is_file() and not f.name.startswith('.')]
    total_files = len(files)
    sent_files = 0
    total_size = sum(f.stat().st_size for f in files)
    sent_size = 0
    start_time = time.time()
    last_update_time = start_time

    # Préparer le message initial
    await msg.edit_text(
        format_message(
            Messages.SENDING_TEMPLATE,
            name=download_info['name'],
            progress_bar=create_progress_bar(0),
            sent=0,
            total=total_files,
            progress=0,
            elapsed_time=format_time(0),
            eta="Calcul...",
            last_file="En attente...",
            avg_speed="0 B"
        ),
        parse_mode=ParseMode.HTML
    )

    for file_path in files:
        try:
            file_size = file_path.stat().st_size
            file_name = file_path.name
            chunk_index = 0

            # Envoyer un avertissement pour les fichiers volumineux
            if file_size > LARGE_FILE_THRESHOLD:
                await msg.reply_text(
                    format_message(
                        Messages.LARGE_FILE_WARNING,
                        filename=file_name,
                        size=format_size(file_size)
                    ),
                    parse_mode=ParseMode.HTML
                )

            # Découper et envoyer les fichiers volumineux
            if file_size > CHUNK_SIZE:
                chunks, total_chunks = await split_large_file(file_path)

                for i, chunk_path in enumerate(chunks):
                    chunk_index = i + 1
                    chunk_size = chunk_path.stat().st_size
                    chunk_start_time = time.time()

                    # Mettre à jour la progression du morceau
                    chunk_progress = (i / total_chunks) * 100
                    await msg.edit_text(
                        format_message(
                            Messages.FILE_CHUNK_PROGRESS,
                            filename=file_name,
                            progress_bar=create_progress_bar(chunk_progress),
                            current_chunk=i,
                            total_chunks=total_chunks,
                            progress=f"{chunk_progress:.1f}",
                            current_size=format_size(i * CHUNK_SIZE),
                            total_size=format_size(file_size),
                            elapsed_time=format_time(time.time() - start_time),
                            eta="Calcul..."
                        ),
                        parse_mode=ParseMode.HTML
                    )

                    # Envoyer le morceau
                    await client.send_document(
                        chat_id=user_id,
                        document=str(chunk_path),
                        caption=f"📁 {file_name} (Partie {chunk_index}/{total_chunks})",
                        disable_notification=True
                    )

                    # Mettre à jour les statistiques
                    sent_size += chunk_size
                    chunk_path.unlink(missing_ok=True)

                    # Calculer la vitesse d'envoi
                    chunk_duration = time.time() - chunk_start_time
                    chunk_speed = chunk_size / max(0.1, chunk_duration)

                    # Mettre à jour toutes les 15 secondes ou pour le dernier morceau
                    current_time = time.time()
                    if current_time - last_update_time > 15 or chunk_index == total_chunks:
                        elapsed = current_time - start_time
                        progress_percent = (sent_size / total_size) * 100

                        # Calculer le temps restant
                        if sent_size > 0:
                            remaining_size = total_size - sent_size
                            avg_speed = sent_size / elapsed
                            eta_seconds = remaining_size / avg_speed if avg_speed > 0 else 0
                        else:
                            eta_seconds = 0

                        await msg.edit_text(
                            format_message(
                                Messages.SENDING_TEMPLATE,
                                name=download_info['name'],
                                progress_bar=create_progress_bar(progress_percent),
                                sent=sent_files,
                                total=total_files,
                                progress=f"{progress_percent:.1f}",
                                elapsed_time=format_time(elapsed),
                                eta=format_time(eta_seconds),
                                last_file=file_name,
                                avg_speed=format_speed(avg_speed)
                            ),
                            parse_mode=ParseMode.HTML
                        )
                        last_update_time = current_time
            else:
                # Envoyer le fichier normal
                await client.send_document(
                    chat_id=user_id,
                    document=str(file_path),
                    caption=f"📁 {file_name}",
                    disable_notification=True
                )
                sent_size += file_size

            sent_files += 1

            # Mettre à jour après chaque fichier
            elapsed = time.time() - start_time
            progress_percent = (sent_size / total_size) * 100

            # Calculer le temps restant
            if sent_size > 0:
                remaining_size = total_size - sent_size
                avg_speed = sent_size / elapsed
                eta_seconds = remaining_size / avg_speed if avg_speed > 0 else 0
            else:
                eta_seconds = 0

            await msg.edit_text(
                format_message(
                    Messages.SENDING_TEMPLATE,
                    name=download_info['name'],
                    progress_bar=create_progress_bar(progress_percent),
                    sent=sent_files,
                    total=total_files,
                    progress=f"{progress_percent:.1f}",
                    elapsed_time=format_time(elapsed),
                    eta=format_time(eta_seconds),
                    last_file=file_name,
                    avg_speed=format_speed(sent_size / elapsed)
                ),
                parse_mode=ParseMode.HTML
            )

            # Supprimer le fichier après envoi
            file_path.unlink(missing_ok=True)

        except Exception as e:
            logger.error(f"Erreur envoi fichier {file_path}: {e}")
            await msg.reply_text(f"❌ Échec de l'envoi du fichier {file_path.name}: {str(e)}")

    # Nettoyage final
    try:
        if dl_path.exists() and dl_path.is_dir():
            if not any(dl_path.iterdir()):
                dl_path.rmdir()
    except Exception as e:
        logger.error(f"Erreur suppression dossier: {e}")

    # Calculer les statistiques finales
    duration = time.time() - start_time
    avg_speed = sent_size / duration if duration > 0 else 0

    await msg.edit_text(
        format_message(
            Messages.TRANSFER_COMPLETE,
            name=download_info['name'],
            sent=sent_files,
            total=total_files,
            duration=format_time(duration),
            additional_info=(
                f"📦 Taille totale: {format_size(total_size)}\n"
                f"⚡ Vitesse moyenne: {format_speed(avg_speed)}/s"
            )
        ),
        parse_mode=ParseMode.HTML
    )

    if download_id in active_downloads:
        del active_downloads[download_id]

@Client.on_callback_query(filters.regex(r"^convert_([a-zA-Z0-9]+)_([a-z0-9]+)_([a-z]+)$"))
async def handle_conversion_request(client: Client, callback_query: CallbackQuery):
    try:
        data = callback_query.matches[0]
        task_id = data.group(1)
        output_format = data.group(2)
        quality = data.group(3)
        if task_id not in active_downloads:
            await callback_query.answer("❌ Tâche introuvable", show_alert=True)
            return
        await callback_query.answer("⚙️ Démarrage de la conversion...")
        conversion_id = await deps.torrent_client.convert_file_format(
            task_id,
            output_format,
            quality
        )
        if conversion_id:
            active_downloads[conversion_id] = {
                "user_id": callback_query.from_user.id,
                "type": "conversion",
                "source_task": task_id,
                "format": output_format,
                "quality": quality,
                "start_time": asyncio.get_event_loop().time()
            }
            await callback_query.message.edit_text(
                format_message(
                    Messages.CONVERSION_STARTED,
                    format=output_format.upper(),
                    quality=quality,
                    eta="5-10 minutes"
                ),
                parse_mode=ParseMode.HTML
            )
        else:
            await callback_query.message.edit_text(
                "❌ Échec du démarrage de la conversion",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Erreur conversion: {str(e)}")
        await callback_query.answer("❌ Erreur lors de la conversion")

@Client.on_message(filters.command("cleanup", prefixes=["/", "!"]) & admin_only)
async def cleanup_command(client: Client, message: Message):
    """Nettoyage manuel des téléchargements bloqués (admin seulement)"""
    await cleanup_stalled_downloads()
    await message.reply_text("✅ Nettoyage effectué")

@Client.on_message(filters.command("stats", prefixes=["/", "!"]) & admin_only)
async def stats_command(client: Client, message: Message):
    """Affiche les statistiques globales du client de téléchargement (admin seulement)"""
    try:
        stats = await deps.torrent_client.get_global_stats()
        total_tasks = stats.get("total_tasks", 0)
        dl_speed = stats.get("total_download_speed", 0)
        ul_speed = stats.get("total_upload_speed", 0)
        disk = stats.get("disk", {})
        active_tasks = stats.get("active_tasks", [])
        completed_tasks = stats.get("completed_tasks", [])
        text = (
            "📊 <b>Statistiques Globales</b>\n\n"
            f"🔄 <b>Tâches totales:</b> {total_tasks}\n"
            f"⬇️ <b>Vitesse DL:</b> {dl_speed:.1f} kB/s\n"
            f"⬆️ <b>Vitesse UL:</b> {ul_speed:.1f} kB/s\n"
        )
        if disk:
            text += (
                f"💾 <b>Disque:</b> {disk['used']:.1f}GB / {disk['total']:.1f}GB "
                f"({disk['percent']}%)\n"
            )
        text += (
            f"\n📥 <b>Téléchargements actifs:</b> {len(active_tasks)}\n"
            f"✅ <b>Téléchargements terminés:</b> {len(completed_tasks)}"
        )
        await message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Erreur stats: {e}", exc_info=True)
        await message.reply_text("❌ Erreur lors de la récupération des statistiques")

@Client.on_message(filters.command("broadcast", prefixes=["/", "!"]) & admin_only)
async def broadcast_command(client: Client, message: Message):
    """Diffuse un message à tous les utilisateurs (admin seulement)"""
    if not message.reply_to_message:
        await message.reply_text("❌ Veuillez répondre à un message à diffuser")
        return
    users = await deps.user_manager.get_all_users()
    total = len(users)
    success = 0
    failed = 0
    progress_msg = await message.reply_text(
        f"📢 Diffusion en cours...\n\n"
        f"✅ Succès: {success}\n"
        f"❌ Échecs: {failed}\n"
        f"📊 Total: {total}"
    )
    for user in users:
        try:
            await message.reply_to_message.copy(user.uid)
            success += 1
        except Exception as e:
            logger.error(f"Erreur diffusion à {user.uid}: {e}")
            failed += 1
        if success % 10 == 0 or success + failed == total:
            await progress_msg.edit_text(
                f"📢 Diffusion en cours...\n\n"
                f"✅ Succès: {success}\n"
                f"❌ Échecs: {failed}\n"
                f"📊 Total: {total}"
            )
    await progress_msg.edit_text(
        f"🎉 Diffusion terminée !\n\n"
        f"✅ Succès: {success}\n"
        f"❌ Échecs: {failed}\n"
        f"📊 Total: {total}"
    )

async def cleanup_stalled_downloads():
    """Nettoie les téléchargements bloqués"""
    current_time = time.time()
    for dl_id, dl_info in list(active_downloads.items()):
        try:
            start_time = dl_info.get("start_time", current_time)
            duration = current_time - start_time

            # Téléchargements actifs bloqués depuis plus de 2 heures
            if duration > 7200:  # 2 heures
                logger.warning(f"Nettoyage téléchargement bloqué: {dl_id}")

                # Annuler le téléchargement dans le client torrent
                await deps.torrent_client.remove(dl_id, delete_data=True)

                # Supprimer le dossier de téléchargement
                dl_path = Path(dl_info.get('dl_path', ''))
                if dl_path.exists() and dl_path.is_dir():
                    shutil.rmtree(dl_path, ignore_errors=True)

                # Supprimer le fichier temporaire s'il existe
                if 'temp_path' in dl_info:
                    temp_path = Path(dl_info['temp_path'])
                    if temp_path.exists():
                        temp_path.unlink(missing_ok=True)

                # Retirer de active_downloads
                del active_downloads[dl_id]

        except Exception as e:
            logger.error(f"Erreur nettoyage bloqué {dl_id}: {e}")

async def periodic_cleanup():
    """Tâche périodique de nettoyage"""
    while True:
        try:
            await cleanup_stalled_downloads()
            logger.info("Nettoyage périodique effectué")
        except Exception as e:
            logger.error(f"Erreur nettoyage périodique: {e}")
        await asyncio.sleep(3600)  # Toutes les heures

# Démarrer la tâche de nettoyage périodique
# asyncio.create_task(periodic_cleanup())