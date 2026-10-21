import threading
import typing


class EncryptionService:
    ENCRYPTION_PREFIX="ENC:"

_encryption_service:typing.Optional[EncryptionService]=None
_encryption_service_lock= threading.Lock()


def get_encryption_service()->EncryptionService:
    global _encryption_service
    if _encryption_service is None:
        with _encryption_service_lock:
            if _encryption_service is None:
                _encryption_service = EncryptionService()
    return _encryption_service