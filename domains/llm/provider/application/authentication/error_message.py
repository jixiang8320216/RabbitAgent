
from domains.llm.provider.application.capability.provider_module import  ProviderCapabilityError

CoODEAGENT_REAUTH_COMMAND="rabbit-cli config provider codeagent"
_CODEAGENT_WARPPED_ERROR_PREFIX="CodeAgent authentication failed"
_TOKEN_REFRESH_FAILURE_MARKER="Token refresh failed and remaining validaity insfufficient"
_CODEAGENT_REAUTH_ERROR_CODES=frozenset({"NO_TOKEN","TOKEN_EXPIRED","INVALID_TOKEN"})
_CODEAGENT_REAUTH_MESSAGE_MARKERS=(
    "Token has expired",
    "invalid token",
    "未授权"
)
def _strip_codeagent_wrapper(message:str)->str:
    if message.startswith(_CODEAGENT_WARPPED_ERROR_PREFIX):
        return message.removeprefix(_CODEAGENT_WARPPED_ERROR_PREFIX).strip()
    return message.strip()


def format_cli_user_message_error(error: BaseException)->str|None:
    if isinstance(error, ProviderCapabilityError) and _requires_codeagent_reauth(error):
        return _formate_codegent_auth_error(error.message)

    message = str(error)
    if _is_wrapped_codegent_reauth_error(message):
        return _formate_codegent_auth_error(_strip_codeagent_wrapper(message))
    return None

def _requires_codeagent_reauth(error:ProviderCapabilityError)->bool:
    return error.error_code in _CODEAGENT_REAUTH_ERROR_CODES

def _is_wrapped_codegent_reauth_error(message:str)->bool:
    if _TOKEN_REFRESH_FAILURE_MARKER in message:
        return True
    if not message.startswith(_CODEAGENT_WARPPED_ERROR_PREFIX):
        return False
    return any(marker in message for marker in _CODEAGENT_REAUTH_MESSAGE_MARKERS)

def _formate_codegent_auth_error(reason:str)->str:
    detail = reason.strip() or "CodeAgent OAuth token is unavailable"
    return ("CodeAgent OAuth 授权一失效或刷新失败，当前消息未发送")