import threading

import domains.llm.provider.application.capability.provider_factory as provider_factory
import domains.llm.provider.application.capability.module_registry as provider_module_registry
import logging

_registration_lock = threading.Lock()
logger = logging.getLogger(__name__)

def register_all_providers() -> None:

    for module in provider_module_registry.ProviderModuleRegistry.get_modules():
        for registration in module.registrations:
            provider_factory.LLMProviderFactory.register(
                registration.provider_id,
                registration.adapter_type,
                force = True,)
    available_providers = provider_factory.LLMProviderFactory.get_available_providers()
    logger.debug("[PROVIDER_REGISTRY]Registered %d providers: %s", len(available_providers),",".join(available_providers))


def ensure_all_providers_registered() -> None:
    export_provider_names ={
        registration.provider_id
        for module in provider_module_registry.ProviderModuleRegistry.get_modules()
        for registration in module.registrations
    }
    registered = set(provider_factory.LLMProviderFactory.get_available_providers())
    if export_provider_names <= registered:
        return
    with _registration_lock:
        registered = set(provider_factory.LLMProviderFactory.get_available_providers())
        if not export_provider_names <= registered:
            register_all_providers()