import pydantic

# 会话/应用状态里大量出现「任意 JSON 对象」字段（迁移前定义于各源顶层包，
# 折叠进 domains.configuration 后缺失）。这里给出与 agent 域一致的规范别名，
# 供 runtime.session_management 等模块直接引用，避免重复定义。
JsonObject = dict[str, pydantic.JsonValue]


class AppState(pydantic.BaseModel):
    ...