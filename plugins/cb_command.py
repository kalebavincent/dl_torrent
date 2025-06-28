import os
import time
from urllib.parse import urlparse
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode
from bot import Dependencies
from model.user import Role, SubTier, UserCreate
from typing import Any, Optional, Dict
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
ALLOWED_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.mp3', '.zip', '.rar', '.pdf', '.docx', '.xlsx', '.pptx', '.txt', ".cbz", ".cb7", ".cbr", ".cbt", ".cb7z", ".cb7z", ".torrent"}
DIRECT_LINK_REGEX = r"https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"

def is_valid_direct_link(url: str) -> bool:
    """Valide que le lien pointe vers un fichier téléchargeable"""
    try:
        parsed = urlparse(url)
        if not parsed.scheme in ('http', 'https'):
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

    active_count = len([d for d in active_downloads.values() if d['user_id'] == user_id])
    return active_count < user.settings.max_parallel

async def cleanup_stalled_downloads(client: Client):
    """Nettoie les téléchargements bloqués"""
    for dl_id, dl_info in list(active_downloads.items()):
        if 'start_time' in dl_info:
            duration = asyncio.get_event_loop().time() - dl_info['start_time']
            if duration > 7200:  # 2 heures
                logger.warning(f"Nettoyage du téléchargement bloqué {dl_id}")
                try:
                    await client.send_message(
                        chat_id=dl_info['user_id'],
                        text=f"🛑 <b>Téléchargement annulé</b>\n\nLe téléchargement {dl_info['name']} a été bloqué trop longtemps.",
                        parse_mode=ParseMode.HTML
                    )
                    dl_path = Path(dl_info.get('dl_path', ''))
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
                except Exception as e:
                    logger.error(f"Erreur nettoyage bloqué {dl_id}: {e}")
                finally:
                    if dl_id in active_downloads:
                        del active_downloads[dl_id]

async def start_background_tasks(client: Client):
    """Lance les tâches d'arrière-plan"""
    while True:
        await asyncio.sleep(3600)  # 1 heure
        try:
            await cleanup_stalled_downloads(client)
        except Exception as e:
            logger.error(f"Erreur tâche arrière-plan: {e}")

def format_speed(speed: float) -> str:
    """Formate la vitesse de téléchargement"""
    if speed < 1024:
        return f"{speed:.1f} B/s"
    elif speed < 1024*1024:
        return f"{speed/1024:.1f} KB/s"
    else:
        return f"{speed/(1024*1024):.1f} MB/s"

def format_size(size: float) -> str:
    """Formate la taille en octets"""
    if size < 1024:
        return f"{size:.1f} B"
    elif size < 1024*1024:
        return f"{size/1024:.1f} KB"
    elif size < 1024*1024*1024:
        return f"{size/(1024*1024):.1f} MB"
    else:
        return f"{size/(1024*1024*1024):.1f} GB"

def format_time(seconds: float) -> str:
    """Formate le temps en secondes"""
    if seconds == float('inf'):
        return "∞"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"

def create_progress_bar(progress: float, width: int = 20) -> str:
    """Crée une barre de progression ASCII"""
    progress = min(100, max(0, progress))
    filled = math.ceil(width * progress / 100)
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}] {progress:.1f}%"

async def send_progress_update(client: Client, user_id: int, download_id: str):
    """Envoie des mises à jour de progression périodiques"""
    start_time = asyncio.get_event_loop().time()
    last_progress = 0
    last_update = start_time

    while download_id in active_downloads:
        try:
            stats = await deps.torrent_client.stats(download_id)
            if not stats:
                logger.error(f"Aucune statistique pour {download_id}")
                break

            current_time = asyncio.get_event_loop().time()
            current_duration = current_time - start_time
            active_downloads[download_id]['duration'] = current_duration

            is_completed = (stats.progress >= 99.9 or
                          (stats.speed <= 0.01 and stats.peers == 0 and stats.progress > 95) or
                          (current_duration > 1800 and abs(stats.progress - last_progress) < 0.1))

            # Barre de progression globale
            global_progress = create_progress_bar(stats.progress)

            # Barre de progression pour le fichier actuel (si disponible)
            file_progress = ""
            if hasattr(stats, 'current_file') and stats.current_file:
                file_progress = (
                    f"\n📄 Fichier actuel: {stats.current_file['name']}\n"
                    f"{create_progress_bar(stats.current_file['progress'])}"
                    f"\n{format_size(stats.current_file['downloaded'])} / {format_size(stats.current_file['size'])}"
                )

            progress_msg = (
                f"📊 <b>Progression du téléchargement</b>\n\n"
                f"🏷️ <code>{active_downloads[download_id]['name']}</code>\n"
                f"📈 {global_progress}\n"
                f"⚡ Vitesse: {format_speed(stats.dl_rate * 1024)}\n"
                f"👥 Pairs: {stats.peers}\n"
                f"⏳ Temps restant: {format_time(stats.eta)}\n"
                f"📦 Taille: {format_size(stats.done * 1024 * 1024)} / {format_size(stats.wanted * 1024 * 1024)}"
                f"{file_progress}"
            )

            if (abs(stats.progress - last_progress) > 1 or (current_time - last_update) > 30):
                if 'msg_id' in active_downloads[download_id]:
                    try:
                        await client.edit_message_text(
                            chat_id=user_id,
                            message_id=active_downloads[download_id]['msg_id'],
                            text=progress_msg,
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("📂 Ouvrir le dossier", callback_data=f"open_{download_id}")]
                            ])
                        )
                        last_update = current_time
                    except Exception as e:
                        if "MESSAGE_NOT_MODIFIED" not in str(e):
                            logger.error(f"Erreur modification message: {e}")
                            break
                else:
                    msg = await client.send_message(
                        chat_id=user_id,
                        text=progress_msg,
                        parse_mode=ParseMode.HTML
                    )
                    active_downloads[download_id]['msg_id'] = msg.id
                    last_update = current_time

            if is_completed or stats.progress >= 100.0:
                logger.info(f"Téléchargement {download_id} marqué comme complet (Prog: {stats.progress}%, Speed: {stats.speed}, Peers: {stats.peers})")
                await handle_download_complete(client, user_id, download_id)
                break

            if (abs(stats.progress - last_progress) < 0.1 and
                current_duration > 3600 and
                stats.progress < 99.9):  # 1 heure sans progression
                logger.warning(f"Téléchargement {download_id} bloqué à {stats.progress}% depuis 1h")
                await client.send_message(
                    chat_id=user_id,
                    text=f"⚠️ <b>Téléchargement bloqué</b>\n\nLe téléchargement est bloqué à {stats.progress}% depuis trop longtemps.",
                    parse_mode=ParseMode.HTML
                )
                break

            last_progress = stats.progress
            await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"Progress update error: {e}", exc_info=True)
            break

async def handle_download_complete(client: Client, user_id: int, download_id: str):
    """Gère la complétion d'un téléchargement"""
    if download_id not in active_downloads:
        logger.warning(f"ID de téléchargement inconnu: {download_id}")
        return

    download_info = active_downloads[download_id]
    try:
        # Récupérer les statistiques finales
        stats = await deps.torrent_client.stats(download_id)
        if not stats:
            logger.error(f"Aucune statistique finale pour {download_id}")
            await client.send_message(
                chat_id=user_id,
                text=f"❌ Impossible de récupérer les stats du torrent {download_id}"
            )
            return

        duration = download_info.get('duration', 0)
        time_str = format_time(duration)

        completed_msg = (
            f"✅ <b>Téléchargement terminé !</b>\n\n"
            f"🏷️ <code>{download_info['name']}</code>\n"
            f"📦 Taille totale: {format_size(stats.wanted * 1024 * 1024)}\n"
            f"⏱️ Durée: {time_str}\n"
            f"📁 Préparation des fichiers...\n\n"
            f"⚡ Vitesse moyenne: {format_speed((stats.wanted * 1024 * 1024)/max(1, duration))}"
        )

        # Envoyer le message de complétion
        status_msg = await client.send_message(
            chat_id=user_id,
            text=completed_msg,
            parse_mode=ParseMode.HTML
        )

        # Envoyer tous les fichiers du dossier de téléchargement avec progression
        dl_path = Path(download_info['dl_path'])
        logger.info(f"Tentative d'envoi depuis: {dl_path} (existe: {dl_path.exists()})")

        if not dl_path.exists():
            await status_msg.edit_text(f"{completed_msg}\n\n❌ Erreur: Dossier introuvable")
            logger.error(f"Dossier introuvable: {dl_path}")
            return

        if not dl_path.is_dir():
            await status_msg.edit_text(f"{completed_msg}\n\n❌ Erreur: Le chemin n'est pas un dossier")
            logger.error(f"Le chemin n'est pas un dossier: {dl_path}")
            return

        try:
            files = list(dl_path.rglob('*'))
            files = [f for f in files if f.is_file() and not f.name.startswith('.')]
            total_files = len(files)
            sent_files = 0

            logger.info(f"Fichiers à envoyer ({total_files}): {[f.name for f in files]}")

            if not files:
                await status_msg.edit_text(f"{completed_msg}\n\n⚠️ Aucun fichier trouvé dans le dossier")
                logger.warning("Aucun fichier trouvé dans le dossier de téléchargement")
                return

            for file_path in files:
                try:
                    file_size = file_path.stat().st_size
                    file_name = file_path.name[:50] + ('...' if len(file_path.name) > 50 else '')

                    progress_text = (
                        f"📦 Envoi des fichiers ({sent_files}/{total_files})\n"
                        f"📄 En cours: {file_name}\n"
                        f"{create_progress_bar(0)}"
                    )
                    await status_msg.edit_text(
                        f"{completed_msg}\n\n{progress_text}"
                    )

                    # Vérifier la taille et les permissions
                    if not os.access(file_path, os.R_OK):
                        logger.error(f"Permission refusée pour {file_path}")
                        await client.send_message(
                            chat_id=user_id,
                            text=f"⚠️ Permission refusée pour: {file_path.name}"
                        )
                        continue

                    if file_size > 2000 * 1024 * 1024:  # 2GB
                        await client.send_message(
                            chat_id=user_id,
                            text=f"⚠️ Fichier trop volumineux pour Telegram: {file_path.name} ({file_size/1024/1024:.1f} MB)"
                        )
                    else:
                        try:
                            await client.send_document(
                                chat_id=user_id,
                                document=str(file_path),
                                caption=f"📁 {file_path.name}",
                                disable_notification=True,
                                progress=update_progress,
                                progress_args=(
                                    client,
                                    status_msg,
                                    completed_msg,
                                    file_path.name,
                                    sent_files,
                                    total_files
                                )
                            )
                            sent_files += 1
                        except Exception as send_error:
                            logger.error(f"Échec envoi {file_path}: {send_error}", exc_info=True)
                            await client.send_message(
                                chat_id=user_id,
                                text=f"⚠️ Échec envoi fichier: {file_path.name} ({str(send_error)})"
                            )
                            continue

                    # Supprimer le fichier après envoi
                    try:
                        file_path.unlink()
                        logger.info(f"Fichier supprimé: {file_path}")
                    except Exception as e:
                        logger.error(f"Échec suppression {file_path}: {e}")

                except Exception as e:
                    logger.error(f"Erreur traitement fichier {file_path}: {e}", exc_info=True)
                    continue

            # Message final
            final_msg = (
                f"✅ <b>Transfert terminé !</b>\n\n"
                f"🏷️ <code>{download_info['name']}</code>\n"
                f"📦 Fichiers envoyés: {sent_files}/{total_files}\n"
            )
            if sent_files < total_files:
                final_msg += f"⚠️ {total_files - sent_files} fichiers non envoyés (trop volumineux ou erreur)"

            await status_msg.edit_text(final_msg)
            logger.info(f"Transfert terminé pour {download_id}. Fichiers envoyés: {sent_files}/{total_files}")

        except Exception as e:
            logger.error(f"Erreur lors de l'envoi des fichiers: {e}", exc_info=True)
            await status_msg.edit_text(
                f"{completed_msg}\n\n❌ Erreur lors de l'envoi des fichiers: {str(e)}"
            )

    except Exception as e:
        logger.error(f"Erreur complétion: {e}", exc_info=True)
        await client.send_message(
            chat_id=user_id,
            text=f"❌ Erreur lors du transfert: {str(e)}",
            parse_mode=ParseMode.HTML
        )
    finally:
        # Nettoyage final
        try:
            await deps.torrent_client.remove(download_id, delete_data=True)

            # Suppression du dossier de téléchargement
            dl_path = Path(download_info['dl_path'])
            if dl_path.exists():
                # Supprimer tous les fichiers restants
                for file in dl_path.glob('*'):
                    try:
                        if file.is_file():
                            file.unlink()
                    except Exception as e:
                        logger.error(f"Échec suppression fichier {file}: {e}")
                # Supprimer le dossier
                try:
                    dl_path.rmdir()
                    logger.info(f"Dossier supprimé: {dl_path}")
                except Exception as e:
                    logger.error(f"Échec suppression dossier {dl_path}: {e}")

            # Supprimer le fichier temporaire torrent
            if 'temp_path' in download_info:
                temp_path = Path(download_info['temp_path'])
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                        logger.info(f"Fichier temporaire supprimé: {temp_path}")
                    except Exception as e:
                        logger.error(f"Échec suppression fichier temp {temp_path}: {e}")

            # Retirer le téléchargement de la liste active
            if download_id in active_downloads:
                del active_downloads[download_id]
                logger.info(f"Téléchargement retiré de active_downloads: {download_id}")

        except Exception as e:
            logger.error(f"Erreur nettoyage final: {e}", exc_info=True)


async def update_progress(current, total, client, status_msg, completed_msg, filename, sent_files, total_files):
    try:
        # Calculer le pourcentage actuel
        progress = (current / total) * 100 if total > 0 else 0

        if not hasattr(update_progress, 'last_progress'):
            update_progress.last_progress = 0

        if abs(progress - update_progress.last_progress) >= 5 or current == total:
            update_progress.last_progress = progress

            progress_text = (
                f"📦 Envoi des fichiers ({sent_files}/{total_files})\n"
                f"📄 En cours: {filename[:50]}{'...' if len(filename) > 50 else ''}\n"
                f"{create_progress_bar(progress)}"
                f"\n{format_size(current)} / {format_size(total)}"
            )

            try:
                await status_msg.edit_text(
                    f"{completed_msg}\n\n{progress_text}"
                )
                logger.debug(f"Progression mise à jour pour {filename}: {progress:.1f}%")
            except Exception as edit_error:
                logger.error(f"Erreur édition message progression: {edit_error}")

    except Exception as e:
        logger.error(f"Erreur mise à jour progression: {e}")

@Client.on_message(filters.command("cleanup", prefixes=["/", "!"]) & filters.private)
async def cleanup_command(client: Client, message: Message):
    """Nettoyage manuel des téléchargements bloqués"""
    user_id = message.from_user.id
    await cleanup_stalled_downloads(client)
    await message.reply_text("✅ Nettoyage des téléchargements bloqués effectué")

def get_main_keyboard(is_new_user: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📖 Guide d'utilisation", callback_data="help"),
         InlineKeyboardButton("❗ Mentions légales", callback_data="disclaimer")],

    ]

    if is_new_user:
        buttons.append([InlineKeyboardButton("🎬 Tutoriel de démarrage", callback_data="tutorial")])

    buttons.extend([
        [InlineKeyboardButton("📌 À propos du service", callback_data="about"),
         InlineKeyboardButton("⚙️ Préférences utilisateur", callback_data="settings")],

        [InlineKeyboardButton("🔄 Vérifier les mises à jour", callback_data="update")]
    ])

    return InlineKeyboardMarkup(buttons)

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

        user_data = UserCreate(
            uid=user.id,
            uname=user.username,
            first=user.first_name or "",
            last=user.last_name or "",
            lang_code=user.language_code or "fr",
            sub=SubTier.FREE,
            role=Role.USER
        )

        existing_user = await deps.user_manager.get_user(user.id)
        print(existing_user)
        logger.info(f"Session start - User ID: {user.id} | Status: {'Registered' if existing_user else 'New'}")

        if not existing_user:
            await deps.user_manager.create_user(user_data)
            welcome_msg = (
                f"✨ <b>Bienvenue sur {bot_info.mention} !</b> ✨\n\n"
                "🛠️ <b><ul>Service professionnel de téléchargement</ul></b>\n"
                "🧲 Prise en charge des liens magnet\n"
                "📥 Gestion des fichiers torrent\n\n"
                "📌 Pour une prise en main rapide, consultez notre "
                "<b>Tutoriel de démarrage</b> ci-dessous."
            )
            await message.reply_text(
                welcome_msg,
                reply_markup=get_main_keyboard(is_new_user=True),
                parse_mode=ParseMode.HTML
            )
        else:
            await message.reply_text(
                f"👋 <b>Heureux de vous revoir, {user.mention} !</b>\n\n"
                "🔍 Que souhaitez-vous faire aujourd'hui ?\n"
                "📥 Accéder à vos téléchargements\n"
                "🛠️ Modifier vos préférences\n"
                "✨ Consulter les nouveautés",
                reply_markup=get_main_keyboard(),
                parse_mode=ParseMode.HTML
            )

    except Exception as e:
        logger.error(f"Command Error [/start] - {str(e)}", exc_info=True)
        await message.reply_text(
            "⚠️ <b>Service temporairement indisponible</b>\n\n"
            "Nos équipes techniques ont été notifiées.\n"
            "Veuillez réessayer ultérieurement.\n\n"
            "📧 Contact : support@hisocode.com",
            parse_mode=ParseMode.HTML
        )

@Client.on_message(filters.text & filters.private)
async def handle_download_requests(client: Client, message: Message):
    """Gère tous les types de téléchargements (magnet et liens directs)"""
    user_id = message.from_user.id

    # Extraction du lien
    magnet_link = extract_magnet_link(message.text)
    direct_link = extract_direct_link(message.text) if not magnet_link else None

    if not magnet_link and not direct_link:
        return

    # Validation du quota utilisateur
    if not await validate_user_quota(user_id):
        await message.reply_text(
            "⚠️ <b>Limite de téléchargements atteinte</b>\n\n"
            "Vous avez trop de téléchargements en cours.\n"
            "Attendez la fin ou augmentez votre limite dans les paramètres.",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        # Configuration du dossier de destination
        dl_path = Path(f"downloads/{user_id}_{int(time.time())}")
        dl_path.mkdir(parents=True, exist_ok=True)

        # Détermination du type de téléchargement
        if magnet_link:
            source = magnet_link
            download_type = "magnet"
            display_name = magnet_link[:50] + ("..." if len(magnet_link) > 50 else "")
            start_message = "🧲 <b>Lien magnet détecté !</b>"
        else:
            source = direct_link
            download_type = "direct"
            display_name = Path(urlparse(direct_link).path).name[:50]
            start_message = "📥 <b>Lien direct détecté !</b>"

        # Lancement du téléchargement
        download_id = await deps.torrent_client.add(
            source=source,
            path=dl_path,
            paused=False
        )

        if not download_id:
            raise ValueError("Échec de l'initialisation du téléchargement")

        # Enregistrement des métadonnées
        active_downloads[download_id] = {
            'user_id': user_id,
            'type': download_type,
            'dl_path': str(dl_path),
            'start_time': asyncio.get_event_loop().time(),
            'name': display_name,
            'source': source
        }

        # Suivi de progression
        asyncio.create_task(send_progress_update(client, user_id, download_id))

        # Réponse à l'utilisateur
        response = await message.reply_text(
            f"{start_message}\n\n"
            f"Fichier: <code>{display_name}</code>\n"
            "Statut: En cours de préparation...\n\n"
            "Vous recevrez des mises à jour automatiques.",
            parse_mode=ParseMode.HTML
        )

        # Suppression du message après 5 secondes
        await asyncio.sleep(5)
        await response.delete()

    except Exception as e:
        logger.error(f"Download error [{user_id}]: {str(e)}", exc_info=True)

        error_message = (
            "❌ <b>Erreur lors du traitement</b>\n\n"
            f"Type: {'Magnet' if magnet_link else 'Lien direct'}\n"
            f"Erreur: {str(e)}"
        )

        await message.reply_text(
            error_message,
            parse_mode=ParseMode.HTML
        )
@Client.on_message(filters.document & filters.private)
async def handle_torrent_files(client: Client, message: Message):
    """Gère les fichiers torrent"""
    if not is_torrent_file(message.document.file_name):
        return

    user_id = message.from_user.id
    if not await validate_user_quota(user_id):
        await message.reply_text(
            "⚠️ <b>Limite de téléchargements atteinte</b>\n\n"
            "Vous avez trop de téléchargements en cours.\n"
            "Attendez la fin ou augmentez votre limite dans les paramètres.",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        # Télécharger le fichier torrent temporairement
        temp_path = Path(f"temp/{user_id}_{message.document.file_name}")
        temp_path.parent.mkdir(parents=True, exist_ok=True)
        await message.download(file_name=str(temp_path))

        # Démarrer le téléchargement
        dl_path = Path(f"downloads/{user_id}")
        dl_path.mkdir(parents=True, exist_ok=True)

        download_id = await deps.torrent_client.add(
            source=str(temp_path),
            path=dl_path,
            paused=False
        )

        if not download_id:
            raise ValueError("Échec de l'ajout du téléchargement")

        # Enregistrer les informations du téléchargement
        active_downloads[download_id] = {
            'user_id': user_id,
            'type': 'torrent',
            'dl_path': str(dl_path),
            'start_time': asyncio.get_event_loop().time(),
            'name': message.document.file_name,
            'temp_path': str(temp_path)
        }

        # Démarrer le suivi de progression
        asyncio.create_task(send_progress_update(client, user_id, download_id))

        response = await message.reply_text(
            "📥 <b>Fichier torrent reçu !</b>\n\n"
            "Votre téléchargement a bien été pris en charge.\n"
            "Vous recevrez des mises à jour régulières.",
            parse_mode=ParseMode.HTML
        )

        await asyncio.sleep(5)
        await response.delete()

    except Exception as e:
        logger.error(f"Torrent error: {e}")
        await message.reply_text(
            "❌ <b>Erreur lors du traitement</b>\n\n"
            f"Impossible de démarrer le téléchargement: {str(e)}",
            parse_mode=ParseMode.HTML
        )
        # Nettoyer le fichier temporaire en cas d'erreur
        if 'temp_path' in locals() and temp_path.exists():
            temp_path.unlink()

@Client.on_callback_query(filters.regex(r"^open_[\w\d]+$"))
async def handle_open_download(client: Client, callback_query: CallbackQuery):
    """Gère l'ouverture du dossier de téléchargement"""
    download_id = callback_query.data.split("_")[1]

    if download_id not in active_downloads:
        return await callback_query.answer("❌ Téléchargement introuvable", show_alert=True)

    download_info = active_downloads[download_id]
    await callback_query.answer(f"📁 Dossier: {download_info['dl_path']}", show_alert=True)