import json
import os
from typing import Dict, Optional

DATA_FILE = "user_folders.json"

class UserFolderManager:
    def __init__(self, data_file: str = DATA_FILE):
        self.data_file = data_file
        self.data: Dict[str, str] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
            except Exception as e:
                print(f"[DataStore] Failed to load data file: {e}")
                self.data = {}
        else:
            self.data = {}

    def _save(self):
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[DataStore] Failed to save data file: {e}")

    def get_folder_token(self, user_id: str) -> Optional[str]:
        return self.data.get(user_id)

    def save_folder_token(self, user_id: str, folder_token: str):
        self.data[user_id] = folder_token
        self._save()
