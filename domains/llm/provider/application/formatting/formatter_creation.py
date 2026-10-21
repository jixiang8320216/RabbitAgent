"""按 provider 配置造出请求格式化器。

装配链::

    ModelContextFactory.create_formatter
        -> llm.provider.composition.get_formatter
            -> chat_formatter_creation.get_formatter
                -> runtime.service.message_formatter.MessageFormatter

``MessageFormatter`` 目前对所有 provider 走同一条归一化路径，所以这里本质上
是把「调用方已知的 provider 上下文」收齐并交给它：

- ``system_prompt`` 此刻还没拼出来（system prompt 要等 agent 装配完才有），
  固定传 ``None``，由调用方后续覆盖 ``formatter.system_prompt``。
- ``tool_args_compression_limit`` / ``api_protocol`` 当前版本的
  ``MessageFormatter`` 不消费，挂在实例上留给后续差异化适配，不因为多了参数
  就让装配失败。
"""

from __future__ import annotations

import logging
import typing
import domains.llm.provider.domain.openai_app_protocol as openai_app_protocol
import domains.llm.provider.application.capability.provider_factory as provider_factory
from domains.llm.provider.domain import runtime_configuration

logger = logging.getLogger(__name__)

__all__ = ["get_formatter"]


def get_formatter(
    provider_name: object,
    tool_args_compression_limit: int = -1,
    model_name: str | None = None,
    api_protocol: openai_app_protocol.OpenAPIProtocol|str =openai_app_protocol.OpenAIAPIProtocol.CHAT_COMPLETIONS,
) -> typing.Any:
    """造请求格式化器。

    :param provider: provider 名，作为分类维度透传。
    :param tool_args_compression_limit: 工具参数压缩阈值；当前仅保存。
    :param model_name: 模型名；当前仅保存。
    :param api_protocol: 接口协议（``responses`` / ``chat_completions`` …）；
        当前仅保存。
    :param system_prompt: 系统提示；``None`` 表示不注入，留给调用方后填。
    :param extra: 调用方额外关键字（如 ``agent_name``），收下并忽略。
    """
    if provider_name is None:
        raise ValueError("provider_name must be provided")
    if not isinstance(provider_name, str):
        raise ValueError("provider_name must be str")

    provider_name = provider_name.lower()
    if not provider_name:
        raise ValueError("provider_name must be str")
    config= runtime_configuration.LLMRuntimeConfiguration(
        provider= provider_name,
        api_protocol= api_protocol,
        model_name= model_name or "default",
    )

    factory = provider_factory.LLMProviderFactory()
    provider= factory.create_formatter_adapter(provider_name, config)

    formatter = provider.create_formatter(
        tool_args_compression_limit=tool_args_compression_limit,
        model_name=model_name,
    )
    return formatter
