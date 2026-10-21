import pydantic
import collections.abc as abc


class ProxyConfiguration(pydantic.BaseModel):
    http_proxy:str |None= None
    https_proxy:str |None= None
    proxy_username:str |None= None
    proxy_password:str |None= pydantic.Field(default=None,repr=False)
    use_proxy:bool |None= None

    @classmethod
    def from_mapping(cls, value: abc.Mapping[str, object] | None) -> "ProxyConfiguration":
        """Build proxy settings from snake_case or camelCase configuration data."""
        if value is None:
            return cls()
        if not isinstance(value, abc.Mapping):
            raise TypeError("ProxyConfiguration.from_mapping expects a mapping")

        nested = value.get("proxy") or value.get("proxy_configuration")
        source = nested if isinstance(nested, abc.Mapping) else value
        aliases = {
            "http_proxy": ("http_proxy", "httpProxy"),
            "https_proxy": ("https_proxy", "httpsProxy"),
            "proxy_username": ("proxy_username", "proxyUsername"),
            "proxy_password": ("proxy_password", "proxyPassword"),
            "use_proxy": ("use_proxy", "useProxy"),
        }
        data: dict[str, object] = {}
        for field_name, field_aliases in aliases.items():
            for alias in field_aliases:
                if alias in source:
                    data[field_name] = source[alias]
                    break
        return cls.model_validate(data)



def resolve_profile_proxy_configuration(
    profile:abc.Mapping[str,object],
    global_configuration: abc.Mapping[str,object],
    *,
    inherits_global_proxy: bool = False,
)->ProxyConfiguration:
    """Resolve profile proxy settings with optional global inheritance."""
    profile_proxy = ProxyConfiguration.from_mapping(profile)
    global_proxy =ProxyConfiguration.from_mapping(global_configuration)
    use_proxy = profile_proxy.use_proxy
    if use_proxy is None and inherits_global_proxy:
        use_proxy = global_proxy.use_proxy

    return ProxyConfiguration(
        http_proxy=_profile_value_or_default(profile_proxy.http_proxy,global_proxy.http_proxy),
        https_proxy=_profile_value_or_default(profile_proxy.https_proxy, global_proxy.https_proxy),
        proxy_username=_profile_value_or_default(profile_proxy.proxy_username,global_proxy.proxy_username),
        proxy_password=_profile_value_or_default(profile_proxy.proxy_password,global_proxy.proxy_password),
        use_proxy=use_proxy,
    )

def _profile_value_or_default(profile:str|None,default_value:str|None)->str|None:
    normalized=profile.strip() if profile is not None else ""
    return normalized or default_value
