from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode
from bot import Dependencies
import logging

from model.user import UserUpdate

deps = Dependencies()
logger = logging.getLogger(__name__)

class BotResponses:
    """Classe centralisant tous les messages du bot"""
    
    @staticmethod
    def main_menu(username: str) -> tuple[str, InlineKeyboardMarkup]:
        """Retourne le message et le clavier du menu principal"""
        message = (
            f"👋 <b>Bienvenue, {username} !</b>\n\n"
            "🔍 Comment puis-je vous aider aujourd'hui ?\n\n"
            "• 📥 Gérer vos téléchargements\n"
            "• ⚙️ Configurer vos préférences\n"
            "• 🔔 Recevoir des notifications"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📖 Guide Complet", callback_data="help"),
             InlineKeyboardButton("❗ Avis Juridique", callback_data="disclaimer")],
            [InlineKeyboardButton("ℹ️ Fonctionnalités", callback_data="about"),
             InlineKeyboardButton("⚙️ Paramètres", callback_data="settings")],
            [InlineKeyboardButton("🔄 Vérifier MAJ", callback_data="update")]
        ])
        
        return message, keyboard

    @staticmethod
    def legal_notice() -> tuple[str, InlineKeyboardMarkup]:
        """Message des mentions légales"""
        message = (
            "<b>📜 Avis Juridique Important</b>\n\n"
            "<b>1. Usage Responsable</b>\n"
            "Ce service est un outil technique neutre. Vous êtes seul responsable "
            "des contenus téléchargés via votre utilisation du bot.\n\n"
            
            "<b>2. Conformité Légale</b>\n"
            "L'utilisation pour du contenu protégé par des droits d'auteur sans "
            "autorisation est strictement interdite et peut entraîner la suspension "
            "immédiate de votre accès.\n\n"
            
            "<b>3. Protection des Données</b>\n"
            "Nous stockons uniquement :\n"
            "- Votre ID Telegram\n"
            "- Prénom et langue\n"
            "Aucune donnée n'est partagée avec des tiers.\n\n"
            
            "<b>4. Support Technique</b>\n"
            "Contactez-nous à : <code>legal@hisocode.com</code>"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Retour", callback_data="back_to_main")]
        ])
        
        return message, keyboard

    @staticmethod
    def about_section() -> tuple[str, InlineKeyboardMarkup]:
        """Section À propos"""
        message = (
            "<b>🌟 À Propos de Notre Service</b>\n\n"
            
            "<b>🚀 Fonctionnalités Clés :</b>\n"
            "• Prise en charge complète des liens magnet\n"
            "• Gestion avancée des fichiers .torrent\n"
            "• Notifications en temps réel\n"
            "• Interface multiplateforme\n\n"
            
            "<b>🔒 Notre Engagement :</b>\n"
            "• Respect strict de la vie privée\n"
            "• Aucune collecte de données inutiles\n"
            "• Technologie chiffrée de bout en bout\n\n"
            
            "<b>📅 Roadmap 2024 :</b>\n"
            "- Intégration cloud\n"
            "- Support multi-langues\n"
            "- API publique\n\n"
            
            "✉️ <i>Questions ? contact@hisocode.com</i>"
        )
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Retour", callback_data="back_to_main")]
        ])
        
        return message, keyboard

@Client.on_callback_query()
async def handle_callback_query(client: Client, callback_query: CallbackQuery):
    """Gestion centralisée des interactions"""
    try:
        await deps.startup()
        data = callback_query.data
        user = callback_query.from_user
        user_data = await deps.user_manager.get_user(user.id)
        if not user_data:
            await callback_query.answer("⚠️ Vous devez d'abord vous inscrire, utiliser /start.", show_alert=True)
            return
        
        # Réponse immédiate à la requête
        await callback_query.answer()
        
        if data == "help":
            await callback_query.message.edit_text(
                "<b>📚 Centre d'Aide</b>\n\n"
                "1. Envoyer un lien magnet ou fichier .torrent\n"
                "2. Le bot traitera votre demande\n"
                "3. Recevez les fichiers directement\n\n"
                "🛠️ <i>Fonctionnalités avancées disponibles dans les paramètres</i>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Retour", callback_data="back_to_main"), InlineKeyboardButton("⚙️ Parametre", callback_data="settings")]
                ]),
                parse_mode=ParseMode.HTML
            )
            
        elif data == "disclaimer":
            message, keyboard = BotResponses.legal_notice()
            await callback_query.message.edit_text(
                message,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
            
        elif data == "about":
            message, keyboard = BotResponses.about_section()
            await callback_query.message.edit_text(
                message,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
            
        elif data == "settings":
            
            settings = user_data.settings
            await callback_query.message.edit_text(
                f"⚙️ <b>Paramètres de {user.mention}</b>\n\n"
                f"🆔 {user.id} | 📅 Inscrit le {user_data.created.strftime('%d/%m/%Y')}\n\n"
                f"💎 Abonnement : <b>{user_data.sub.value.upper()}</b>\n\n"
                "🔧 <u>Préférences</u>\n"
                f"• {'🌙' if settings.dark else '☀️'} Thème : {'Sombre' if settings.dark else 'Clair'}\n"
                f"• {'🔔' if settings.notifs else '🔕'} Notifications : {'Activées' if settings.notifs else 'Désactivées'}\n"
                f"• 📁 Dossier : <code>{settings.dl_path}</code> Par defaut\n"
                f"• 🗑️ Suppression auto : {'Activée' if settings.auto_del else 'Désactivée'}\n"
                f"• 🌀 DLs parallèles : <b>{settings.max_parallel}/{user_data.quotas.max_dls}</b>\n\n"
                
                "📊 <u>Statistiques</u>\n"
                f"• ⬇️ Téléchargements : {user_data.stats.dls}\n"
                f"• ⏱️ Dernière activité : {user_data.stats.last_active.strftime('%d/%m/%Y %H:%M')}\n\n"
                
                "🚫 <u>Limites</u>\n"
                f"• 🔢 Maximum DLs simultanés : {user_data.quotas.max_dls}\n\n"
                "<i>Plus d'options bientôt disponibles</i>",
                reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    f"{'🌙 Désactiver' if settings.dark else '☀️ Activer'} thème",
                    callback_data="toggle_dark"
                ),
                InlineKeyboardButton(
                    f"{'🔕 Désactiver' if settings.notifs else '🔔 Activer'} notifs",
                    callback_data="toggle_notifs"
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑️ Suppression auto",
                    callback_data="toggle_autodel"
                ),
                InlineKeyboardButton(
                    "🌀 Modifier parallèles",
                    callback_data="toggle_parallel"
                )
            ],
            [
                InlineKeyboardButton("📁 Changer dossier", callback_data="change_path")
            ],
            [InlineKeyboardButton("🔙 Retour", callback_data="back_to_main")]
        ]),
                parse_mode=ParseMode.HTML
            )
        
            
        elif data == "update":
            await callback_query.message.edit_text(
                "<b>🔄 Mises à Jour</b>\n\n"
                "Version actuelle : <code>v2.1.4</code>\n"
                "Dernière MAJ : 15/06/2024\n\n"
                "✅ Vous utilisez la derniere version de notre service !",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Retour", callback_data="settings")]
                ]),
                parse_mode=ParseMode.HTML
            )
            
        elif data == "back_to_main":
            message, keyboard = BotResponses.main_menu(user.mention)
            await callback_query.message.edit_text(
                message,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
        
        elif data.startswith("toggle_"):
            setting = data.split("_")[1]
            
            if setting == "dark":
                new_value = not user_data.settings.dark
                await deps.user_manager.update_user(user.id, UserUpdate(settings={"dark": new_value}))
                await callback_query.message.edit_text(
                    f"🌙 <b>Thème {'Sombre' if new_value else 'Clair'} activé</b>",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Retour", callback_data="settings")]
                    ]),
                    parse_mode=ParseMode.HTML
                )
            
            elif setting == "notifs":
                new_value = not user_data.settings.notifs
                await deps.user_manager.update_user(user.id, UserUpdate(settings={"notifs": new_value}))
                await callback_query.message.edit_text(
                    f"🔔 <b>Notifications {'activées' if new_value else 'désactivées'}</b>",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Retour", callback_data="settings")]
                    ]),
                    parse_mode=ParseMode.HTML
                )
            elif setting == "autodel":
                new_value = not user_data.settings.auto_del
                await deps.user_manager.update_user(user.id, UserUpdate(settings={"auto_del": new_value}))
                await callback_query.message.edit_text(
                    f"🗑️ <b>Suppression automatique {'activée' if new_value else 'désactivée'}</b>",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Retour", callback_data="settings")]
                    ]),
                    parse_mode=ParseMode.HTML
                )
            elif setting == "parallel":
                await callback_query.message.edit_text(
                    "🔄 <b>Modifier le nombre de téléchargements parallèles</b>\n\n"
                    "Veuillez entrer le nouveau nombre de téléchargements parallèles :",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("1", callback_data="set_parallel_1"), InlineKeyboardButton("2", callback_data="set_parallel_2"), InlineKeyboardButton("3", callback_data="set_parallel_3")],
                        [InlineKeyboardButton("Mettre a jours le Plan", callback_data="updateplan")],
                        [InlineKeyboardButton("🔙 Retour", callback_data="settings")]
                    ]),
                    parse_mode=ParseMode.HTML
                )
        
        elif data.startswith("set_parallel_"):
            new_value = int(data.split("_")[2])
            await deps.user_manager.update_user(user.id, UserUpdate(settings={"max_parallel": new_value}))
            await callback_query.message.edit_text(
                f"🌀 <b>Nombre de téléchargements parallèles mis à jour à {new_value}</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Retour", callback_data="settings")],
                ]),
                parse_mode=ParseMode.HTML
            )
            
    except Exception as e:
        logger.error(f"Callback error: {str(e)}", exc_info=True)
        await callback_query.message.edit_text(
            "⚠️ <b>Erreur Temporaire</b>\n\n"
            "Notre équipe a été notifiée.\n"
            "Veuillez réessayer plus tard.",
            parse_mode=ParseMode.HTML
        )