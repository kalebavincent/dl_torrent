import requests
import json
import time
from pathlib import Path
from typing import Optional, Dict

class FreeConvertBot:
    def __init__(self, api_key: str, download_dir: str = "downloads"):
        self.api_key = api_key
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.base_url = "https://api.freeconvert.com/v1/process"
        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

    def create_job(self, input_format: str = "mp4", output_format: str = "mp4") -> Optional[Dict]:
        """Crée un nouveau job de conversion"""
        payload = {
            "tasks": {
                "import-1": {
                    "operation": "import/upload"
                },
                "compress-1": {
                    "operation": "compress",
                    "input": "import-1",
                    "input_format": input_format,
                    "output_format": output_format
                },
                "export-1": {
                    "operation": "export/url",
                    "input": ["compress-1"]
                }
            }
        }

        try:
            response = requests.post(
                f"{self.base_url}/jobs",
                headers=self.headers,
                json=payload
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Erreur création job: {str(e)}")
            return None

    def upload_file(self, job_id: str, file_path: Path) -> bool:
        """Upload un fichier pour le job"""
        upload_url = f"{self.base_url}/import/upload"
        
        try:
            with open(file_path, 'rb') as f:
                files = {'file': (file_path.name, f)}
                response = requests.post(
                    upload_url,
                    headers={'Authorization': self.headers['Authorization']},
                    files=files,
                    data={'job': job_id, 'task': 'import-1'}
                )
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Erreur upload: {str(e)}")
            return False

    def wait_for_completion(self, job_id: str, timeout: int = 300, interval: int = 5) -> bool:
        """Attend la fin du traitement"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                response = requests.get(
                    f"{self.base_url}/jobs/{job_id}",
                    headers=self.headers
                )
                data = response.json()
                
                if data.get('status') == 'completed':
                    return True
                elif data.get('status') == 'failed':
                    print(f"Job échoué: {data.get('error', 'Unknown error')}")
                    return False
                
                print(f"Progression: {data.get('progress', 0)}%")
                time.sleep(interval)
                
            except requests.exceptions.RequestException as e:
                print(f"Erreur vérification statut: {str(e)}")
                return False
        
        print("Délai dépassé")
        return False

    def download_result(self, job_id: str, output_filename: Optional[str] = None) -> Optional[Path]:
        """Télécharge le fichier converti"""
        try:
            # Récupérer les infos d'export
            response = requests.get(
                f"{self.base_url}/jobs/{job_id}",
                headers=self.headers
            )
            data = response.json()
            
            export_task = next(
                (t for t in data['tasks'] if t.get('operation') == 'export/url'),
                None
            )
            
            if not export_task or not export_task.get('result', {}).get('files'):
                print("Aucun fichier à exporter")
                return None
                
            download_url = export_task['result']['files'][0]['url']
            filename = output_filename or export_task['result']['files'][0]['filename']
            output_path = self.download_dir / filename
            
            # Téléchargement
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(output_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            
            return output_path
            
        except Exception as e:
            print(f"Erreur téléchargement: {str(e)}")
            return None

    def process_file(self, input_file: Path, output_format: str = "mp4") -> Optional[Path]:
        """Processus complet de conversion"""
        # 1. Création du job
        job = self.create_job(input_format=input_file.suffix[1:], output_format=output_format)
        if not job:
            return None
            
        job_id = job['id']
        print(f"Job créé: {job_id}")
        
        # 2. Upload du fichier
        if not self.upload_file(job_id, input_file):
            return None
        print("Fichier uploadé avec succès")
        
        # 3. Attente traitement
        if not self.wait_for_completion(job_id):
            return None
        print("Traitement terminé")
        
        # 4. Téléchargement résultat
        output_file = self.download_result(job_id)
        if output_file:
            print(f"Fichier téléchargé: {output_file}")
            return output_file
        
        return None