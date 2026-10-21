import abc
import enum


class AuthType(enum.StrEnum):
    API_KEY="api_key"
    OAUTH="oauth"



class ProviderConfigurationPolicy(abc.ABC):
    PROVIDER_TYPE:str
    DISPLAY_NAME:str
    AUTH_TYPE: AuthType =AuthType.API_KEY
    REQUIRED_FIELDS: tuple[str,...]=("provider","modelName")
    HIDDEN_FIELDS:tuple[str,...]=()
    SECRET_FIELDS:tuple[str,...]=()
    HARDCODED_URL:str|None=None
