from collections.abc import Mapping

from pydantic import ConfigDict, Field

from domains.configuration import ConfigMapping
from domains.configuration.domain.model.config_data import ConfigData, RuntimeConfigModel
from domains.configuration.domain.model.llm_profiles import RuntimeModelProfileOverride, RuntimeLLMProfile


class RuntimePluginsSnapshot(RuntimeConfigModel):
      disabled: list[str] = Field(default_factory=list)
      config: ConfigData = Field(default_factory=dict)

      def resolve_disabled(self, disabled: list[str] | None) -> list[str]:
            """Merge runtime-disabled plugins with the configured disabled list."""
            configured = list(self.disabled)
            for plugin in disabled or []:
                  if plugin not in configured:
                        configured.append(plugin)
            return configured


class RuntimeSessionConfig(RuntimeConfigModel):
      session_id: str | None = Field(default=None, alias="sessionId")
      is_resume: bool = Field(default=False, alias="isResume")

      @classmethod
      def from_raw(cls, value: object) -> "RuntimeSessionConfig":
            if value is None:
                  return cls()
            if not isinstance(value, Mapping):
                  raise TypeError("RuntimeSessionConfig.from_raw expects a mapping")
            return cls.model_validate(dict(value))

      def with_runtime_fields(
            self,
            *,
            session_id: str | None,
            is_resume: bool,
      ) -> "RuntimeSessionConfig":
            return self.model_copy(
                  update={
                        "session_id": session_id,
                        "is_resume": is_resume,
                  }
            )

class RuntimeConfigBlock(RuntimeConfigModel):

      llm_config:ConfigMapping|None=None

      def without_llm_config(self) -> "RuntimeConfigBlock":
            """Return a copy of this block without its LLM-specific configuration."""
            return self.model_copy(update={"llm_config": None})

      def to_config_data(self) -> ConfigData:
            """Serialize the block while omitting the LLM configuration."""
            return self.model_dump(
                  mode="json",
                  by_alias=True,
                  exclude_none=True,
                  exclude={"llm_config"},
            )




class RuntimeConfigSnapshot(RuntimeConfigModel):
      model_config = ConfigDict(extra="allow", populate_by_name=True)
      session: RuntimeSessionConfig = Field(default_factory=RuntimeSessionConfig)
      plugins: RuntimePluginsSnapshot = Field(default_factory=RuntimePluginsSnapshot)
      llm_configs: list[RuntimeLLMProfile]=Field(default_factory=list)
      compressiin: RuntimeConfigBlock|None=None
      planning :RuntimeConfigBlock|None=None

      @classmethod
      def from_merged_config(cls, merged_config: ConfigMapping) -> "RuntimeConfigSnapshot":
            data = dict(merged_config)
            raw_profiles = data.get("llm_configs", data.get("llmConfigs", []))
            if isinstance(raw_profiles, Mapping):
                  raw_profiles = [raw_profiles]
            elif not isinstance(raw_profiles, list):
                  raw_profiles = []
            data["llm_configs"] = raw_profiles
            data.pop("llmConfigs", None)
            return cls.model_validate(data)

      def with_session_config(
            self,
            *,
            session_id: str | None,
            is_resume: bool,
      ) -> "RuntimeConfigSnapshot":
            # 键必须是字段名 ``session``。写成 "session_id" 时 model_copy 不校验，
            # 又因 extra="allow"，会把整个 RuntimeSessionConfig 实例塞进
            # JsonValue 类型的 extra 里：既丢更新（真正的 session 字段没变），
            # 又让每次 model_dump 刷一屏 pydantic serializer 警告。
            return self.model_copy(update={"session": self.session.with_runtime_fields(
                  session_id=session_id,
                  is_resume=is_resume,
                  )
                }
            )

      def with_model_profile_override(
            self,
            model_profile_override: RuntimeModelProfileOverride | None,
      ) -> "RuntimeConfigSnapshot":
            if model_profile_override is None or not model_profile_override.has_model():
                return self
            snapshot =self.model_copy(
                  update={
                             "llm_configs": [
                                          model_profile_override.to_llm_profile(),
                                          *[profile.deactivated() for profile in self.llm_configs],]
                          }
            )
            # 字段名拼写就是 compressiin（历史遗留）；update 键必须与之一致，
            # 写成 "compression" 会变成多出来的 extra（且值是模型实例，
            # 序列化时同样触发 pydantic serializer 警告）。
            if snapshot.compressiin is not None:
                  snapshot = snapshot.model_copy(update={"compressiin": snapshot.compressiin.without_llm_config()})
            if snapshot.planning is not None:
                  snapshot =snapshot.model_copy(update={"planning": snapshot.planning.without_llm_config()})
            return snapshot

      def with_disabled_plugins(
            self,
            disabled: list[str],
      ) -> "RuntimeConfigSnapshot":
            return self.model_copy(
                  update={"plugins": self.plugins.model_copy(update={"disabled": list(disabled)})}
            )