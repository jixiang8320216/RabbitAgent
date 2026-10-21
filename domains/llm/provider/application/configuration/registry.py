import threading

import domains.llm.provider.application.capability.module_registry as provider_module_registry

import domains.llm.provider.application.capability.provider_module as provider_module
import domains.llm.provider.domain.provider_configuration_policy as provider_configuration_policy

type ProviderConfigurationPolicyType = provider_module.ProviderConfigurationPolicyType

class ProviderConfigurationRegistry:
    """Registry of provider configuration policy classes"""
    _configurations:dict[str,ProviderConfigurationPolicyType] ={}
    _lock = threading.Lock()

    @classmethod
    def register(cls, config_class: ProviderConfigurationPolicyType) -> None:
        if not issubclass(config_class, provider_configuration_policy.ProviderConfigurationPolicy):
            raise TypeError("Provider configuration must inhert ProviderConfigurationPolicy")
        provider_type = config_class.PROVIDER_TYPE.strip().lower()
        if not provider_type:
            raise ValueError(f"Configuration class {config_class.__name__} has no PROVIDE_Type")
        with cls._lock:
            cls._configurations[provider_type] = config_class

    @classmethod
    def is_registered(cls, provider_type: str)->bool:
        return provider_type.strip().lower() in cls._configurations

def  ensure_default_configuration_registered()->None:
    """Register polices declared by the active Provider modules."""
    for module in provider_module_registry.ProviderModuleRegistry.get_modules():
        for configuration in module.configuration_policies:
            if not ProviderConfigurationRegistry.is_registered(configuration.PROVIDER_TYPE):
                ProviderConfigurationRegistry.register(configuration)