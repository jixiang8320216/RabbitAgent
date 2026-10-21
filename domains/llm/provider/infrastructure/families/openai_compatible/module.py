
import domains.llm.provider.application.capability.provider_module as provider_module
import domains.llm.provider.domain.provider_identity as provider_identity
import domains.llm.provider.infrastructure.families.openai_compatible.provider as openai_provider
import domains.llm.provider.domain.openai_compatible.provider_configuration as openai_configuration
import domains.llm.provider.domain.openai_compatible.legacy_custom_configuration as custom_configuration
import domains.llm.provider.domain.runtime_configuration as runtime_configuration
import domains.llm.provider.infrastructure.families.openai_compatible.legacy_custom_provider as custom_provider

DEFAULT_PROVIDER_ID= provider_identity.LLMProviderType.OPENAI.value

def _migrate_profile(profile: runtime_configuration.JsonObject)->runtime_configuration.JsonObject:
    migrated=dict(profile)
    provider_id = provider_identity.normalize_provider_name(migrated.get("provider"))
    if provider_id in provider_identity.OENAI_COMPATIBLE_LEGACY_ALIASES:
        migrated["provider"] = provider_identity.LLMProviderType.OPENAI.value
    if provider_id in provider_identity.OPENAI_COMPATIBLE_PROFILE_PROVIDERS:
        meta=migrated.get("metadata")
        if isinstance(meta, dict):
            canonical_metadata=dict(meta)
            legacy_base_url= canonical_metadata.get("baseURL",None)
            if not migrated.get("baseURL") and isinstance(legacy_base_url, str) and legacy_base_url.strip():
                migrated["baseURL"] = legacy_base_url.strip()
            if canonical_metadata:
                migrated["metadata"] = canonical_metadata
            else:
                migrated.pop("metadata", None)
    return migrated

MODULE= provider_module.ProviderModule(
    family_id = provider_identity.OPENAI_COMPATIBLE_PROVIDER_NAME,
    registrations=(
        provider_module.ProviderAdapterRegistration(
             DEFAULT_PROVIDER_ID,
             openai_provider.OpenAIProvider,
        ),
        provider_module.ProviderAdapterRegistration(
            provider_identity.OPENAI_COMPATIBLE_PROVIDER_NAME,
            openai_provider.OpenAIProvider,
        ),
        provider_module.ProviderAdapterRegistration(
            provider_identity.LLMProviderType.CUSTOM.value,
            custom_provider.CustomProvider,
        ),
    ),
    configuration_policies =(openai_configuration.OpenAIProviderConfiguration,
                             custom_configuration.CustomProviderConfiguration),
    capabilities=provider_module.ProviderModuleCapabilities(
        authentication=provider_module.ProviderAuthenticationCapability(requires_api_key=True),
        multimodal= provider_module.ProviderMultimodalCapability(),
    ),
    profile_migrator=_migrate_profile,
)