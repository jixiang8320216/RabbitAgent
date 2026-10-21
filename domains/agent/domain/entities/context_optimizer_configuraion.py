"""上下文优化器的配置模型。

上下文优化是「用小模型把长提示词压短」的旁路能力：优化器自己也需要
一份配置（用哪个 provider、哪个模型、什么协议），与业务 agent 的配置
分开管理 —— 优化器的模型选择不应该影响业务 agent。

**历史缺口**：本模块此前是 0 字节文件，而
``context_optimizer_adapter.ContextOptimizerBootstrapper.bootstrap``
按这些字段构造配置对象，导致上下文优化链路在装配阶段就 AttributeError。

**为什么文件名是 ``configuraion``**：迁移时拼写已错，但改名会破坏
既有引用路径，因此保留文件名，在文档中标注正确拼写。
"""

from __future__ import annotations

import typing

import pydantic


class ContextOptimizerConfiguration(pydantic.BaseModel):
    """上下文优化器的运行配置。

    :param provider: provider 名（``openai`` / ``anthropic`` …）
    :param api_protocol: 使用的 API 协议
    :param model_name: 优化器模型名
    :param base_url: 自定义端点；空串表示用 provider 默认
    :param api_key: 访问凭据
    :param http_proxy / https_proxy: 代理地址
    :param proxy_username / proxy_password: 代理认证
    :param use_proxy: 是否启用代理
    :param thinking_mode: 是否开启思考模式
    :param service_zone: 服务档位
    :param agent_id: 优化器自身的 agent 标识（用于日志与追踪）
    :param codeagent_environment: CodeAgent 运行环境标识
    :param custom_headers: 附加到请求的自定义头
    """

    model_config = pydantic.ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
    )

    provider: str
    api_protocol: typing.Any = None
    model_name: str
    base_url: str = ""
    api_key: str = ""
    http_proxy: str | None = None
    https_proxy: str | None = None
    use_proxy: bool | None = None
    proxy_username: str | None = None
    proxy_password: str | None = None
    thinking_mode: bool | None = None
    service_zone: str = ""
    agent_id: str = ""
    codeagent_environment: str = ""
    custom_headers: typing.Mapping[str, str] = pydantic.Field(default_factory=dict)

    def has_credential(self) -> bool:
        """是否具备可用的访问凭据。"""
        return bool(self.api_key and self.api_key.strip())


# 正确拼写的别名
ContextOptimizerConfig = ContextOptimizerConfiguration

__all__ = ["ContextOptimizerConfig", "ContextOptimizerConfiguration"]
