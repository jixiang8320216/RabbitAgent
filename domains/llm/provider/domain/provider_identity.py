
import secrets
import enum


LEGACY_CODEMATE_YELLOW_PROVIDER="codemate-yellow"
LEGACY_CODEMATE_YELLOW_ALIAS="codemate_yellow"
OPENAI_COMPATIBLE_PROVIDER_NAME="openai_compatible"



class LLMProviderType(enum.Enum):
    CODEMATE="codemate"
    CODAGENT="codagent"
    RABBITAGENT="rabbitagent"
    HIS="his"
    OPENAI="openai"
    ANTHROPIC="anthropic"
    AZURE="azure"
    CUSTOM="custom"
    OPENAI_CODEX="openai_codex"

CODEAGENT_RPOVIDERS=frozenset({
    LLMProviderType.CODEMATE.value,
    LEGACY_CODEMATE_YELLOW_PROVIDER
})

PROVIDER_MANAGED_API_KEY_PROVIDERS =CODEAGENT_RPOVIDERS

OENAI_COMPATIBLE_LEGACY_ALIASES=frozenset(
    {
        LLMProviderType.CUSTOM.value,
        OPENAI_COMPATIBLE_PROVIDER_NAME,
    }
)

OPENAI_COMPATIBLE_PROFILE_PROVIDERS=frozenset(
    {
        LLMProviderType.OPENAI.value,
        LLMProviderType.CUSTOM.value,
        OPENAI_COMPATIBLE_PROVIDER_NAME,
    }
)

PROVIDER_MANAGED_BASE_URL_PROVIDERS=frozenset({
    *PROVIDER_MANAGED_API_KEY_PROVIDERS,
    LLMProviderType.HIS.value,
    LLMProviderType.RABBITAGENT.value,
}
)

def generate_profile_id()->str:
    return secrets.token_hex(4)


def normalize_provider_name(value:object)->str:
    raw_value= value.value if isinstance(value, LLMProviderType) else value
    normalized =str(raw_value or LLMProviderType.OPENAI.value).strip().casefold()
    if normalized == LEGACY_CODEMATE_YELLOW_ALIAS:
        return LEGACY_CODEMATE_YELLOW_PROVIDER
    return normalized