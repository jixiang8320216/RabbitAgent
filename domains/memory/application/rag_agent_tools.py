"""把 RAG 能力挂进 agent 的工具集。

与 :mod:`domains.memory.application.memory_agent_tools` 对称的设计：
RAG 对 agent 暴露为**一个工具**（``rag``），用 ``action`` 参数表达
「加文档 / 查一下 / 问答 / 统计」。

**为什么是单工具**：RAG 的操作种类也多（入库、检索、问答、清库），
拆成多个工具会挤占模型的工具预算。

**与记忆工具的分工**：记忆管的是「agent 自己的经历与偏好」，RAG 管的是
「外部知识库」。两者是互补关系，不是替代关系。

**默认不装配**：RAG 依赖外部 Qdrant 服务。装配链在
:func:`register_rag_tools` 里对「Qdrant 不可用」的情况直接放弃注册
（只记一条 debug 日志），而不是注册一个必然失败的工具让模型反复尝试。
"""

from __future__ import annotations

import logging
import typing

logger = logging.getLogger(__name__)

#: RAG 工具在工具集里的名字
RAG_TOOL_NAME = "rag"


def register_rag_tools(
    toolkit: typing.Any,
    settings: typing.Mapping[str, typing.Any] | None = None,
) -> typing.Any | None:
    """把 RAG 工具注册进工具集。

    :param toolkit: 目标工具集
    :param settings: RAG 运行参数（见 ``rag_config_rules.resolve_rag_settings``）；
                     为 ``None`` 或 ``enabled`` 为假时不注册。
    :return: 注册成功的工具对象；未注册时返回 ``None``

    注册失败只记日志 —— RAG 是**可选能力**，不该让 agent 装配失败。
    """
    if toolkit is None:
        return None

    if not settings or not settings.get("enabled"):
        logger.debug("rag tools not enabled; skip registration")
        return None

    try:
        from domains.tool.builtin.rag_tool import RAGTool
    except Exception:  # noqa: BLE001 - 依赖缺失时降级为「无 RAG」
        logger.debug("rag tools unavailable: dependency import failed", exc_info=True)
        return None

    try:
        tool = RAGTool(
            knowledge_base_path=settings.get("knowledge_base_path") or "./knowledge_base",
            qdrant_url=settings.get("qdrant_url"),
            qdrant_api_key=settings.get("qdrant_api_key"),
            collection_name=settings.get("collection_name") or "rag_knowledge_base",
            rag_namespace=settings.get("namespace") or "default",
        )
    except Exception:  # noqa: BLE001
        logger.debug("rag tools unavailable: tool construction failed", exc_info=True)
        return None

    # RAGTool 内部初始化失败（典型：Qdrant 不可达）时，工具会处于
    # ``initialized=False`` 状态。此时**不注册**——注册一个必然返回
    # 「未正确初始化」的工具，只会让模型反复试错并浪费回合。
    if not getattr(tool, "initialized", False):
        logger.info(
            "RAG 工具未正确初始化（%s），跳过注册；请检查 Qdrant 服务与 embedding 配置",
            getattr(tool, "init_error", "未知原因"),
        )
        return None

    try:
        register = getattr(toolkit, "register_tool_function", None)
        if register is None:
            logger.debug("rag tools unavailable: toolkit has no register_tool_function")
            return None
        # 注册 ``execute`` 而不是 ``run``：toolkit 以 ``func(**kwargs)`` 调用
        # （``ToolRegistration.call`` → ``self.func(**kwargs)``），
        # 而 ``RAGTool.run`` 的签名是 ``run(parameters: dict)`` —— 传关键字会
        # 直接 TypeError。``execute(action, **kwargs)`` 才是与调用约定匹配的形状
        # （记忆工具注册的也是 ``execute``）。
        register(
            tool.execute,
            name=RAG_TOOL_NAME,
            description=(
                "RAG tool - retrieval augmented generation over a local knowledge base. "
                "Actions: add_document / add_text / ask / search / stats / clear."
            ),
            group="memory",
            source="builtin",
        )
    except Exception:  # noqa: BLE001
        logger.debug("rag tool registration failed", exc_info=True)
        return None

    logger.info("rag tool registered")
    return tool


__all__ = ["RAG_TOOL_NAME", "register_rag_tools"]
