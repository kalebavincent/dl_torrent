import json
from pathlib import Path
from typing import Dict

class Lang:
    def __init__(self, dir="lang", default="fr"):
        self.tr = {}
        self.lang = default
        for f in Path(dir).glob("*.json"):
            with open(f, 'r', encoding='utf-8') as file:
                self.tr[f.stem] = json.load(file)

    def set(self, lang: str):
        if lang in self.tr:
            self.lang = lang

    def get(self, key: str, **kw) -> str:
        try:
            parts = key.split('.')
            val = self.tr[self.lang]
            for p in parts:
                val = val[p]
            return val.format(**kw) if kw else val
        except:
            return key

tr = Lang()