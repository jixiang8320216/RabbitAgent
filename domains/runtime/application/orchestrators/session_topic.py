"""会话主题协调器（SessionTopicCoordinator）。

会话主题（TUI/CLI 侧显示的那一行标题）不是用户填的，而是**开场对话稳定后由
模型生成**的：收集默认 agent 上下文里的 user/assistant 消息，走一次
``usage_operation_kind="topic_generation"`` 的内部模型调用（该 kind 会跳过
thinking 遥测，见 ``provider_chat_model._INTERNAL_OPERATION_KINDS``），把结果
写进 ``app.topic`` 并通知 ``observer.on_topic_updated``。

``SessionOrchestrator`` 持有本协调器（``_topic_coordinator``）并直接暴露它的
``tasks`` 集合（``_topic_generation_tasks``）；``rabbit_application`` 的会话收尾
调 ``cancel_topic_generation`` 收敛在跑的任务——对应本类的 :meth:`cancel`。

三条铁律：

1. 主题是**锦上添花**：没模型、没消息、生成失败，一律静默跳过，绝不让一轮
   对话失败；
2. ``asyncio.CancelledError`` 必须透传——取消是上层会话收尾在说话；
3. 已有主题不重复生成（``has_existin_display_topic`` 短路），在跑的不重复排。
"""

from __future__ import annotations

import logging
import typing
import asyncio
import collections.abc
from pydantic import ConfigDict,BaseModel,Field

import domains.persistence.domain.store_ports as store_ports
import domains.observability.domain.rabbit_observer as rabbit_observer
import domains.persistence.application.persistence_plane as persistence_plane_module
import domains.agent.domain.entities.agent_instance as agent_instance_module
import domains.session.infrastructure.session_persistence as session_persistence_module
import domains.configuration.domain.model.app_config as application_configuration
import domains.agent.domain.aggregates.execution_context as request_execution_context
import domains.observability.application.emitter as emitter
import domains.observability.application.observability_context as observability_context
import domains.configuration.domain.model.llm_config as llm_config_module
import domains.runtime.application.topic_generator as topic_generator_module
logger = logging.getLogger(__name__)

__all__ = ["SessionTopicCoordinator", "SessionTopicApplication"]

_LOG_PREFIX = "[TOPIC] "
TopicMessage=dict[str, object]
# 主题生成的系统提示：要短、要名词性、只输出短语本身。
_TOPIC_SYSTEM_PROMPT = (
    "你是会话主题命名器。根据对话内容给出一个简短的主题标题，"
    "不超过 20 个字，只输出标题本身，不要解释、不要标点修饰、不要引号。"
)

# 主题最大长度（字符）。超了截断——UI 上一行放不下。
_TOPIC_MAX_CHARS = 30

# 送进提示词的对话转写最大长度。主题取决于开场，取头部即可。
_TRANSCRIPT_MAX_CHARS = 2000

# 每次收集送给模型的消息条数上限。
_DEFAULT_MESSAGE_LIMIT = 20

GenerateTopic=collections.abc.Callable[[str],collections.abc.Awaitable[None]]

def message_content_text(content:object)->str:
    if isinstance(content,str):
        return content
    if isinstance(content,list):
        parts: list[str]=[]
        for item in content:
            if isinstance(item,str):
                parts.append(item)
            elif isinstance(item,dict):
                text= item.get("text")
                if isinstance(text,str):
                    parts.append(text)
        return " ".join(parts)
    return str(content)if content else ""

def iter_topic_message_dicts(value:object)-> collections.abc.Iterable[dict[str, object]]:
    if isinstance(value,dict):
        yield value
        return
    if isinstance(value,(list,tuple)):
        for item in value:
            yield from iter_topic_message_dicts(item)

def normalize_topic_messages(messages: collections.abc.Iterable[object]) -> list[TopicMessage]:
    normalized: list[TopicMessage]=[]
    for message in messages:
        for candidate in iter_topic_message_dicts(message):
            role= candidate.get("role")
            if role not in {"user","assistant"}:
                continue
            content= message_content_text(candidate.get("content")).strip()
            if content:
                normalized.append({"role":role,"content":content})
    return normalized

def event_to_topic_message(event: dict[str, object]) -> TopicMessage|None:
    event_type=event.get("type")
    role= event.get("role")
    if role not in {"user","assistant"}:
        if event_type in {"user","user-message","user_message"}:
            role="user"
        elif event_type in {"agent","agent-message","agent_message","assistant-message","assistant_message"}:
            role="assistant"
    if role not in  {"user","assistant"}:
         return None
    content= message_content_text(event.get("content") or event.get("text")).strip()
    if not content:
        return None
    return {"role": role,"content": content}


class SessionTopicApplication(typing.Protocol):
    current_default_agent_id:str|None
    agent_instances:dict[str, agent_instance_module.AgentInstance]
    persistence_plane: persistence_plane_module.PersistencePlane|None
    session_persistence: session_persistence_module.SessionPersistence|None
    config : application_configuration.AppConfig
    tags:dict[str, str]
    topic:str |None
    session_type:str |None
    observer: rabbit_observer.RabbitObserver

class TopicSeed(BaseModel):

    model_config = ConfigDict(extra="forbid", forzen=True,strict= True)
    content:str =Field(min_length=1)

    def to_message(self)->TopicMessage:
        return {"role":"user", "content": self.content}

class SaveAppState(typing.Protocol):
    async def __call__(self, session_id:str, *, preserve_updated_at:bool=False) -> None:...

class SessionTopicCoordinator:

    def __init__(self, app: SessionTopicApplication)->None:
        self._app = app
        self.tasks: set[asyncio.Task[None]]=set()

    # --- 消息收集 -----------------------------------------------------------

    async def collect_messages(
        self,
        session_id:str,
    ) -> list[TopicMessage]:
        messages:list[object]=[]
        default_id = self._app.current_default_agent_id
        if default_id and default_id in self._app.agent_instances:
            instance= self._app.agent_instances[default_id]
            try:
                msg_objects = await instance.context.get_messages()
                messages=[messages.to_dict() for messages in msg_objects]
            except Exception as exc:
                logger.exception(exc)
        normalized_messages= normalize_topic_messages(messages)
        if normalized_messages:
            return normalized_messages
        persistence_plane = self._app.persistence_plane
        event_store = persistence_plane.session_event if persistence_plane is not None else None
        if event_store is None:
            return normalized_messages
        try:
            if isinstance(event_store, store_ports.IndexedSessionEventStore):
                events=await event_store.read_by_event_type(
                    session_id,
                    evnet_types=("user","user_message","agent","assistant","agent_response"),
                )
            else:
                events= await event_store.read(session_id)
        except Exception as exc:
            return  normalized_messages
        fallbck_messages=[
            messages
            for event in events
            if isinstance(event,dict)
            for message in [event_to_topic_message(event)]
            if message is not None
        ]
        if fallbck_messages:
            return fallbck_messages
        return normalized_messages

    # --- 主题查询 -----------------------------------------------------------

    async def has_existin_display_topic(self, session_id:str) -> bool:
        tags=dict(self._app.tags)
        live_topic =tags.get("display_topic") or self._app.topic
        live_session_type = self._app.session_type
        if live_topic and live_session_type:
            return True
        persistence_plane = self._app.persistence_plane
        app_state_store = persistence_plane.app_state if persistence_plane is not None else None
        if app_state_store is None:
            return bool(live_session_type and live_topic)
        try:
            persisted = await app_state_store.load(session_id)
        except Exception as exc:
            logger.exception(exc)
            return bool(live_session_type and live_topic)
        if not persisted:
            return bool(live_session_type and live_topic)
        persisted_topic = persisted.tags.get("display_topic") or persisted.topic
        persisted_session_type = persisted.session_type
        return bool(persisted_topic and persisted_session_type)


    # --- 后台任务管理 -------------------------------------------------------

    def schedule(self, session_id: str, generate_topic:GenerateTopic) -> None:
        tags = dict(self._app.tags)
        has_topic=  bool(tags.get("display_topic") or self._app.topic)
        has_session_type=bool(self._app.session_type)
        if has_topic and has_session_type:
            logger.debug("topic session had topic and session type")
            return
        if any(not task.done() for task in self.tasks):
            logger.debug("%stopic generation already in flight; skip", _LOG_PREFIX)
            return

        task = asyncio.create_task(generate_topic(session_id))
        self.tasks.add(task)
        task.add_done_callback(self.discard_task)

    def discard_task(self, task: asyncio.Task[None]) -> None:
        self.tasks.discard(task)




    async def cancel(self) -> int:
        """取消所有在跑的主题生成任务，返回取消个数。

        供会话收尾使用（``rabbit_application`` → ``SessionOrchestrator.
        cancel_topic_generation``）：会话都要结束了，没必要再烧一次 LLM。
        """
        tasks =tuple (task for task in self.tasks if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)
    async def generate_and_save(
        self,
        session_id: str,
        *,
        seed: TopicSeed,
        topic_generator_factory: collections.abc.Callable[
            [llm_config_module.LLMConfig],
            topic_generator_module.TopicGenerator,
        ],
        save_app_state: SaveAppState,
        llm_config_override:llm_config_module.LLMConfig |None = None,
    ) -> None:
        if not self._app.session_persistence:
            return

        try:
            if await self.has_existin_display_topic(session_id):
                return
            topic_generator = topic_generator_factory(llm_config_override or self._app.confg.llm_config)
            previous_invocation_id =request_execution_context.get_current_invocation_id()
            request_execution_context.set_current_invocation_id(None)
            try:
                topic=await topic_generator.generate_topic([seed.to_message() ],session_id=session_id)
            finally:
                request_execution_context.set_current_invocation_id(previous_invocation_id)

            if not topic:
                return None
            preserved_topic = await self._existing_display_topic(session_id)
            selected_topic = preserved_topic or topic.topic
            self._app.topic = selected_topic
            if preserved_topic:
                self._app.tags["display_topic"] = preserved_topic
            if not self._app.session_type:
                self._app.session_type = topic.session_type
                observability_context.set_session_type(topic.session_type)
                emitter.emit_session_type_updated(
                    session_id=session_id,
                    session_type=topic.session_type,
                    topic=selected_topic,
                    source="auto",
                )
            await save_app_state(session_id, preserve_updated_at=True)
            try:
                await self._app.observer.on_topic_updated(session_id=session_id, topic=selected_topic)
            except Exception as exc:
                logger.exception(exc)
        except Exception as exc:  # noqa: BLE001 - 主题生成是旁路能力，绝不外抛
            logger.debug("%stopic generation degraded: %s", _LOG_PREFIX, exc)
            return


    async def _existing_display_topic(self, session_id:str) -> str | None:
        live_topic =self._app.tags.get("display_topic") or self._app.topic
        if live_topic :
            return live_topic
        persistence_plane = self._app.persistence_plane
        store= persistence_plane.app_state if persistence_plane is not None else None
        if store  is None:
            return None
        persisted= await store.load(session_id)
        if persisted is None:
             return None
        return persisted.tags.get("display_topic") or persisted.topic
