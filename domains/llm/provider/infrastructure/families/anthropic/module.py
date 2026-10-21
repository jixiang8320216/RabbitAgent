
import domains.llm.provider.application.capability.provider_module as provider_module
import domains.llm.provider.infrastructure.families.anthropic.provider as anthropic_provider
import domains.llm.provider.domain.anthropic.provider_configuration as  anthropic_configuration

MODULE= provider_module.ProviderModule(
    family_id = "anthropic",
    registrations=(
        provider_module.ProviderAdapterRegistration(
            "anthropic",
             anthropic_provider.AnthropicProvider,
        ),
    ),
    configuration_policies =(anthropic_configuration.AnthropicProviderConfiguration,),
    capabilities=provider_module.ProviderModuleCapabilities(
        authentication=provider_module.ProviderAuthenticationCapability(requires_api_key=True),
        multimodal= provider_module.ProviderMultimodalCapability(),
    )
)