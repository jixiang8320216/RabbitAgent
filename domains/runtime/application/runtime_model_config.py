import secrets

import domains.configuration as configuration


def _stable_model_id_from_profile(profile: configuration.JsonOject)->str:
    return secrets.token_hex(6)

def ensure_profile_stable_id(profile: configuration.JsonObject)->configuration.JsonOject:

    normalized = dict(profile or {})
    if "id" not in normalized or not normalized["id"]:
        normalized["id"] = _stable_model_id_from_profile(normalized)

    provider = normalized["provider"]
    if provider and isinstance(provider, str) and "_" in provider:
        original_provider = provider
        normalized_provider = provider.replace("_", "-")
        normalized["provider"]= normalized_provider
    return normalized



def normalize_profiles_with_stable_ids(
    llm_configs: list[configuration.JsonObject],
)->list[configuration.JsonObject]:
    return [ensure_profile_stable_id(profile) for profile in (llm_configs or [])]