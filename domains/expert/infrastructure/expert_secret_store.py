import threading
from pathlib import Path
from domains.permission.infrastructure.security.credential_encryption import EncryptionService

class ExpertMcpSecretStore:

    def __init__(self,
        user_data_home:Path,
        *,
        encryption_service: EncryptionService|None = None,
        ) -> None:
        if not user_data_home.is_absolute():
            raise ValueError("user_data_home must be a absolute path")
        self._path = user_data_home/"expert-secrets"/"mcp.json"
        self._encryption_service = encryption_service or EncryptionService()
        self._lock= threading.Lock()