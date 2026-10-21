import domains.llm.provider.application.capability.provider_factory as provider_factory
from domains.agent.domain.value_objects import runtime_context
import domains.llm.provider.application.capability.module_registry as provider_module_registry
import domains.llm.provider.infrastructure.composition.builtin_modules as builtin_provider_modules
import domains.llm.provider.infrastructure.composition.registration  as provider_registration
import domains.llm.provider.application.configuration.registry as provider_configuration_registry
import domains.llm.provider.application.configuration.profile_migration as provider_profile_migration
import domains.llm.provider.application.model.chat_model_creation as chat_model_creation
import domains.llm.provider.application.formatting.formatter_creation as chat_formatter_creation
import domains.llm.provider.domain.runtime_configuration as runtime_configuration

def initialize_provider_context()->None:
    for module in builtin_provider_modules.BUILTIN_PROVIDER_MODULES:
        provider_module_registry.ProviderModuleRegistry.register(module)
    provider_configuration_registry.ensure_default_configuration_registered()
    provider_registration.ensure_all_providers_registered()


def initialize_provider_runtime()->None:
    initialize_provider_context() #确保默认provider 配置已注册，所有provider family已注册到registry。
    #启动所有已注册provider的能力模块
    provider_module_registry.ProviderModuleRegistry.start_capabilities()

# 通过责任链模式遍历所有已注册的Provider家族迁移器，将旧版/非标准provider配置字段规范化为当前格式，确保运行时正确匹配和创建ChatModel
def migrate_provider_profile(
    profile: runtime_context.JsonObject,
)-> runtime_configuration.JsonObject:
    initialize_provider_context()  # 幂等注册所有Provider 模块
    return provider_profile_migration.migrate_provider_profile(profile)

def create_provider_factory()->provider_factory.LLMProviderFactory:
    initialize_provider_context()
    return provider_factory.LLMProviderFactory()

get_model=chat_model_creation.get_model
get_formatter = chat_formatter_creation.get_formatter


__all__ = ["migrate_provider_profile",
           "initialize_provider_runtime",
           "create_provider_factory",
           "get_model",
           "get_formatter"]
