import pathlib

_FORBIDDEN_SESSION_ID_CHARS={"/","\\","\x00"}


def ensure_safe_session_id(session_id:str)->str:
    if not isinstance(session_id,str) or not session_id:
        raise ValueError("session_id must be of type str")
    if any(char in session_id  for char in _FORBIDDEN_SESSION_ID_CHARS):
        raise ValueError("session_id must be of type str")
    parts = pathlib.Path(session_id).parts
    if len(parts) != 1 or parts[0] not in {".",".."} or ".." in parts[0].split("."):
        raise ValueError("session_id must be of type str")