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

deps = Dependencies()
logger = logging.getLogger(__name__)

# Configuration des expressions régulières
TORRENT_REGEX = r"^.*\.(torrent)$"
MAGNET_REGEX = r"^magnet:\?xt=urn:btih:[a-zA-Z0-9]{32,40}.*$"

# Dictionnaire pour suivre les téléchargements actifs
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
        
    # Vérifier le nombre de téléchargements actifs
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
                    # Supprimer les fichiers associés
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
        await asyncio.sleep(3600)  # Toutes les heures
        try:
            await cleanup_stalled_downloads(client)
        except Exception as e:
            logger.error(f"Erreur tâche arrière-plan: {e}")

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
            
            # Vérifier si le téléchargement est vraiment terminé
            is_completed = (stats.progress >= 99.9 or 
                          (stats.speed <= 0.01 and stats.peers == 0 and stats.progress > 95) or
                          (current_duration > 1800 and abs(stats.progress - last_progress) < 0.1))  # 30 minutes sans progression
            
            progress_msg = (
                f"📊 <b>Progression du téléchargement</b>\n\n"
                f"🏷️ <code>{active_downloads[download_id]['name']}</code>\n"
                f"📈 Progression: {stats.progress:.1f}%\n"
                f"⚡ Vitesse: {stats.speed:.2f} MB/s\n"
                f"👥 Pairs: {stats.peers}\n"
                f"⏳ Temps restant: {'inf' if stats.eta == float('inf') else f'{stats.eta:.0f}s'}\n"
                f"📦 Taille: {stats.done:.1f}/{stats.wanted:.1f} MB"
            )
            
            # Ne mettre à jour que si nécessaire (changement >1% ou 30s écoulées)
            if (abs(stats.progress - last_progress) > 1 or (current_time - last_update) > 30):
                if 'msg_id' in active_downloads[download_id]:
                    try:
                        await client.edit_message_text(
                            chat_id=user_id,
                            message_id=active_downloads[download_id]['msg_id'],
                            text=progress_msg,
                            parse_mode=ParseMode.HTML
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
                
            # Vérification robuste de la complétion
            if is_completed or stats.progress >= 100.0:
                logger.info(f"Téléchargement {download_id} marqué comme complet (Prog: {stats.progress}%, Speed: {stats.speed}, Peers: {stats.peers})")
                await handle_download_complete(client, user_id, download_id)
                break
                
            # Si la progression stagne pendant trop longtemps
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
        return
        
    download_info = active_downloads[download_id]
    try:
        # Récupérer les statistiques finales
        stats = await deps.torrent_client.stats(download_id)
        if not stats:
            logger.error(f"Aucune statistique finale pour {download_id}")
            return
            
        # Formatage du temps de téléchargement
        duration = download_info.get('duration', 0)
        hours, remainder = divmod(duration, 3600)
        minutes, seconds = divmod(remainder, 60)
        time_str = f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
        
        completed_msg = (
            f"✅ <b>Téléchargement terminé !</b>\n\n"
            f"🏷️ <code>{download_info['name']}</code>\n"
            f"📦 Taille totale: {stats.wanted:.1f} MB\n"
            f"⏱️ Durée: {time_str}\n"
            f"📁 Préparation des fichiers...\n\n"
            f"⚡ Vitesse moyenne: {stats.wanted/(duration+0.1):.2f} MB/s"
        )
        
        # Envoyer le message de complétion
        status_msg = await client.send_message(
            chat_id=user_id,
            text=completed_msg,
            parse_mode=ParseMode.HTML
        )
        
        # Envoyer tous les fichiers du dossier de téléchargement avec progression
        dl_path = Path(download_info['dl_path'])
        if dl_path.exists() and dl_path.is_dir():
            files = [f for f in dl_path.glob('*') if f.is_file()]
            total_files = len(files)
            sent_files = 0
            
            for file_path in files:
                try:
                    # Mettre à jour le statut
                    progress_text = (
                        f"📦 Envoi des fichiers ({sent_files}/{total_files})\n"
                        f"📄 En cours: {file_path.name[:50]}..."
                    )
                    await status_msg.edit_text(
                        f"{completed_msg}\n\n{progress_text}"
                    )
                    
                    # Envoyer le fichier avec gestion de la taille
                    if file_path.stat().st_size > 2000 * 1024 * 1024:  # 2GB
                        await client.send_message(
                            chat_id=user_id,
                            text=f"⚠️ Fichier trop volumineux pour Telegram: {file_path.name} ({file_path.stat().st_size/1024/1024:.1f} MB)"
                        )
                    else:
                        await client.send_document(
                            chat_id=user_id,
                            document=str(file_path),
                            caption=f"📁 {file_path.name}",
                            progress=lambda current, total, name: logger.debug(f"Progression {name} {current}/{total}"),
                            progress_args=(file_path.name,)
                        )
                    sent_files += 1
                    
                    # Supprimer le fichier après envoi
                    try:
                        file_path.unlink()
                        logger.info(f"Fichier supprimé: {file_path}")
                    except Exception as e:
                        logger.error(f"Erreur suppression fichier {file_path}: {e}")
                        
                except Exception as e:
                    logger.error(f"Erreur envoi fichier {file_path}: {e}")
                    await client.send_message(
                        chat_id=user_id,
                        text=f"❌ Impossible d'envoyer le fichier {file_path.name}: {str(e)}"
                    )
            
            # Message final
            final_msg = (
                f"✅ <b>Transfert terminé !</b>\n\n"
                f"🏷️ <code>{download_info['name']}</code>\n"
                f"📦 Fichiers envoyés: {sent_files}/{total_files}\n"
            )
            if sent_files < total_files:
                final_msg += f"⚠️ {total_files - sent_files} fichiers non envoyés (trop volumineux ou erreur)"
            
            await status_msg.edit_text(final_msg)
        
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
            dl_path = Path(download_info['dl_path'])
            if dl_path.exists():
                # Supprimer tous les fichiers restants
                for file in dl_path.glob('*'):
                    try:
                        if file.is_file():
                            file.unlink()
                    except:
                        pass
                # Supprimer le dossier
                try:
                    dl_path.rmdir()
                    logger.info(f"Dossier supprimé: {dl_path}")
                except:
                    pass
                
            # Supprimer le fichier temporaire torrent
            if 'temp_path' in download_info:
                temp_path = Path(download_info['temp_path'])
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except:
                        pass
            
            # Retirer le téléchargement de la liste active
            if download_id in active_downloads:
                del active_downloads[download_id]
                
        except Exception as e:
            logger.error(f"Erreur nettoyage final: {e}")


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
async def handle_magnet_links(client: Client, message: Message):
    """Gère les liens magnet"""
    magnet_link = extract_magnet_link(message.text)
    if not magnet_link:
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
        # Démarrer le téléchargement
        dl_path = Path(f"downloads/{user_id}")
        dl_path.mkdir(parents=True, exist_ok=True)
        
        download_id = await deps.torrent_client.add(
            source=magnet_link,
            path=dl_path,
            paused=False
        )
        
        if not download_id:
            raise ValueError("Échec de l'ajout du téléchargement")
            
        # Enregistrer les informations du téléchargement
        active_downloads[download_id] = {
            'user_id': user_id,
            'type': 'magnet',
            'dl_path': str(dl_path),
            'start_time': asyncio.get_event_loop().time(),
            'name': magnet_link[:50] + "..." if len(magnet_link) > 50 else magnet_link
        }
        
        # Démarrer le suivi de progression
        asyncio.create_task(send_progress_update(client, user_id, download_id))
        
        ms = await message.reply_text(
            "🧲 <b>Lien magnet détecté !</b>\n\n"
            "Votre téléchargement a bien été pris en charge.\n"
            "Vous recevrez des mises à jour régulières.",
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(5)
        await ms.delete()
    except Exception as e:
        logger.error(f"Magnet error: {e}")
        await message.reply_text(
            "❌ <b>Erreur lors du traitement</b>\n\n"
            f"Impossible de démarrer le téléchargement: {str(e)}",
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
        
        await message.reply_text(
            "📥 <b>Fichier torrent reçu !</b>\n\n"
            "Votre téléchargement a bien été pris en charge.\n"
            "Vous recevrez des mises à jour régulières.",
            parse_mode=ParseMode.HTML
        )
        
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