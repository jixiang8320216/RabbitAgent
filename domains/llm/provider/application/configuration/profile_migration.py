

import collections.abc as abc
import domains.llm.provider.domain.runtime_configuration as runtime_configuration
import domains.llm.provider.application.capability.module_registry as provider_module_registry

# provider 别名迁移
def migrate_provider_profile(profile: abc.Mapping[str,object]) -> runtime_configuration.JsonObject:
    migrated = runtime_configuration.validate_json_object(dict(profile))
    for module in provider_module_registry.ProviderModuleRegistry.get_modules():
        migrator = module.profile_migrator
        if migrator is not None:
            migrated = migrator(migrated)
    return migrated