import asyncio
import contextvars
import hashlib
import inspect
import json
import logging
import threading
import time
import uuid
import typing

import domains.agent.domain.aggregates.react_agent as react_agent
import domains.agent.domain.value_objects.message as message_module
import domains.agent.domain.aggregates.cancellation as cancel_scope
import domains.persistence.domain.conversation_branch as branch_domain
import domains.permission as confirmation
import domains.agent.domain.aggregates.execution_context as request_execution_context
import domains.runtime.application.orchestrators.session_turn_contracts as turn_contracts
import domains.configuration.domain.runtime_state.prompt_expansion as prompt_expansion_module
import domains.agent.domain.aggregates.scope_context as scope_context
import domains.agent.domain.aggregates.turn_identity as turn_identity
import domains.tool.scheduling.delivery_context as delivery_context
import domains.agent.domain.aggregates.hook_context as infrastructure
import domains.configuration.domain.runtime_state.turn_completion as turn_completion_module
import domains.agent.domain.value_objects.conversation_context as conversation_context_module
import domains.agent.domain.entities.input_overflow_result as input_overflow_result_module
import domains.observability.application.tracking as tracking
import domains.observability.application.telemetry_policy_state as telemetry_configuration
import domains.agent.domain.aggregates.react_agent as react_agent_module
logger = logging.getLogger(__name__)


class TurnContextResetError(RuntimeError):

    def __init__(self, session_id:str):
        super().__init__(f"session id is {session_id}")
        self.session_id = session_id


class TurnExecutionOwner(typing.Protocol):

    def _apply_model_override(
            self,
            entry_agent: react_agent_module.ReactAgent,
            model_index: int,
    ) -> react_agent_module.ReactAgent: ...


class SessionTurnExecutor:


    def __init__(
        self,
        app: turn_contracts.TurnExecutionApplication,
        owner: turn_contracts.TurnExecutionOwner,
     )->None:
        self.app = app
        self.owner = owner
        # 最近一次交给 _save_completed_turn 的完成事实记录。
        # _persist_completion_boundary 只把 callback 传给 _record_context_persistence，
        # 这里留一份让它可以补回 witness，而不必改签名。
        self._last_completion_witness: turn_completion_module.TurnCompletionWitness | None = None
        # 已回执过的异步投递 id：重试/重入路径下避免重复确认。
        self._acked_delivery_ids: set[str] = set()


    def  _register_turn_scope(
         self,
         user_msg:message_module.Msg,
         session_id:str,
         )-> cancel_scope.TurnCancelScope:
        """为一次用户消息创建轮次作用域，并登记到中断管理器。

        返回的作用域有三个去向：

        - :meth:`handle_user_message` 用 ``terminated`` 判断本轮是否已被取消，
          并在 ``finally`` 里经 ``InterruptManager.clear_turn`` 注销；
        - 中断发起方（ESC / Ctrl+C）通过 ``InterruptManager.get_turn_scope``
          拿到它并调用 ``cancel()``，正在等待的代码即可感知；
        - :meth:`_invoke_agent` 把它放进上下文，供 hook / tool 检查取消点。

        作用域挂在**会话级父作用域**下：取消整个会话会联动取消其下所有轮次，
        而中断单个轮次不会影响会话本身。

        :param user_msg: 本轮用户消息；若其 ``metadata`` 已带 ``turn_id`` 则直接
            沿用，避免与上游已落库的轮次标识脱节。
        :param session_id: 会话标识。
        :return: 已登记的轮次作用域。
        """

        session_scope = confirmation.InterruptManager.get_session_scope(session_id)
        message_id = user_msg.id or str(id(user_msg))
        turn_scope = cancel_scope.TurnCancelScope(session_scope, message_id= message_id)
        try:
            confirmation.InterruptManager.register_turn(session_id, turn_scope)
        except ValueError:
            turn_scope.fail(reason="session id is invalid")
            raise
        return turn_scope

    def _prepare_entry_agent(
        self,
        session_id:str,
        model_index_value:object,
    )->tuple[react_agent.ReActAgent,int|None]:
        self.owner._bind_plugin_request_context()
        request_execution_context.set_root_agent_id(None)
        model_index= typing.cast(int|None, model_index_value)
        if model_index is not None:
            logger.info(f"[TURN-SCOPE] model_index={model_index}")
        default_agent_id = self.app.current_default_agent_id
        if default_agent_id is None:
            raise RuntimeError("Cannot handle a user message before a default agent")
        entry_agent= self.app.build_agent_for_invoke(default_agent_id)
        if model_index is not None:
            entry_agent =self.owner._apply_model_override(entry_agent, model_index)
        return entry_agent, model_index


    async def handle_user_message(self, user_msg:message_module.Msg, session_id:str, **kwargs:object)-> message_module.Msg:
        # 中断注册
        turn_scope = self._register_turn_scope(user_msg, session_id) #为本次用户消息创建TurnCancelScope，挂到会话级Scope下
        try:
            # 获取入口agent
            entry_agent,model_index = self._prepare_entry_agent(session_id, kwargs.get('model_index'))
            self.app.agent_tree.bind_scope(entry_agent.name, turn_scope)
            admission_callback = self._turn_admission_callback(kwargs.get("turn_admitted_callback"))
            if admission_callback is not None:
                await admission_callback()
            turn_id = self.owner._prepare_turn_identity(user_msg, session_id)
            await self.owner._publish_turn_checkpoint(
                session_id,
                turn_id=turn_id,
                boundary= branch_domain.CheckpointBoundary.BEFORE_ROOT_TURN,
                user_msg=user_msg,
            )
            expansion= self._prompt_expansion(kwargs.get("prompt_expansion"))
            context_persistence_callback = self._context_persistence_callback(
                kwargs.get("context_persistence_callback")
            )
            prepared_user_message_callback = self._prepared_user_message_callback(
                kwargs.get("prepared_user_message_callback")
            )
            #前置Hook
            user_msg, blocked_response, async_delivery_ids = await self.owner._run_prompt_hooks(
                entry_agent=entry_agent,
                user_msg=user_msg,
                session_id=session_id,
                expansion=expansion,
                model_index=model_index,
            )

            if blocked_response is not None:
                witness =(
                    None
                    if context_persistence_callback is not None
                    else self._blocked_completion_witness(user_msg, turn_id)
                )
                await self._persist_completion_boundary(
                    session_id=session_id,
                    witness=witness,
                    callback=context_persistence_callback,
                )
                turn_scope.complete()
                return blocked_response
            # 输入溢出检查
            user_msg= await self._offload_user_input(user_msg, kwargs.get("user_input_offload_callback"))
            self._bind_turn_message_metadata(user_msg, turn_id)
            user_msg = await self._prepare_user_message(
                user_msg,
                prepared_user_message_callback,
            )
            await self._persist_admitted_context(
                session_id,
                entry_agent,
                user_msg,
                context_persistence_callback,
            )

            self._start_query_tracking(user_msg,session_id)
            #调用 agent
            response= await self._invoke_agent(
                entry_agent=entry_agent,
                user_msg=user_msg,
                session_id=session_id,
                turn_id=turn_id,
                turn_scope=turn_scope,
                model_index=model_index,
            )

            await self._persist_completed_turn(
                session_id=session_id,
                turn_id=turn_id,
                async_delivery_ids=async_delivery_ids,
                model_index=model_index,
                context_persistence_callback=context_persistence_callback,
                entry_agent=entry_agent,
                user_msg=user_msg,
            )

            if not turn_scope.terminated:
                turn_scope.complete()
            return response
        except asyncio.CancelledError:
            if not turn_scope.terminated:
                turn_scope.cancel(reason="cancelled_error_raised")
            raise
        except Exception as exc:
            if not turn_scope.terminated:
                turn_scope.failed(reason=f"{type(exc).__name__}:{exc}")
            raise
        finally:
            confirmation.InterruptManager.clear_turn(session_id,turn_scope)


    async def _invoke_agent(
        self,
        entry_agent:react_agent.ReActAgent,
        user_msg: message_module.Msg,
        session_id: str,
        *,
        turn_id:str,
        turn_scope: cancel_scope.TurnCancelScope,
        model_index: int|None
    )-> message_module.Msg:
        scope_token = scope_context.set_turn_scope_context(turn_scope)
        turn_identity_token = turn_identity.set_turn_id(turn_id)
        delivery_context.bind_user_message_metadata(getattr(user_msg, "metadata", None))
        previous_hook_turn_id = infrastructure.get_hook_turn_id()
        infrastructure.set_hook_turn_id(str(uuid.uuid4()))
        try:
            response = await entry_agent(user_msg)
            # 后置hook
            response = await self.owner._run_turn_completion_hooks(
                entry_agent=entry_agent,
                response=response,
                session_id=session_id,
                model_index=model_index,
            )
            self._end_query_tracking_ok(session_id)
            return response
        except asyncio.CancelledError:
            if not turn_scope.terminated:
                turn_scope.cancel(reason="cancelled_error_raised")
            self._end_query_tracking_cancelled(session_id)
            raise
        except Exception as exc:
            if not turn_scope.terminated:
                turn_scope.fail(reason=f"{type(exc).__name__}:{exc}")
            await self._handle_turn_failure(entry_agent, session_id, exc, model_index=model_index)
            raise
        finally:
            self._clear_turn_context(
                session_id,
                scope_token=scope_token,
                turn_identity_token=turn_identity_token,
                previous_hook_turn_id=previous_hook_turn_id,
            )

    # --- 回调归一化 -------------------------------------------------------

    @staticmethod
    def _as_async_callback(callback: object, *, name: str)-> typing.Any:
        """把可选回调归一成「可 await、只收关键字参数」的调用。

        宿主传进来的回调可能是协程函数、普通函数（返回 awaitable 或普通值）、
        或者实现了 ``__call__`` 的对象；统一包装后调用点不必到处分支判断。
        """
        if callback is None:
            return None
        if not callable(callback):
            logger.warning("[TURN] %s is not callable, ignored", name)
            return None
        if inspect.iscoroutinefunction(callback):
            return callback

        async def _invoke(**payload: object)-> typing.Any:
            result = callback(**payload)
            if inspect.isawaitable(result):
                result = await result
            return result

        return _invoke

    def _turn_admission_callback(self, value: object)-> turn_contracts.TurnAdmissionCallback:
        """轮次准入回调包装。

        准入回调在「拿到入口 agent、但还没跑 hook」这一刻执行：宿主可以在这里做
        限流、配额、模型可用性校验。回调自己决定是否抛错/拒绝。
        """
        if value is None:
            return None
        if not callable(value):
            raise TypeError("[TURN_ADMISSION] %s is not callable, ignored", value)
        return typing.cast(turn_contracts.TurnAdmissionCallback, value)

    def _context_persistence_callback(self, value: object)-> turn_contracts.ContextPersistenceCallback:
        if value is None:
            return None
        if not callable(value):
            raise TypeError("[TURN_ADMISSION] %s is not callable, ignored", value)
        return typing.cast(turn_contracts.ContextPersistenceCallback, value)

    def _prepared_user_message_callback(self, value: object)-> turn_contracts.PreparedUserMessageCallback:
        """用户消息准备回调包装：消息落库前给宿主最后一次改写机会。"""
        if value is None:
            return None
        if not callable(value):
            raise TypeError("[TURN_ADMISSION] %s is not callable, ignored", value)
        return typing.cast(turn_contracts.PreparedUserMessageCallback, value)

    def _prompt_expansion(self, value: object)-> prompt_expansion_module.PromptExpansion|None:
        """PromptExpansion 归一化。

        入参可能是模型实例、等价的映射，或者干脆没传。无法归一（例如字段不合法）
        时返回 ``None`` 而不是抛错——按「本次没有展开」处理，turn 照常继续。
        """
        if value is None:
            return None
        if not isinstance(value, prompt_expansion_module.PromptExpansion):
            raise TypeError("[TURN_EXPANSION] %s is not callable, ignored", value)
        return value

    # --- 拦截 / 持久化边界 ------------------------------------------------

    def _blocked_completion_witness(
        self,
        user_msg: message_module.Msg,
        turn_id: str,
    )-> turn_completion_module.TurnCompletionWitness:
        """构造「被前置 hook 拦截」时的完成 witness。

        witness 是给持久化层的一小段事实记录：这一轮**没有**进入 agent，而是被
        hook 挡下了。没有它，回放时无法区分「模型没说话」和「压根没调模型」。
        """
        try:
            return turn_completion_module.TurnCompletionWitness.blocked(
                context_message_id = user_msg.id,
                turn_id = turn_id,
            )
        except  Exception as exc:
            raise turn_contracts.TurnContextPersistenceIndeterminateError(
                "blocked_completion_witness"
            )

    @staticmethod
    def _context_completion_witness(
        entry_agent: react_agent.ReActAgent,
        user_msg: message_module.Msg,
        turn_id: str,
    )-> turn_completion_module.TurnCompletionWitness:
        """构造「agent 已产出回复」时的完成 witness。

        与 :meth:`_blocked_completion_witness` 的区别：本方法要额外钉住**这一轮是在
        什么样的上下文前缀上跑出来的** —— ``context_message_conut`` 记下上下文长度，
        ``context_prefix_digest`` 记下「到用户消息为止」这段前缀的 SHA-256。
        回放或续跑时用它可以判断上下文有没有被压缩/裁剪过；只比对消息条数是不可靠的
        （压缩后条数可能恰好相同）。

        取不到上下文（agent 未暴露 ``context``）、用户消息不在上下文里、或字段不合法
        时返回 ``None`` 而非抛错：witness 只是一段事实记录，缺了它顶多让校验变弱，
        不该让已经拿到回复的用户这一轮整个失败。失败原因一律记 warning，
        便于事后区分「没有上下文」和「上下文对不上」。

        :param entry_agent: 入口 agent，取其 ``context`` 与 ``name``。
        :param user_msg: 本轮用户消息；前缀在它这里截断（含它自身）。
        :param turn_id: 轮次标识。
        :return: witness；无法构造返回 ``None``。
        """
        try:
            state = conversation_context_module.ContextState.model_validate(
                entry_agent.context.state_dict()
            )
            return turn_completion_module.TurnCompletionWitness.for_context(
                context_message_id = user_msg.id,
                turn_id = turn_id,
                agent_id = entry_agent.name,
                state = state,
            )
        except Exception as exc:
            raise turn_contracts.TurnContextPersistenceIndeterminateError(
                "completion_witness"
            )

    @staticmethod
    def _snapshot_agent_context(
        entry_agent: react_agent.ReActAgent,
    )-> conversation_context_module.ContextState|None:
        """给 agent 的上下文拍一张状态快照。

        ``ContextState.model_validate`` 接受带 ``state_dict()`` 的上下文对象、等价映射
        或裸 content 序列，所以这里直接把 agent 的 ``context`` 交给它。快照失败只记日志
        —— 上下文拿不到不等于这一轮失败。
        """
        context = getattr(entry_agent, "context", None)
        if context is None:
            logger.warning("[TURN] entry agent exposes no context, witness will be degraded")
            return None
        try:
            return conversation_context_module.ContextState.model_validate(context)
        except Exception as exc:
            logger.warning("[TURN] failed to snapshot agent context: %s", exc)
            return None

    @staticmethod
    def _context_prefix_digest(
        state: conversation_context_module.ContextState,
        message_id: str,
    )-> str|None:
        """计算「到用户消息为止」的上下文前缀摘要。

        摘要把 marks 也算进去：marks 承载压缩边界、裁剪标记等上下文级元数据，
        它们同样影响模型实际看到的内容，只摘要消息体会漏掉这层差异。

        :param state: 上下文快照。
        :param message_id: 截断位置（含该消息自身）。
        :return: 64 位小写十六进制 SHA-256；无法计算返回 ``None``。
        """
        ids = [message.id for message in state.messages]
        if message_id not in ids:
            logger.warning(
                "[TURN] user message %r is absent from agent context, prefix digest skipped",
                message_id,
            )
            return None
        prefix = state.content[: ids.index(message_id) + 1]
        try:
            payload = json.dumps(
                [
                    [message.model_dump(mode="json"), list(marks)]
                    for message, marks in prefix
                ],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                default=str,
            )
        except Exception as exc:
            logger.warning("[TURN] failed to serialize context prefix: %s", exc)
            return None
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


    async def _persist_completion_boundary(
        self,
        *,
        session_id: str,
        witness: turn_completion_module.TurnCompletionWitness | None,
        callback: turn_contracts.ContextPersistenceCallback |None = None,
    )-> None:
        """写「完成」边界。

        优先交给宿主的持久化回调；没有回调时退化为发布一个
        ``AFTER_ROOT_TURN`` 检查点。任何失败都只记日志——持久化出问题不应该让
        已经拿到回复的用户再看到一个崩溃。
        """
        deferred_cancellation = await self._save_completed_turn(
            session_id,
            witness,
        )
        await self._record_context_persistence(callback)
        if deferred_cancellation is not None:
            raise deferred_cancellation

    async def _save_completed_turn(
        self,
        session_id: str,
        witness: turn_completion_module.TurnCompletionWitness|None,
    )-> asyncio.CancelledError|None:
        """保存「轮次已完成」这一事实，返回**被推迟的**取消信号。

        返回值是本方法的关键约定：如果持久化过程中被取消（``asyncio.CancelledError``），
        **不在这里抛出**，而是把它交回调用方。原因是调用方还有一步
        :meth:`_record_context_persistence` 要走——直接抛出会让宿主的上下文持久化回调
        永远得不到执行，会话就停在「轮次已完成但未记录」的半截状态。调用方跑完回调后
        再把取消信号原样抛回去。

        其它异常（检查点发布失败等）只记日志：这一轮已经拿到了模型回复，不能因为落盘
        失败让用户白问一次。

        :param session_id: 会话标识。
        :param witness: 完成事实记录；``None`` 表示本轮没有可记录的事实，直接跳过。
        :return: 被推迟的取消信号；正常完成时返回 ``None``。
        """
        save_task= asyncio.create_task(
            self.owner._save_session(
                session_id,
                turn_completion_witness=witness,
            )
        )
        deferred_cancellation: asyncio.CancelledError|None = None
        while True:
            try:
                await asyncio.shield(save_task)
                return deferred_cancellation
            except asyncio.CancelledError as exc:
                if save_task.cancelled():
                    raise turn_contracts.TurnContextPersistenceIndeterminateError(
                        "completed agent turn final persistence wa cancelled"
                    )
                deferred_cancellation = deferred_cancellation or exc
            except turn_contracts.TurnContextPersistenceIndeterminateError:
                raise
            except Exception as exc:
                raise turn_contracts.TurnContextPersistenceIndeterminateError(
                    "completed agent turn final persistence wa failed: %s", exc
                )

    async def _record_context_persistence(
        self,
        callback: turn_contracts.ContextPersistenceCallback|None,
    )-> None:
        """把完成事实交给宿主的上下文持久化回调。

        和检查点发布不同，这一步失败**会**抛出
        ``TurnContextPersistenceIndeterminateError``：回调一旦执行到一半，我们就无法判断
        上下文到底落盘了没有，会话处于「不确定」状态。静默吞掉会让后续轮次在一个可能
        错误的上下文上继续跑——这与 :meth:`_persist_admitted_context` 的处理保持一致。

        约定的回调签名::

            async def callback(*, witness: TurnCompletionWitness | None,
                               session_id: str | None) -> None

        回调也可以是同步函数（返回 ``None`` 或 awaitable 均可）。

        :param callback: 宿主回调；``None`` 表示宿主没有接管持久化，直接跳过。
        :param witness: 完成事实记录；省略时沿用最近一次 :meth:`_save_completed_turn`
            收到的 witness——``_persist_completion_boundary`` 只传了 callback。
        :param session_id: 会话标识，随回调一起下发。
        :raises asyncio.CancelledError: 取消不包装，原样透传。
        :raises TurnContextPersistenceIndeterminateError: 回调执行失败。
        """
        if callback is None:
            logger.debug("[TURN] no context persistence callback, completion not recorded")
            return
        try:
            await  callback()
        except Exception as exc:
            raise turn_contracts.TurnContextPersistenceCallbackError(
                "ROOT turn Context completion persistence is indeterminate"
            ) from exc

    async def _persist_admitted_context(
        self,
        session_id: str,
        entry_agent: react_agent.ReActAgent,
        user_msg: message_module.Msg,
        callback: turn_contracts.ContextPersistenceCallback |None,
    )-> None:
        """准入后持久化：把「已通过 hook、即将进入 agent」的上下文存下来。"""
        if callback is None:
            logger.debug("[TURN] no context persistence callback, admitted context skipped")
            return
        try:
            state = conversation_context_module.ContextState.model_validate(
                entry_agent.context.state_dict()
            )
            matches=[
                (message,marks)
                for message,marks in state.content
                if message.id == user_msg.id
            ]
            expected = conversation_context_module.ContextMessageState.from_message(
                user_msg
            )
            if not matches:
                await entry_agent.context.add(user_msg)
            elif len(matches) !=1 or matches[0] != (expected,[]):
                raise RuntimeError("turn admission Context identity is ambiguous")
            await self.owner._save_session(
                session_id,
                turn_admission_agent_id=entry_agent.name,
                preserve_updated_at = True,
            )
        except asyncio.CancelledError:
            raise
        except turn_contracts.TurnContextPersistenceIndeterminateError:
            raise
        except Exception as exc:
            raise turn_contracts.TurnContextPersistenceIndeterminateError(
                "ROOT turn Context admission persitence is indeterminate"
            ) from exc

    async def _persist_completed_boundary(
        self,
        *,
        session_id: str,
        witness: turn_completion_module.TurnCompletionWitness|None,
        callback: turn_contracts.ContextPersistenceCallback|None = None,
    )-> None:
        """写「轮次完成」边界（agent 已产出回复）。

        与 :meth:`_persist_completion_boundary` 只在边界语义上不同——那个用于「被前置
        hook 拦截、压根没进 agent」的场景，本方法用于「agent 已返回」的场景；两者共用
        同一组持久化原语（:meth:`_save_completed_turn` +
        :meth:`_record_context_persistence`）。

        顺序是有讲究的：先落检查点、再通知宿主回调，**最后**才把可能被推迟的取消信号
        抛回。取消优先于持久化异常（取消时持久化结果的对错已经不重要），但持久化异常
        也不会被取消信号吃掉——两者都不会静默丢失。

        :param session_id: 会话标识。
        :param witness: 完成事实记录；``None`` 表示本轮没有可记录的事实。
        :param context_persistence_callback: 宿主的上下文持久化回调；``None`` 时跳过通知。
        """
        deferred_cancellation = await self._save_completed_turn(session_id, witness)
        await self._record_context_persistence(
                callback
            )

        if deferred_cancellation is not None:
            raise deferred_cancellation


    def notify_user_turn_finalized(self, session_id: str) -> None:
        """通知「用户这一轮已经收尾」。

        :meth:`SessionOrchestrator._notify_user_turn_finalized` 会回调到这里。
        目前没有落地的消费者（UI 侧还没接「轮次结束」事件），所以这里只记录并在
        宿主/应用上探测一个可选回调：探测不到就静默返回。

        刻意做成**同步**：调用方没有 await 它，改异步会让通知被丢弃而没有任何
        报错。
        """
        logger.debug("[TURN] user turn finalized, session=%s", session_id)
        for source in (self.owner, self.app):
            hook = getattr(source, "on_user_turn_finalized", None)
            if not callable(hook):
                continue
            try:
                hook(session_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "[TURN] user turn finalized hook failed on %r: %s",
                    type(source).__name__,
                    exc,
                )

    async def _persist_completed_turn(
        self,
        session_id: str,
        *,
        turn_id: str,
        async_delivery_ids: tuple[str,...],
        context_persistence_callback: turn_contracts.ContextPersistenceCallback|None,
        entry_agent: react_agent.ReActAgent,
        user_msg: message_module.Msg,
        model_index: int | None = None,
    )-> None:
        """轮次完成持久化：写后置边界并带回异步投递标识。

        :param model_index: 本轮使用的模型序号。调用方（模型自动切换/重试）会传，
            当前持久化不区分模型，接收但不参与落盘——保留参数是为了让调用方不必
            按有无重试分两条路径调用。
        """
        witness=(
            None
            if context_persistence_callback is None
            else self._context_completion_witness(
                entry_agent=entry_agent,
                user_msg=user_msg,
                turn_id=turn_id,
            )
        )
        await self._persist_completion_boundary(
             session_id=session_id,
            witness=witness,
            callback=context_persistence_callback,
        )
        await self._ack_async_deliveries(session_id, async_delivery_ids)
        try:
            await self.app.request_pending_hook_rewake(session_id)
        except Exception:
            logger.exception("[TURN] failed pending hook rewake")
        self.owner._notify_user_turn_finalized(session_id)

    async def _ack_async_deliveries(
        self,
        session_id: str,
        delivery_ids: tuple[str,...]
    )-> None:
        """回执前置 hook 产生的异步投递。

        异步投递（后台交付、hook 里的延迟消息）不占用本轮返回值，它们由投递子系统在
        后台发出。轮次结束时回执一次，投递子系统才能把「已投递」从待办表里摘掉，
        避免下一轮重复投递。

        投递子系统目前还没有接线，所以这里是**探测式**调用：依次在宿主应用 ``app``、
        ``owner``、``delivery_context`` 模块上找 ``ack_async_deliveries``；一个都没有就
        记 debug 后返回。找到后以 ``(session_id, ids)`` 调用，同步/异步均可。

        异常只记日志（``CancelledError`` 除外，取消必须透传）——回执失败不能影响用户
        已经拿到的回复。同一个 id 只回执一次，重试路径下不会重复确认。

        :param session_id: 会话标识。
        :param async_delivery_ids: 投递 id；``None``/空串/空序列都视为「没有投递」。
        """
        lifecycle = self.app.hook_lifecycle
        if lifecycle is None or not delivery_ids:
            return
        try:
            await lifecycle._ack_async_deliveries(session_id, delivery_ids)
        except Exception:
            logger.exception("[TURN] failed pending hook rewake")


    def _resolve_delivery_acker(self)-> typing.Any:
        """探测可用的异步投递回执入口：宿主应用 → owner → ``delivery_context`` 模块。

        按这个顺序是因为宿主应用最清楚投递表在哪；``delivery_context`` 作为最后兜底，
        方便将来把回执能力直接做进上下文模块。都没有则返回 ``None``（调用方跳过）。
        """
        for source in (self.app, self.owner, delivery_context):
            acker = getattr(source, "ack_async_deliveries", None)
            if callable(acker):
                return acker
        return None

    async def _publish_checkpoint_best_effort(self, session_id: str, **payload: object)-> None:
        """在没有持久化回调时，尽力通过宿主发布检查点；失败只记日志。"""
        publisher = getattr(self.owner, "_publish_turn_checkpoint", None)
        if publisher is None:
            logger.debug("[TURN] host has no _publish_turn_checkpoint, checkpoint skipped")
            return
        try:
            result = publisher(session_id, **payload)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            logger.warning("[TURN] publish checkpoint failed: %s", exc)

    # --- 用户消息预处理 ----------------------------------------------------

    async def _offload_user_input(
        self,
        user_msg: message_module.Msg,
        callback_value: object,
    )-> message_module.Msg:
        """输入溢出卸载。

        超长输入（粘贴大文件、超长日志）在进入模型前会被宿主卸载成引用。回调可以
        直接改写消息并返回它，也可以只做副作用返回 ``None``（此时沿用原消息）。

        卸载失败**不打断本轮**：宁可把原始输入交给模型，也不要因为优化手段出错
        而让用户这一轮白问。
        """
        user_msg, overflow_result = self.owner._check_input_overflow(user_msg)
        if not overflow_result.offloaded:
            return user_msg
        callback = typing.cast(turn_contracts.InputOffloadCallback|None, callback_value)
        if callback is not None:
            try:
                 await callback(user_msg, overflow_result)
            except Exception as exc:
                logger.warning("[TURN] offload user input failed: %s", exc)
        return user_msg

    def _bind_turn_message_metadata(self, user_msg: message_module.Msg, turn_id: str)-> None:
        """把 turn id 写进用户消息元数据，供持久化与异步投递对齐同一轮。"""
        metadata = dict(user_msg.metadata or {})
        existing_turn_id = metadata.get("turn_id")
        if existing_turn_id is not None and existing_turn_id != turn_id:
            raise turn_contracts.TurnContextPersistenceIndeterminateError(
               "prepared user message turn identity changed"
           )
        metadata["turn_id"] = turn_id
        user_msg.metadata = metadata

    async def _prepare_user_message(
        self,
        user_msg: message_module.Msg,
        callback: turn_contracts.PerparedUserMessageCallback|None,
    )-> message_module.Msg:
        """用户消息预处理（附加引用、注入上下文等）。

        回调返回消息则以返回值为准，返回 ``None`` 视为「只做副作用，消息不变」。

        注意：这里是「**有**回调才调用」，判空方向与直觉相反，写反会在无回调时
        直接 ``await None``。
        """
        if callback is None:
            return user_msg
        produced = callback(user_msg)
        if inspect.isawaitable(produced):
            produced = await produced
        return produced if produced is not None else user_msg

    # --- 查询追踪 ----------------------------------------------------------

    def _query_tracking_store(self)-> dict[str, dict[str, object]]:
        """按会话记录 turn 起的追踪信息（延迟创建，避免要求固定 ``__init__``）。"""
        store = self.__dict__.get("_query_tracking")
        if store is None:
            store = {}
            self._query_tracking = store
        return store

    def _start_query_tracking(self, user_msg: message_module.Msg, session_id: str)-> None:
        """开始查询追踪：记录起点时间与消息标识。"""
        if not telemetry_configuration.is_telemetry_enabled():
            return
        try:
            tracking.start_query_tracking(
                conversation_id= session_id,
                app_mode = self.app.app_mode,
                query_text= user_msg.get_text_content(),
                session_type=self.app.session_type,
            )
        except Exception as exc:
            logger.warning("[TURN] start query tracking failed: %s", exc)

    def _end_query_tracking_ok(self, session_id: str)-> None:
        """追踪成功收尾。"""
        if not telemetry_configuration.is_telemetry_enabled():
            return
        try:
            tracking.end_query_tracking(status="ok")
        except Exception as exc:
            logger.warning("[TURN] end query tracking failed: %s", exc)

    def _end_query_tracking_cancelled(self, session_id: str)-> None:
        if not telemetry_configuration.is_telemetry_enabled():
            return
        try:
            tracking.end_query_tracking(status="cancel")
        except Exception as exc:
            logger.warning("[TURN] end query tracking failed: %s", exc)

    def _finish_query_tracking(self, session_id: str, *, outcome: str)-> None:
        """计算耗时并清理追踪记录；没有起点记录时静默跳过。"""
        entry = self._query_tracking_store().pop(session_id, None)
        if entry is None:
            logger.debug("[TURN] no query tracking started for session %r", session_id)
            return
        started_at = entry.get("started_at")
        elapsed = time.monotonic() - started_at if isinstance(started_at, float) else None
        logger.debug(
            "[TURN] session=%r turn %s in %.3fs (message_id=%s)",
            session_id,
            outcome,
            elapsed if elapsed is not None else float("nan"),
            entry.get("message_id"),
        )

    # --- 失败与上下文清理 --------------------------------------------------

    async def _handle_turn_failure(
        self,
        entry_agent: react_agent.ReActAgent,
        session_id: str,
        error: BaseException,
        *,
        model_index: int | None = None,
    )-> None:
        """失败处理：记日志、通知界面、尽力保存现场。

        这里**不允许再抛异常**——它是 ``except Exception`` 分支里的最后一步，
        再抛会盖掉真正的原始异常。
        """
        # 先把原始异常落到日志：下面的 hook 链路本身有缺口
        # （``_run_stop_failure_hook`` 历史上缺失过），一旦它抛错，原始异常就
        # 只剩一条 WARNING，排查时完全看不到真因。这里用 error 级别 + 完整
        # 堆栈，保证「turn 为什么失败」永远有据可查。
        logger.error("[TURN] turn failed: %r", error, exc_info=error)
        try:
            await self.owner._run_stop_failure_hook(
                entry_agent=entry_agent,
                session_id=session_id,
                error=error,
                model_index=model_index,
            )
        except Exception as exc:
            logger.warning("[TURN] turn failure failed: %s", exc)
        if not telemetry_configuration.is_telemetry_enabled():
            return
        try:
            error_type = type(error).__name__
            tracking.end_query_tracking(status="error",error_type=error_type)
        except Exception as exc:
            logger.warning("[TURN] turn failure failed: %s", exc)

    @staticmethod
    def check_input_overflow(
        user_msg: message_module.Msg,
    ) -> tuple[message_module.Msg, input_overflow_result_module.InputOverflowResult]:
        """输入溢出检查：估算用户消息规模并与阈值比较，返回 ``(消息, 结果)``。

        宿主目前没有接真实的卸载实现，阈值也取不到。这里按「**判不出来就当没
        溢出**」处理：卸载只是优化手段，绝不能因为估算失败把用户的输入拦下来。

        拿到阈值后（消息或其 metadata 上给 ``input_offload_threshold``）本函数会
        自动生效，调用方无需改动。
        """
        content = getattr(user_msg, "content", None)
        text: str | None
        if isinstance(content, str):
            text = content
        elif isinstance(content, (list, tuple)):
            text = "".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
        else:
            text = None

        threshold = getattr(user_msg, "input_offload_threshold", None)
        metadata = getattr(user_msg, "metadata", None)
        if threshold is None and isinstance(metadata, dict):
            threshold = metadata.get("input_offload_threshold")
        if not isinstance(threshold, int) or threshold <= 0:
            threshold = None

        return user_msg, input_overflow_result_module.InputOverflowResult.from_text(
            text, threshold
        )

    @staticmethod
    def _clear_turn_context(
        session_id: str,
        *,
        scope_token: contextvars.Token,
        turn_identity_token: contextvars.Token[str|None],
        previous_hook_turn_id: str | None = None,
    )-> None:
        """清理本轮置位的上下文（``finally`` 中调用）。

        与 :meth:`_invoke_agent` 的置位顺序**逆序**恢复。每步单独兜异常：这里是
        ``finally`` 路径，任何一步失败都不能影响后续复位，更不能盖掉主异常。
        """
        try:
            scope_context.reset_turn_scope_context(scope_token)
        except LookupError:
            logger.debug("[TURN] reset hook turn id failed", exc_info=True)
        except Exception as error:
            raise TurnContextResetError(session_id) from error
        turn_identity.reset_turn_id(turn_identity_token)
        delivery_context.clear_scheduled_delivery_context()
        infrastructure.set_hook_turn_id(previous_hook_turn_id)










