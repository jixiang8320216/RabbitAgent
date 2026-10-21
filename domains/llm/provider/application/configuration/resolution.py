import abc

import domains.network.domain.proxy_configuration as proxy_configuration
import domains.llm.provider.application.configuration.profile_migration as provider_profile_migration
import domains.llm.provider.application.capability.module_registry as provider_module_registry


def resolve_profile_proxy_configuration(
    profile: abc.Mapping[str,object],
    global_configuration:abc.Mapping[str,object],
)->proxy_configuration.ProxyConfiguration:
    migrated_profile = provider_profile_migration.migrate_provider_profile(profile)
    provider_id = str(migrated_profile.get("provider") or "").strip().casefold()
    module = provider_module_registry.ProviderModuleRegistry.get_module(provider_id)
    inherits_global_proxy=(
        module.capabilities.authentication.inherits_global_proxy if module is not None else True
    )

    return proxy_configuration.resolve_profile_proxy_configuration(
        migrated_profile,
        global_configuration,
        inherits_global_proxy = inherits_global_proxy,
    )