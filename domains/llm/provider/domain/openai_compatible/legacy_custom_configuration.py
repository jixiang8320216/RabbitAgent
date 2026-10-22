
import domains.llm.provider.domain.provider_configuration_policy as provider_configuration_policy
import domains.llm.provider.domain.runtime_configuration as runtime_configuration


class CustomProviderConfiguration(provider_configuration_policy.ProviderConfigurationPolicy):

      PROVIDER_TYPE:str = "custom"
      DISPLAY_NAME:str = "Custom"

      AUTH_TYPE: provider_configuration_policy.AuthType = provider_configuration_policy.AuthType.API_KEY

      REQUIRED_FILEDS:tuple[str,...]=("provider","modelName","baseUrl")
      HIDDEN_FIELDS:tuple[str,...]=(
             "tokens"
             )
      SECRET_FIELDS:tuple[str,...]=("apiKey")
      HARDCODED_URL:str|None=None

      @classmethod
      def validate_profile_dict(cls, profile:runtime_configuration.JsonObject)->tuple[str,...]:
            errors=[]
            for field in cls.REQUIRED_FILEDS:
                  value=profile.get(field)
                  if cls._is_empty(value):
                      errors.append(f"{field}: 该字段为必选项")
            base_url= profile.get("baseUrl")
            if base_url and not cls._is_empty(base_url):
                  if not base_url.startswith("http://") and not base_url.startswith("https://"):
                        errors.append("baseUrl: URL必须以 http://或https://开头")
            return errors