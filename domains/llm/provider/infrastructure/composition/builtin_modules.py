
import domains.llm.provider.application.capability.provider_module as provider_module
import domains.llm.provider.infrastructure.families.anthropic as authropic
import domains.llm.provider.infrastructure.families.openai_compatible as openai_compatible


BUILTIN_PROVIDER_MODULES:tuple[provider_module.ProviderModule,...]=(
    authropic.MODULE,
    openai_compatible.MODULE,
)

__all__=["BUILTIN_PROVIDER_MODULES"]