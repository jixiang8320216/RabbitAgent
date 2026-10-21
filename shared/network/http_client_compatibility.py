import httpx

def _version_pair(version:str)->tuple[int, int]:
    components = version.split('.', maxsplit=2)
    try:
        major = int(components[0])
        minor = int(components[1])
    except ValueError:
        return (0, 0)
    return (major, minor)

_httpx_version = _version_pair(httpx.__version__)

_PROXY_PARAM_SUPPORTED= _httpx_version >=(0,25)

def build_proxy_kwargs(
    proxy_url:str|None=None,
    trust_env:bool=True,
) -> dict[str, object]:
    if proxy_url is None and not trust_env:
        return {"proxy":None, "trust_env":False}
    if proxy_url:
        if _PROXY_PARAM_SUPPORTED:
            return {"proxy":proxy_url, "trust_env":trust_env}
        return {"proies":{"http://":proxy_url, "https://": proxy_url}, "trust_env":trust_env}
    return {"trust_env":trust_env}