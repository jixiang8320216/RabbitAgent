import typing

import pydantic
import collections.abc
import collections.abc as abc

from domains.llm.provider.domain import runtime_configuration
import domains.llm.provider.domain.provider_configuration_policy as provider_configuration_policy
import domains.llm.provider.application.model.multimodal_configuration as multimodal_configuration
import domains.llm.provider.application.capability.adapter_port as provider_adapter_port
import domains.llm.provider.application.authentication.provider_authentication as provider_authentication


type ProviderAdapterType = type[provider_adapter_port.LLMProviderAdapter]
type ProviderConfigurationPolicyType =type[provider_configuration_policy.ProviderConfigurationPolicy]
type ProviderProfileEnvironmentValidator= collections.abc.Callable[[collections.abc.Mapping[str,object]],None]
type ProviderProfileEnvironmentSStamper =collections.abc.Callable[[collections.abc.Mapping[str,object]],runtime_configuration.JsonObject]


class ProviderCapabilityError(RuntimeError):
    def __init__(self,
                 message:str,
                 *,
                 error_code:str,
                 suggestion:str="",
                 is_retryable:bool=False,
                 )->None:
        self.message = message
        self.error_code = error_code
        self.suggestion = suggestion
        self.is_retryable = is_retryable
        super().__init__(message)

class ProviderAuthenticationHookRequest(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")


type ProviderAuthHookFactory= collections.abc.Callable[
    ["ProviderAuthenticationHookRequest"],
    provider_authentication.ProviderAuthHook]

type ProviderProfileMigrator = collections.abc.Callable[
    [runtime_configuration.JsonObject],
     runtime_configuration.JsonObject
]

class ProviderOAuthServicePort(typing.Protocol):
    """

    """


class ProviderCredentialExchangePort(typing.Protocol):
    """

    """


class ProviderAuthenticationRuntimePort(typing.Protocol):
    """

    """

class ProviderAuthenticationEnvironment(pydantic.BaseModel):
    """"""



@typing.runtime_checkable
class ProviderCapabilityLifecycle(typing.Protocol):

    def start(self)->None:...

    def shutdown(self)->None:...

class  ProviderAuthenticationCapability(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    requires_api_key: bool
    inherits_global_proxy:bool=True
    auto_hook_factory:ProviderAuthHookFactory |None=None
    oauth_service_factory: collections.abc.Callable[[],ProviderOAuthServicePort]|None=None
    credential_exchange_factory: collections.abc.Callable[[], ProviderCredentialExchangePort] |None=None
    runtime_factory: collections.abc.Callable[[], ProviderAuthenticationRuntimePort]|None=None
    environment_resolver: collections.abc.Callable[[],ProviderAuthenticationEnvironment] |None = None
    profile_environment_validator: ProviderProfileEnvironmentValidator |None = None
    profile_environment_stamper: ProviderProfileEnvironmentSStamper|None=None


ProviderAuthenticationCapability.model_rebuild()


class ProviderCatalogIdentityCacheUpate(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")


@typing.runtime_checkable
class ProviderModelCatalogPort(typing.Protocol):
    ...


class ProviderModeCatalogCapability(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    service_factory: collections.abc.Callable[[],ProviderModelCatalogPort]

    identity_cache_updater: collections.abc.Callable[[ProviderCatalogIdentityCacheUpate],None] |None = None


class ProviderQuotaRequest(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")

    access_token: pydantic.SecretStr
    model_id: str = pydantic.Field(min_length=1)


class ProviderQuotaWindow(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True, extra="forbid")
    type: typing.Literal["TPD","TPH","TPM"]
    used_quota:str
    total_quota:str
    remaning_quota:str
    usage_ratio:float|None=None
    period_start: str|None=None
    period_end: str|None=None
    reset_at: str|None=None

class ProviderQuotaUsagePort(typing.Protocol):
    async def fetch_quota(self,request: ProviderQuotaRequest)->tuple[ProviderQuotaWindow,...]:...

class  ProviderQuotaUsageCapability(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True,extra="forbid", arbitrary_types_allowed=True)

    service_factory: collections.abc.Callable[[],ProviderQuotaUsagePort]


class ProviderServiceZoneCapability(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True,extra="forbid")

    allow_values: tuple[str,...]
    default_value: str


class ProviderMultimodalCapability(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True,extra="forbid", arbitrary_types_allowed=True)

    projector: multimodal_configuration.ProviderMultimodalProjector |None = None


class  ProviderRequestAttestationCapability(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(frozen=True,extra="forbid", arbitrary_types_allowed=True)

    runtime: ProviderCapabilityLifecycle


class ProviderModuleCapabilities(pydantic.BaseModel):
    """Complete ,explicitly named capability declaration for one family"""
    model_config = pydantic.ConfigDict(frozen=True,extra="forbid", arbitrary_types_allowed=True)

    authentication: ProviderAuthenticationCapability
    model_catalog: ProviderModeCatalogCapability|None=None
    quota_usage: ProviderQuotaUsageCapability|None=None
    service_zone: ProviderServiceZoneCapability|None=None
    multimodal: ProviderMultimodalCapability|None=None
    request_attestation:ProviderRequestAttestationCapability|None=None

    def lifecycle_capabilities(self)->tuple[ProviderCapabilityLifecycle,...]:
        """"""
        if self.request_attestation is None:
            return ()
        return (self.request_attestation.runtime,)



class ProviderAdapterRegistration:
    """one Provider ID mapped to its request adapter registration"""
    __slots__ = ("_adapter_type","_provider_id")

    def __init__(self, provider_id:str, adapter_type:ProviderAdapterType) -> None:
        normalized_provider_id = provider_id.strip().lower()
        if not normalized_provider_id:
            raise ValueError("Invalid provider id")
        self._provider_id = normalized_provider_id
        self._adapter_type = adapter_type

    @property
    def provider_id(self)->str:
        return self._provider_id

    @property
    def adapter_type(self)->ProviderAdapterType:
        return self._adapter_type


class ProviderModule:
    """composition declaration owned by ont Provider family package"""

    __slots__ =(
         "_capabilities",
         "_configuration_policies",
         "_family_id",
         "_profile_migrator",
         "_registrations"
    )

    def __init__(
        self,
        *,
        family_id:str,  #"anthropic","codemate",...
        registrations: tuple[ProviderAdapterRegistration,...], #一个或多个provider_id
        configuration_policies: tuple[ProviderConfigurationPolicyType,...], #配置策略类
        capabilities: ProviderModuleCapabilities, #能力声明
        profile_migrator:ProviderProfileMigrator |None = None, # profile 迁移函数
    )->None:
        normalized_family_id = family_id.strip().lower()
        if not normalized_family_id:
            raise ValueError("Invalid family id")
        if not registrations:
            raise ValueError(f"Provider module {normalized_family_id} has no adapter registrations")
        provider_ids = tuple(registration.provider_id for registration in registrations)
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError(f"Provider module {normalized_family_id} has duplicate provider ids")
        self._family_id = normalized_family_id
        self._registrations = registrations
        self._configuration_policies = configuration_policies
        self._capabilities = capabilities
        self._profile_migrator = profile_migrator

    @property
    def family_id(self)->str:
        return self._family_id

    @property
    def registrations(self)->tuple[ProviderAdapterRegistration,...]:
        return self._registrations

    @property
    def configuration_policies(self)->tuple[ProviderConfigurationPolicyType,...]:
        return self._configuration_policies

    @property
    def capabilities(self)->ProviderModuleCapabilities:
        return self._capabilities

    @property
    def profile_migrator(self)->ProviderProfileMigrator |None:
        return self._profile_migrator


