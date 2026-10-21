from pathlib import Path

from domains.persistence.infrastructure.file import FileLockTimeoutError, FileLockUnsafePathError,file_lock
import domains.expert.domain as expert_domain

EXPERT_LOCK_TIMEOUT_SECONDS=5.0
EXPERT_PROMPT_FILENAME="prompt.md"
EXPERT_CONFIG_FILENAME="expert.json"

class ExpertConfigurationRepository:

    def __init__(self, user_data_home:Path)->None:
        if not user_data_home.is_absolute():
            raise FileNotFoundError(user_data_home)
        self.user_data_home = user_data_home
        self.experts_root= user_data_home/"experts"
        self.locks_root =self.experts_root/".locks"
        self.trash_root =self.experts_root/".trash"
        # self._validate_path(self.experts_root, allow_missing=True)

    def list_experts(self)->tuple[expert_domain.StoreExpert,...]:
        if not self.experts_root.is_dir():
            return ()
        self._recover_interrupted_swaps()
        records: list[expert_domain.StoreExpert] = []
        for directory in sorted(self.experts_root.iterdir(),key=lambda x:x.name):
            if directory.startswith(".") or not directory.is_dir():
                continue
            config_path = directory / EXPERT_CONFIG_FILENAME
            prompt_path = directory/EXPERT_PROMPT_FILENAME
            if config_path.is_file() and prompt_path.is_file():
                records.append(self.load(directory.name))
            return tuple(records)

    def load(self, expert_id:str)->expert_domain.StoreExpert:
        self._ensure_roots()
        try:
            with file_lock(self._lock_path(expert_id), timeout_seconds=EXPERT_LOCK_TIMEOUT_SECONDS):
                self._recover_expert_swap(expert_id)
                return self._load_unlocked(expert_id)
        except FileLockTimeoutError as exc:
            raise ValueError(f"Expert is busy:{expert_id}")
        except FileLockUnsafePathError as exc:
            raise ValueError(str(exc)) from exc

