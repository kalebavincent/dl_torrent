#!/usr/bin/env python3
import asyncio
from pathlib import Path
from utils.torrent import TorrentClient, TorrentState

class TorrentCLI:
    def __init__(self):
        self.client = None
        self.current_tid = None
        self.running = True

    async def _get_input(self, prompt, default=None, type_cast=str):
        """Helper pour la saisie utilisateur avec valeur par défaut"""
        response = input(f"{prompt} [{default}]: " if default else f"{prompt}: ")
        return type_cast(response or default) if default else type_cast(response)

    async def init_client(self):
        """Initialise le client torrent"""
        print("\nConfiguration du client")
        dl_dir = await self._get_input("Dossier de téléchargement", "./downloads")
        max_up = await self._get_input("Limite upload (kB/s)", 1000, int)
        max_dl = await self._get_input("Limite download (kB/s, -1=illimité)", -1, int)
        
        self.client = TorrentClient(
            dl_dir=dl_dir,
            max_up=max_up,
            max_dl=max_dl
        )
        print("\nClient prêt!")

    async def show_menu(self, title, options):
        """Affiche un menu générique"""
        print(f"\n{'='*50}\n{title.center(50)}\n{'='*50}")
        for i, opt in enumerate(options, 1):
            print(f"{i}. {opt}")
        return await self._get_input("\nVotre choix", len(options), int)

    async def main_loop(self):
        """Boucle principale"""
        while self.running:
            choice = await self.show_menu(
                "TORRENT CLI - MENU PRINCIPAL",
                [
                    "Ajouter un torrent", "Lister torrents", "Gérer torrent",
                    "Vérifier intégrité", "Créer ZIP", "Reconfigurer", "Quitter"
                ]
            )

            try:
                if choice == 1: await self.add_torrent_flow()
                elif choice == 2: await self.list_torrents()
                elif choice == 3: await self.manage_torrent()
                elif choice == 4: await self.verify_torrent()
                elif choice == 5: await self.create_zip()
                elif choice == 6: await self.init_client()
                elif choice == 7: self.running = False
            except Exception as e:
                print(f"\nErreur: {e}")

    async def add_torrent_flow(self):
        """Flux d'ajout de torrent"""
        choice = await self.show_menu(
            "AJOUTER UN TORRENT",
            ["Fichier .torrent", "Lien magnet", "Retour"]
        )

        if choice == 3: return

        source = await self._get_input(
            "Chemin vers le fichier" if choice == 1 else "Lien magnet"
        )
        if choice == 1 and not Path(source).exists():
            print("Fichier introuvable!")
            return

        save_path = await self._get_input("Dossier de sauvegarde")
        await self._add_and_track(source, save_path)

    async def _add_and_track(self, source, save_path=None):
        """Ajoute et suit un torrent"""
        if not self.client:
            print("Client non initialisé!")
            return
            
        tid = await self.client.add(source, save_path)
        if not tid:
            print("Échec de l'ajout")
            return
            
        print(f"\nTorrent ID: {tid}")
        self.current_tid = tid
        
        while True:
            stats = await self.client.stats(tid)
            if not stats: break
                
            print(
                f"\rProgression: {stats.progress:.1f}% | "
                f"↓{stats.dl_rate:.1f}kB/s | "  # Utilisation de dl_rate depuis TorrentStats
                f"Pairs: {stats.peers} | "       # Utilisation de peers depuis TorrentStats
                f"État: {stats.state}", 
                end="", flush=True
            )
                  
            if stats.state == TorrentState.COMPLETED:
                print("\n\nTéléchargement terminé!")
                break
                
            await asyncio.sleep(1)

    async def list_torrents(self):
        """Liste les torrents actifs"""
        if not self.client or not self.client.handles:
            print("\nAucun torrent actif")
            return
            
        print(f"\n{'-'*80}\n{'TORRENTS ACTIFS'.center(80)}\n{'-'*80}")
        print(f"{'ID':<15} {'Nom':<40} {'Progr.':<8} {'État':<15}\n{'-'*80}")
        
        for tid, handle in self.client.handles.items():
            stats = await self.client.stats(tid)
            if stats:
                print(f"{tid:<15} {handle.name()[:40]:<40} "
                      f"{stats.progress:.1f}%{'':<6} {str(stats.state):<15}")

    async def manage_torrent(self):
        """Gestion d'un torrent spécifique"""
        if not self.client or not self.client.handles:
            print("\nAucun torrent à gérer")
            return
            
        tid = await self._get_input("ID du torrent")
        if tid not in self.client.handles:
            print("ID invalide")
            return
            
        self.current_tid = tid
        handle = self.client.handles[tid]
        
        while True:
            stats = await self.client.stats(tid)
            if not stats: break
                
            print(f"\n{'='*50}\nGESTION: {handle.name()[:50]}\n{'='*50}")
            print(stats)
            
            choice = await self.show_menu(
                "OPTIONS",
                ["Pause/Reprendre", "Lister fichiers", "Priorités", "Supprimer", "Retour"]
            )

            if choice == 1:
                action = "reprendre" if stats.state == TorrentState.PAUSED else "pause"
                await (self.client.resume(tid) if action == "reprendre" 
                       else self.client.pause(tid))
                print(f"Torrent en {action}")
            elif choice == 2: await self.show_files(tid)
            elif choice == 3: await self.set_priorities(tid)
            elif choice == 4:
                if await self._get_input("Supprimer fichiers? (o/n)").lower() == 'o':
                    await self.client.remove(tid, True)
                    print("Torrent et fichiers supprimés")
                    return
            elif choice == 5: return

    async def show_files(self, tid):
        """Affiche les fichiers d'un torrent"""
        files = await self.client.get_files(tid)
        if not files:
            print("\nAucun fichier disponible")
            return
            
        print(f"\n{'-'*80}\n{'FICHIERS'.center(80)}\n{'-'*80}")
        print(f"{'ID':<5} {'Taille':<8} {'Progr.':<8} {'Prior.':<8} {'Chemin'}\n{'-'*80}")
        
        for f in files:
            print(f"{f['index']:<5} {f['size']/(1024*1024):<8.1f} "
                  f"{f['progress']:<8.1f} {f['priority']:<8} {f['path']}")

    async def set_priorities(self, tid):
        """Modifie les priorités des fichiers"""
        await self.show_files(tid)
        try:
            indices = [
                int(i) for i in 
                await self._get_input("Fichiers (ex: 0,2,3)").split(",")
            ]
            priority = await self._get_input("Priorité (0-7)", 1, int)
            
            if await self.client.set_priority(tid, indices, priority):
                print("Priorités mises à jour")
        except ValueError:
            print("Saisie invalide")

    async def verify_torrent(self):
        """Vérifie l'intégrité d'un torrent"""
        if not self.client or not self.client.handles:
            print("\nAucun torrent à vérifier")
            return
            
        tid = await self._get_input("ID du torrent")
        if tid not in self.client.handles:
            print("ID invalide")
            return
            
        print("\nVérification en cours...")
        result = await self.client.verify(tid)
        
        print(f"\n{'='*50}\n{'RÉSULTATS'.center(50)}\n{'='*50}")
        print(f"Progrès: {result['progress']:.1f}%")
        print(f"Complet: {'Oui' if result['is_complete'] else 'Non'}")
        
        if result['files']:
            print("\nFichiers incomplets:")
            for f in result['files']:
                if f['progress'] < 100:
                    print(f"- {f['path']} ({f['progress']:.1f}%)")

    async def create_zip(self):
        """Crée une archive ZIP"""
        if not self.client or not self.client.handles:
            print("\nAucun torrent disponible")
            return
            
        tid = await self._get_input("ID du torrent")
        if tid not in self.client.handles:
            print("ID invalide")
            return
            
        await self.show_files(tid)
        indices = [
            int(i) for i in 
            await self._get_input("Fichiers à inclure (ex: 0,2,3)").split(",")
        ] if await self._get_input("Tous les fichiers? (o/n)").lower() == 'n' else None
        
        output = await self._get_input("Fichier de sortie", "archive.zip")
        compression = await self._get_input("Compression (none/fast/normal/high)", "normal")
        
        print("\nCréation de l'archive...")
        result = await self.client.create_zip(tid, output, indices, compression)
        
        print(f"\nArchive créée: {result}" if result else "\nÉchec de création")

    async def run(self):
        """Lance l'application"""
        print("\n" + "TORRENT CLI".center(50) + "\n" + "="*50)
        await self.init_client()
        await self.main_loop()
        
        if self.client:
            await self.client.close()
        print("\nFermeture de l'application")

if __name__ == "__main__":
    cli = TorrentCLI()
    asyncio.run(cli.run())