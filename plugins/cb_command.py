import os
import sys
import time
import subprocess
from urllib.parse import urlparse
from pyrogram import Client, filters
from pyrogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    ChatJoinRequest,
)
from pyrogram.enums import ParseMode, ChatType, ChatMemberStatus
from bot import Dependencies
from model.user import Role, SubTier, UserCreate
from typing import Any, Optional, Dict, List
import logging
import re
import asyncio
from pathlib import Path
import math

deps = Dependencies()
logger = logging.getLogger(__name__)

# Configuration des expressions régulières
TORRENT_REGEX = r"^.*\.(torrent)$"
MAGNET_REGEX = r"^magnet:\?xt=urn:btih:[a-zA-Z0-9]{32,40}.*$"
ALLOWED_EXTENSIONS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".mp3",
    ".zip",
    ".rar",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".txt",
    ".cbz",
    ".cb7",
    ".cbr",
    ".cbt",
    ".cb7z",
    ".torrent",
}
DIRECT_LINK_REGEX = r"https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"

class Messages:
    """Classe contenant tous les templates de messages"""

    WELCOME_NEW = """
✨ <b>Bienvenue sur {bot_name} !</b> ✨

🌟 <b>Service Premium de Téléchargement</b>
• 🧲 Prise en charge des liens magnet
• 📥 Gestion des fichiers torrent
• ⚡ Vitesses optimisées

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

📁 Préparation des fichiers...
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


def format_message(template: str, **kwargs) -> str:
    """Formate un message template avec les kwargs"""
    return template.format(**kwargs).strip()


def is_valid_direct_link(url: str) -> bool:
    """Valide que le lien pointe vers un fichier téléchargeable"""
    try:
        parsed = urlparse(url)
        if not parsed.scheme in ("http", "https"):
            return False

        path = Path(parsed.path)
        if not path.suffix.lower() in ALLOWED_EXTENSIONS:
            return False

        return True
    except:
        return False


def extract_direct_link(text: str) -> Optional[str]:
    """Extrait et valide un lien direct"""
    match = re.search(DIRECT_LINK_REGEX, text)
    if not match:
        return None

    url = match.group(0)
    return url if is_valid_direct_link(url) else None


active_downloads: Dict[str, Dict[str, Any]] = {}


def extract_magnet_link(text: str) -> Optional[str]:
    """Extrait un lien magnet d'un texte"""
    match = re.search(MAGNET_REGEX, text)
    return match.group(0) if match else None


def is_torrent_file(filename: str) -> bool:
    """Vérifie si un fichier est un fichier torrent"""
    return bool(re.match(TORRENT_REGEX, filename, re.IGNORECASE))


async def validate_user_quota(user_id: int) -> bool:
    """Vérifie si l'utilisateur peut lancer un nouveau téléchargement"""
    user = await deps.user_manager.get_user(user_id)
    if not user:
        return False

    active_count = len([d for d in active_downloads.values() if d["user_id"] == user_id])
    return active_count < user.settings.max_parallel


async def cleanup_stalled_downloads(client: Client):
    """Nettoie les téléchargements bloqués"""
    for dl_id, dl_info in list(active_downloads.items()):
        if "start_time" in dl_info:
            duration = asyncio.get_event_loop().time() - dl_info["start_time"]
            if duration > 7200:  # 2 heures
                logger.warning(f"Nettoyage du téléchargement bloqué {dl_id}")
                try:
                    await client.send_message(
                        chat_id=dl_info["user_id"],
                        text="🛑 <b>Téléchargement annulé</b>\n\nLe téléchargement a été bloqué trop longtemps.",
                        parse_mode=ParseMode.HTML,
                    )
                    dl_path = Path(dl_info.get("dl_path", ""))
                    if dl_path.exists():
                        for file in dl_path.glob("*"):
                            try:
                                file.unlink()
                            except:
                                pass
                        try:
                            dl_path.rmdir()
                        except:
                            pass
                except Exception as e:
                    logger.error(f"Erreur nettoyage bloqué {dl_id}: {e}")
                finally:
                    if dl_id in active_downloads:
                        del active_downloads[dl_id]


def format_speed(speed: float) -> str:
    """Formate la vitesse de téléchargement"""
    if speed < 1024:
        return f"{speed:.1f} B/s"
    elif speed < 1024 * 1024:
        return f"{speed/1024:.1f} KB/s"
    else:
        return f"{speed/(1024*1024):.1f} MB/s"


def format_size(size: float) -> str:
    """Formate la taille en octets"""
    if size < 1024:
        return f"{size:.1f} B"
    elif size < 1024 * 1024:
        return f"{size/1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size/(1024*1024):.1f} MB"
    else:
        return f"{size/(1024*1024*1024):.1f} GB"


def format_time(seconds: float) -> str:
    """Formate le temps en secondes"""
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
    """Crée une barre de progression ASCII"""
    progress = min(100, max(0, progress))
    filled = math.ceil(width * progress / 100)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}] {progress:.1f}%"


async def update_progress(
    current,
    total,
    client,
    status_msg,
    completed_msg,
    filename,
    sent_files,
    total_files,
):
    """Met à jour la progression de l'envoi de fichier"""
    try:
        progress = (current / total) * 100 if total > 0 else 0
        if not hasattr(update_progress, "last_progress"):
            update_progress.last_progress = 0

        if abs(progress - update_progress.last_progress) >= 5 or current == total:
            update_progress.last_progress = progress

            progress_text = (
                f"📦 Envoi des fichiers ({sent_files}/{total_files})\n"
                f"📄 En cours: {filename[:50]}{'...' if len(filename) > 50 else ''}\n"
                f"{create_progress_bar(progress)}"
                f"\n{format_size(current)}/{format_size(total)}"
            )

            try:
                await status_msg.edit_text(f"{completed_msg}\n\n{progress_text}")
            except Exception as e:
                logger.error(f"Erreur édition message progression: {e}")

    except Exception as e:
        logger.error(f"Erreur mise à jour progression: {e}")


# Filtres personnalisés
def group_or_admin_filter():
    """Filtre pour n'autoriser que les groupes autorisés ou les admins en privé"""
    async def func(_, client, message: Message):
        # Autoriser tous les messages dans les groupes autorisés
        if message.chat.id in deps.config.GROUPS:
            return True

        # Autoriser les admins en privé
        if message.chat.type == "private":
            return message.from_user.id in deps.config.ADMIN_IDS

        return False

    return filters.create(func)


def admin_only_filter():
    """Filtre pour les commandes admin uniquement"""
    async def func(_, client, message: Message):
        return message.from_user.id in deps.config.ADMIN_IDS

    return filters.create(func)


# Création des instances de filtres
group_or_admin = group_or_admin_filter()
admin_only = admin_only_filter()


@Client.on_callback_query(filters.regex(r"^refresh_([a-zA-Z0-9]+)$"))
async def handle_refresh(client: Client, callback_query: CallbackQuery):
    """Gère l'actualisation manuelle de la progression"""
    try:
        download_id = callback_query.matches[0].group(1)
        if download_id not in active_downloads:
            await callback_query.answer("❌ Ce téléchargement n'existe plus", show_alert=True)
            return

        await callback_query.answer("🔄 Actualisation en cours...")
        await send_progress_update(
            client, callback_query.from_user.id, download_id, callback_query.message
        )

    except Exception as e:
        logger.error(f"Erreur lors de l'actualisation: {e}")
        await callback_query.answer("❌ Échec de l'actualisation", show_alert=True)


@Client.on_callback_query(filters.regex(r"^open_([a-zA-Z0-9]+)$"))
async def handle_open_download(client: Client, callback_query: CallbackQuery):
    """Ouvre le dossier de téléchargement"""
    try:
        download_id = callback_query.matches[0].group(1)
        if download_id not in active_downloads:
            await callback_query.answer("❌ Téléchargement introuvable", show_alert=True)
            return

        download_info = active_downloads[download_id]
        dl_path = Path(download_info["dl_path"])

        if not dl_path.exists():
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
    """Annule un téléchargement en cours"""
    try:
        download_id = callback_query.matches[0].group(1)
        if download_id not in active_downloads:
            await callback_query.answer("❌ Téléchargement introuvable", show_alert=True)
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
                    if dl_path.exists():
                        if not any(dl_path.iterdir()):
                            dl_path.rmdir()
                except Exception as e:
                    logger.error(f"Erreur nettoyage dossier: {e}")

                if "temp_path" in dl_info:
                    try:
                        temp_path = Path(dl_info["temp_path"])
                        if temp_path.exists():
                            temp_path.unlink()
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


@Client.on_message(filters.command("start", prefixes=["/", "!"]) & filters.private)
async def start_command(client: Client, message: Message):
    """Point d'entrée principal pour les utilisateurs"""
    try:
        await deps.startup()
        bot_info = await client.get_me()
        user = message.from_user

        if not user:
            await message.reply_text("🔴 Erreur : Impossible d'identifier votre compte.")
            return

        # Pour les non-admins, générer un lien d'invitation au lieu d'afficher les IDs
        if user.id not in deps.config.ADMIN_IDS:
            if not deps.config.GROUPS:
                await message.reply_text(
                    "🚫 Ce bot ne peut être utilisé qu'en groupe. Aucun groupe autorisé n'est configuré.",
                    parse_mode=ParseMode.HTML
                )
                return

            group_id = deps.config.GROUPS[0]
            try:
                # Créer un lien d'invitation temporaire
                invite_link = await client.create_chat_invite_link(
                    chat_id=group_id,
                    name=f"Invite_{user.id}",
                    member_limit=1,
                    creates_join_request=False
                )

                # Stocker le lien
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
                        [InlineKeyboardButton(
                            "🎯 Rejoindre le groupe",
                            url=invite_link.invite_link
                        )]
                    ])
                )
                return

            except Exception as e:
                logger.error(f"Erreur création lien: {e}")
                # Fallback vers le lien public
                group_link = f"https://t.me/c/{str(group_id)[4:] if str(group_id).startswith('-100') else group_id}"
                await message.reply_text(
                    "👋 <b>Bienvenue !</b>\n\n"
                    "Rejoignez notre groupe pour utiliser le bot:\n"
                    f"👉 {group_link}",
                    parse_mode=ParseMode.HTML
                )
                return

        # Pour les admins, afficher le menu normal
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
        await message.reply_text(
            "⚠️ <b>Service temporairement indisponible</b>",
            parse_mode=ParseMode.HTML,
        )

def get_main_keyboard(is_new_user: bool = False) -> InlineKeyboardMarkup:
    """Retourne le clavier principal"""
    buttons = [
        [
            InlineKeyboardButton("📖 Guide", callback_data="help"),
            InlineKeyboardButton("⚙️ Préférences", callback_data="settings"),
        ],
    ]

    if is_new_user:
        buttons.append([InlineKeyboardButton("🎬 Tutoriel", callback_data="tutorial")])

    buttons.extend(
        [
            [InlineKeyboardButton("📌 À propos", callback_data="about")],
            [InlineKeyboardButton("🔄 Mises à jour", callback_data="update")],
        ]
    )

    return InlineKeyboardMarkup(buttons)

@Client.on_message(filters.new_chat_members & filters.group)
async def handle_new_members(client: Client, message: Message):
    """Gère les nouveaux membres dans le groupe"""
    try:
        for user in message.new_chat_members:
            if user.is_bot:
                continue

            # Gestion des liens d'invitation
            if hasattr(message, 'invite_link') and message.invite_link:
                invite_link = message.invite_link.invite_link
                if hasattr(deps, "active_invite_links") and invite_link in deps.active_invite_links:
                    try:
                        # Révoquer le lien après utilisation
                        await client.revoke_chat_invite_link(
                            chat_id=message.chat.id,
                            invite_link=invite_link
                        )
                        del deps.active_invite_links[invite_link]
                    except Exception as e:
                        logger.error(f"Erreur révocation lien: {e}")

            # Enregistrement de l'utilisateur
            user_data = UserCreate(
                uid=user.id,
                uname=user.username,
                first=user.first_name or "",
                last=user.last_name or "",
                lang_code=user.language_code or "fr",
                sub=SubTier.FREE,
                role=Role.USER,
            )

            if not await deps.user_manager.get_user(user.id):
                await deps.user_manager.create_user(user_data)

            # Message de bienvenue
            await message.reply_text(
                f"👋 Bienvenue {user.mention()} dans le groupe !",
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logger.error(f"Erreur gestion nouveaux membres: {e}")


@Client.on_message(filters.text & group_or_admin)
async def handle_download_requests(client: Client, message: Message):
    """Gère les requêtes de téléchargement (uniquement dans les groupes autorisés ou par les admins)"""
    user = message.from_user
    magnet_link = extract_magnet_link(message.text)
    direct_link = extract_direct_link(message.text) if not magnet_link else None

    if not magnet_link and not direct_link:
        return

    if not await validate_user_quota(user.id):
        await message.reply_text(
            format_message(Messages.QUOTA_EXCEEDED),
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.id,
        )
        return

    try:
        dl_path = Path(f"downloads/{user.id}_{int(time.time())}")
        dl_path.mkdir(parents=True, exist_ok=True)

        if magnet_link:
            source = magnet_link
            display_name = magnet_link[:50] + ("..." if len(magnet_link) > 50 else "")
            start_msg = format_message(Messages.MAGNET_DETECTED)
        else:
            source = direct_link
            display_name = Path(urlparse(direct_link).path).name[:50]
            start_msg = format_message(Messages.DIRECT_LINK_DETECTED)

        download_id = await deps.torrent_client.add(source, str(dl_path))
        if not download_id:
            raise ValueError("Échec de l'initialisation")

        active_downloads[download_id] = {
            "user_id": user.id,
            "type": "magnet" if magnet_link else "direct",
            "dl_path": str(dl_path),
            "start_time": asyncio.get_event_loop().time(),
            "name": display_name,
            "source": source,
        }

        response = await message.reply_text(
            start_msg,
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.id,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Actualiser", callback_data=f"refresh_{download_id}"
                        ),
                        InlineKeyboardButton(
                            "📂 Ouvrir", callback_data=f"open_{download_id}"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Annuler", callback_data=f"cancel_{download_id}"
                        )
                    ],
                ]
            ),
        )

        asyncio.create_task(
            send_progress_update(client, user.id, download_id, response)
        )

    except Exception as e:
        logger.error(f"Erreur téléchargement: {str(e)}", exc_info=True)
        await message.reply_text(
            format_message(
                Messages.DOWNLOAD_ERROR,
                download_type="Magnet" if magnet_link else "Lien direct",
                error=str(e),
            ),
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.id,
        )


@Client.on_message(filters.document & group_or_admin)
async def handle_torrent_files(client: Client, message: Message):
    """Gère les fichiers torrent (uniquement dans les groupes autorisés ou par les admins)"""
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
        temp_path = Path(f"temp/{user.id}_{message.document.file_name}")
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        await message.download(file_name=str(temp_path))

        dl_path = Path(f"downloads/{user.id}")
        dl_path.mkdir(parents=True, exist_ok=True)

        download_id = await deps.torrent_client.add(str(temp_path), str(dl_path))
        if not download_id:
            raise ValueError("Échec de l'ajout")

        active_downloads[download_id] = {
            "user_id": user.id,
            "type": "torrent",
            "dl_path": str(dl_path),
            "start_time": asyncio.get_event_loop().time(),
            "name": message.document.file_name,
            "temp_path": str(temp_path),
        }

        response = await message.reply_text(
            format_message(Messages.TORRENT_RECEIVED),
            parse_mode=ParseMode.HTML,
            reply_to_message_id=message.id,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔄 Actualiser", callback_data=f"refresh_{download_id}"
                        ),
                        InlineKeyboardButton(
                            "📂 Ouvrir", callback_data=f"open_{download_id}"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Annuler", callback_data=f"cancel_{download_id}"
                        )
                    ],
                ]
            ),
        )

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
            temp_path.unlink()


async def send_progress_update(
    client: Client, user_id: int, download_id: str, msg: Message
):
    """Envoie des mises à jour de progression"""
    start_time = asyncio.get_event_loop().time()
    last_progress = 0

    while download_id in active_downloads:
        try:
            stats = await deps.torrent_client.stats(download_id)
            if not stats:
                break

            current_time = asyncio.get_event_loop().time()
            active_downloads[download_id]["duration"] = current_time - start_time

            progress_data = {
                "name": active_downloads[download_id]["name"],
                "progress_bar": create_progress_bar(stats.progress),
                "speed": format_speed(stats.dl_rate * 1024),
                "peers": stats.peers,
                "eta": format_time(stats.eta),
                "done": format_size(stats.done * 1024 * 1024),
                "total": format_size(stats.wanted * 1024 * 1024),
                "file_progress": "",
            }

            if hasattr(stats, "current_file") and stats.current_file:
                progress_data["file_progress"] = (
                    f"\n📄 Fichier actuel: {stats.current_file['name']}\n"
                    f"{create_progress_bar(stats.current_file['progress'])}"
                    f"\n{format_size(stats.current_file['downloaded'])}/{format_size(stats.current_file['size'])}"
                )

            await msg.edit_text(
                format_message(Messages.PROGRESS_TEMPLATE, **progress_data),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🔄 Actualiser", callback_data=f"refresh_{download_id}"
                            ),
                            InlineKeyboardButton(
                                "📂 Ouvrir", callback_data=f"open_{download_id}"
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                "❌ Annuler", callback_data=f"cancel_{download_id}"
                            )
                        ],
                    ]
                ),
            )

            if stats.progress >= 99.9:
                await handle_download_complete(client, user_id, download_id, msg)
                break

            await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"Erreur mise à jour: {str(e)}")
            break


async def handle_download_complete(
    client: Client, user_id: int, download_id: str, msg: Message
):
    """Gère la complétion du téléchargement"""
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

        await msg.edit_text(
            format_message(Messages.COMPLETED_TEMPLATE, **completed_data),
            parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 Ouvrir le dossier", callback_data=f"open_{download_id}")]
            ])
        )

        dl_path = Path(download_info['dl_path'])
        if not dl_path.exists() or not dl_path.is_dir():
            await msg.edit_text(
                f"{format_message(Messages.COMPLETED_TEMPLATE, **completed_data)}\n\n"
                "❌ Erreur: Dossier introuvable"
            )
            return

        files = [f for f in dl_path.rglob('*') if f.is_file() and not f.name.startswith('.')]
        total_files = len(files)
        sent_files = 0

        if not files:
            await msg.edit_text(
                f"{format_message(Messages.COMPLETED_TEMPLATE, **completed_data)}\n\n"
                "⚠️ Aucun fichier trouvé dans le dossier"
            )
            return

        for file_path in files:
            try:
                file_size = file_path.stat().st_size
                if file_size > 2000 * 1024 * 1024:  # 2GB
                    continue

                await client.send_document(
                    chat_id=user_id,
                    document=str(file_path),
                    caption=f"📁 {file_path.name}",
                    disable_notification=True,
                    progress=update_progress,
                    progress_args=(
                        client,
                        msg,
                        format_message(Messages.COMPLETED_TEMPLATE, **completed_data),
                        file_path.name,
                        sent_files,
                        total_files
                    )
                )
                sent_files += 1
                file_path.unlink()
            except Exception as e:
                logger.error(f"Erreur envoi fichier {file_path}: {e}")

        additional_info = ""
        if sent_files < total_files:
            additional_info = f"⚠️ {total_files-sent_files} fichiers non envoyés (trop volumineux ou erreur)"

        await msg.edit_text(
            format_message(
                Messages.TRANSFER_COMPLETE,
                name=download_info['name'],
                sent=sent_files,
                total=total_files,
                duration=format_time(duration),
                additional_info=additional_info
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 Ouvrir le dossier", callback_data=f"open_{download_id}")]
            ])
        )

    except Exception as e:
        logger.error(f"Erreur complétion: {str(e)}")
        await msg.edit_text(
            f"❌ <b>Erreur lors du transfert</b>\n\n{str(e)}",
            parse_mode=ParseMode.HTML
        )
    finally:
        try:
            await deps.torrent_client.remove(download_id, delete_data=True)
            dl_path = Path(download_info.get('dl_path', ''))
            if dl_path.exists():
                for file in dl_path.glob('*'):
                    try:
                        file.unlink()
                    except:
                        pass
                try:
                    dl_path.rmdir()
                except:
                    pass

            if 'temp_path' in download_info:
                temp_path = Path(download_info['temp_path'])
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except:
                        pass

            if download_id in active_downloads:
                del active_downloads[download_id]
        except Exception as e:
            logger.error(f"Erreur nettoyage: {str(e)}")


@Client.on_message(filters.command("cleanup", prefixes=["/", "!"]) & admin_only)
async def cleanup_command(client: Client, message: Message):
    """Nettoyage manuel des téléchargements bloqués (admin seulement)"""
    await cleanup_stalled_downloads(client)
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


# async def main():
#     """Point d'entrée principal"""
#     await deps.initialize()
#     await deps.client.start()
#     logger.info("Bot démarré avec succès")

#     # Planifie le nettoyage périodique
#     async def periodic_cleanup():
#         while True:
#             await asyncio.sleep(3600)  # Toutes les heures
#             await cleanup_stalled_downloads(deps.client)

#     asyncio.create_task(periodic_cleanup())

#     await deps.client.idle()


# if __name__ == "__main__":
#     asyncio.run(main())