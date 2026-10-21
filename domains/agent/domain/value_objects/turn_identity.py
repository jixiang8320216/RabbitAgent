import contextvars

_TURN_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "realy_conversation_turn_id",
    default=None,
)

def set_turn_id(turn_id:str)->contextvars.Token[str|None]:
    if not isinstance(turn_id,str) or not turn_id.strip():
       raise TypeError("turn_id is required")
    return _TURN_ID.set(turn_id)

def get_turn_id()->str|None:
    return _TURN_ID.get()

def reset_turn_id(token: contextvars.Token[str|None])->None:
    _TURN_ID.reset(token)