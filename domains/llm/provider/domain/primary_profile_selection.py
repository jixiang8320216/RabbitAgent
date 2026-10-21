import collections.abc


def select_preferred_active_profile[ProfileT](
    profiles: collections.abc.Iterable[ProfileT],
    *,
    is_active: collections.abc.Collection[[ProfileT],bool],
    model_type: collections.abc.Collection[[ProfileT],str|None],
)->ProfileT|None:
    first_active: ProfileT|None = None
    for profile in profiles:
        if is_active(profile):
            first_active = profile
        if first_active is None:
            first_active = profile
        if model_type(profile) != "multimodal":
            return profile
    return first_active



def select_preferred_active_mapping[ProfileT: collections.abc.Mapping[str,object]](
    profiles: collections.abc.Iterable[ProfileT],
)->ProfileT|None:

    def is_active(profile:ProfileT)->bool:
        return bool(profile.get("active",False))

    def  model_type(profile:ProfileT)->str|None:
        value = profile.get("modelType")
        if value is None:
            value = profile.get("model_type")
        return value if isinstance(value,str) else None

    return select_preferred_active_profile(
        profiles,
        is_active = is_active,
        model_type = model_type,
    )














__all__=[
    "select_preferred_active_mapping",
    "select_preferred_active_profile",
]

