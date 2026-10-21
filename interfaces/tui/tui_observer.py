import collections
import dataclasses
import json
import logging
import typing
from collections.abc import Mapping
from typing import Deque,Dict,Optional,List,Any,cast
import os
import asyncio
from datetime import datetime, UTC
import time

from domains.events.application import EventProtocol
import domains.llm.provider.domain as provider_domain
from domains.observability.domain.rabbit_observer import RabbitObserver
from domains.plan.domain import execution_plan
from domains.configuration import get_user_config_dir
from domains.agent.domain.aggregates.execution_context import get_session_id
from interfaces.tui.widgets.tui_app import TUIApp
from interfaces.tui.display.application.application_display import ApplicationDisplay
from interfaces.tui.display.application.interaction_display import InteractionDisplay
from interfaces.tui.display.thinking.thinking_handler import ThinkingHandler
from interfaces.tui.display.relay.relay_handler import ReplayHandler
from textual.widgets import Static
from interfaces.tui.display.skills.skill_status_handler import SkillStatusHandler
from interfaces.tui.user_input.processor import process_user_input
from interfaces.tui.display.cli_theme import ERROR_COLOR,SUCCESS_COLOR
from interfaces.tui.display.render_utils import split_think_content
from rich.text import Text
from interfaces.tui.widgets.dinamic_spinner import DynamicSpinner
from domains.observability.domain.tool_result_projection import  StructuredToolResult
from domains.llm.provider.application.context.context_window_resolver import get_context_window_for_model

from interfaces.tui.display.tools import (
    BaseToolHandler,
    DefaultToolHandler,
    EditFileHandler,
    ExecuteCommandHandler,
    GenerateResponseHandler,
    GlobToolHandler,
    GrepToolHandler,
    KnowledgeRetrieveHandler,
    UpdateFileToolHandler,
    ViewTextFileHandler,
    WriteFileHandler,
)

logger = logging.getLogger(__name__)




@dataclasses.dataclass
class RetryState:
    """一次可重试失败的重试状态。

    原先这个类只有注解没有 ``@dataclasses.dataclass`` 装饰器：``attemps: int = 0``
    之类的语句只会在类体里生成普通类属性，类本身仍用默认的 ``object.__init__``，
    于是 ``RetryState(max_attempts=...)`` 这种带关键字实参的构造会抛 ``TypeError``。

    同时把 ``attemps`` / ``max_attemps`` 的历史拼写统一成 ``attempts`` /
    ``max_attempts``（英文正确拼写），与 ``render_retry_status`` 的形参一致——
    原来的 ``attemps``/``max_attemps`` 是拼写错误，且 dataclass 字段与调用处的
    关键字参数对不上号。
    """

    attempts: int = 0
    max_attempts: int = 10
    delay: float = 5.0
    error: str = ""
    error_type: str = ""
    start_time: str = ""


class TUIConsoleAdapter:

    def __init__(self,observer:"TUIObserver"):
        self._observer = observer
        self._buffer=[]
        self._console=None
        self._printed_texts:list[str]=[]
        self._capturing:bool=False

    def begin_command_capture(self) -> None:
        """打开「本次命令窗口」：重置去重基线。

        由 :meth:`TUIObserver.begin_command_output_capture` 调用。这里只置一个标志，
        真正的去重判定交给 :meth:`has_printed_message`。失败也不应阻断命令执行，故
        不抛异常。
        """
        self._capturing = True

    def has_printed_message(self, message: object) -> bool:
        """该消息是否已在本窗口打印过（命令结果去重用）。

        保守策略：拿不准一律返回 ``False``（与 ``has_printed_command_result`` 文档一致——
        漏判的代价是可能重复显示一行，误判的代价是用户什么都看不到，故宁可重复）。
        """
        return False


class TUIObserver(RabbitObserver):
    TOOL_RUNNING_UPDATE_INTERRVAL_SECONDS=1.0
    def __init__(
        self,
        command_registry=None,
        chat_history_manager=None,
        tui_application=None,
        project_home=None,
    ) -> None:
        self.app = TUIApp(command_registry,tui_application=tui_application)
        self.project_home = project_home
        self.session_id:Optional[str]=None
        self.current_model:Optional[str]=None
        self.active_skills:List[str]=[]
        self.app.set_status_context(working_directory=project_home)
        self.call_depth=0
        self.app_display=ApplicationDisplay(self.app)
        self.interact_display= InteractionDisplay(self.app)
        self.thinking_handler = ThinkingHandler(self.app)
        self.replay_handler = ReplayHandler(self.thinking_handler)

        self.console = TUIConsoleAdapter(self)
        self._agent_start_times:Dict[str,str]={}
        self._tool_start_times:Dict[str,str]={}
        self._tool_running_update_tasks: Dict[str,asyncio.Task[None]]={}
        self._retry_states : Dict[str,RetryState]={}
        self._last_input_tokens:int=0
        self._last_output_tokens:int=0
        self._last_max_tokens:int= 128000
        self._chat_history_manager= chat_history_manager

        self.tool_handlers:Dict[str,BaseToolHandler]={
            "execute_command":ExecuteCommandHandler(),
            "read_file": ViewTextFileHandler(),
            "write_file":WriteFileHandler(),
            "global_files":GlobToolHandler(),
            "grep_content":GrepToolHandler(),
            "generate_response":GenerateResponseHandler(),
            "knowledge_retrieve_tool": KnowledgeRetrieveHandler(),
            "edit_file":EditFileHandler(title="Edit File"),
            "update_file": UpdateFileToolHandler(),
        }
        self.default_tool_handler = DefaultToolHandler("json",None)
        self.skill_status_handler = SkillStatusHandler(command_registry)

        self._live_static:Optional[Static]=None
        self._active_block_id:Optional[str]=None
        # 用户在 agent 执行期间提交的输入，先排队，等主循环下次 get_user_input 时取出
        self._pending_user_inputs:Deque[str]=collections.deque()
        self._replay_buffer_len:int=0
        self._stream_buffer:str =""
        self._thinking_buffer:str=""
        self._context_window_rendered:bool=False
        self._agent_replay_finalized:bool=False
        self._final_response_text:str=""

        self._current_user_input:Optional[str]=None
        self.tui_application = tui_application
        self._load_max_tokens_from_config()

        self.EPHEMERAL_EVENT_TYPES={
            "token-update",
            "thinking-content-update",
            "tool-call-streaming",
            "session-state",
            "heartbeat-response",
        }

        # # 由 TUIApplication 注入的依赖
        # self.command_registry = command_registry
        # self.chat_history_manager = chat_history_manager
        # self.tui_application = tui_application  # TUIApplication 引用，用于 ESC 中断等
        #
        # # 内部状态
        # self._buffer: list[str] = []
        # self._console = None
        # self._print_text: list[str] = []

    def show_welcome(self, session_id:str, model:str, skills:List[str])->None:
        self.session_id =session_id
        self.current_model = model
        self.active_skills = skills
        self.app.set_status_context(model_name=model,working_directory=self.project_home)

    def prepare_for_agent_turn(self,user_input:str):
        self._live_static=None
        self._active_block_id=None
        self._stream_buffer=""
        self._thinking_buffer=""
        self._context_window_rendered=False
        self._agent_reply_finalized=False
        self._final_response_text=""
        self._current_user_input= user_input

    async def on_error(self, source:str, error_msg:str, details: Optional[str]=None)->None:
        self._stop_active_live(transient=True)
        self.app_display.render_error(source,error_msg,details)

    async def on_command_warning(self, message:str)->None:
        self.app.add_widget(Static(Text(message,style=ERROR_COLOR)))

    async def _render_banner_after_ready(self, version:str)->None:
         await self.app.get_ready_event().wait()
         self._stop_active_live()
         self.app_display.render_banner(version)

    def _handle_app_result(self,t:asyncio.Task)->None:
        """TUI 主循环任务结束后的收尾判断。

        ``App.run_async()`` 返回的是 ``App.return_value``（即 ``exit(result=...)``），
        进程退出码要看 ``App.return_code``，两者不是一回事。这里两者都判：
        任一非零就记录原因并硬退出，避免 TUI 已死、进程还挂着。

        退出前调用 ``logging.shutdown()``：``os._exit`` 不走 atexit，
        不 flush 的话日志会丢在缓冲区里。
        """
        try:
            result=t.result()
        except asyncio.CancelledError:
            logger.info("CLI app task cancelled, shutting down")
            return
        except Exception:
            logger.error("CLI ap exists with error,exit!",exc_info=True)
            logging.shutdown()
            os._exit(1)

        return_code=0
        try:
            return_code=self.app.return_code or 0
        except Exception:
            logger.debug("failed to read app return code",exc_info=True)

        if return_code or result:
            logger.error(
                "CLI APP EXIST ABNORMALLY,exist! return_code=%s result=%r",
                return_code,result,
            )
            logging.shutdown()
            os._exit(return_code or 1)

    def _load_max_tokens_from_config(self) -> None:
        """
        从 ``tui_application.app_config.llm_config`` 读 max_tokens，更新 ``_last_max_tokens``。

        读取链任一环节缺失或类型不符时，保留 ``__init__`` 设的默认值（128000），
        不抛错——确保启动不因配置读取失败而中断。

        兼容 llm_config 是对象（属性访问）或 dict（键访问）两种形态，
        并尝试常见字段名：``max_tokens`` / ``context_window_size`` /
        ``max_context_tokens`` / ``context_tokens``。

        """
        try:
            config_path = get_user_config_dir()/"config.json"
            if config_path.exists():
                with open(config_path, "r",encoding="utf-8") as f:
                    config = json.load(f)
                    model_name =None
                    model_name=config.get("modelName")
                    if not model_name:
                        llm_configs = config.get("llm_configs", [])
                        if isinstance(llm_configs, dict):
                            selected_profile= provider_domain.select_preferred_active_mapping(llm_configs)
                            if selected_profile is not None:
                                model_name = selected_profile.get("modelName")

                    if model_name:
                        self._last_max_tokens = self._get_model_context_window(model_name)
                    else:
                        self._last_max_tokens = 128000
        except Exception:
            # 配置读取失败不影响启动，保留默认值
            pass

    def _stop_active_live(self, transient: bool=False)->None:
        """收起当前 live 流式渲染区，并复位流式状态。

        :param transient: ``True`` 表示整块移除（提示/ transient 区不该留在屏上）；
            ``False`` 表示只清空内容、保留占位。

        调用点：``get_user_input``（取输入前收起 live，避免与输入框抢屏）、
        ``_render_banner_after_ready``（渲染 banner 前清场）。
        任何异常都只记 debug——收起失败不该影响主循环。
        """
        if self._live_static :
            try:
                if self._active_block_id =="global_spinner" or transient:
                   self.app.remove_widget(self._live_static)
            except Exception:
                logger.debug("failed to stop active live block", exc_info=True)
            self._live_static = None
            self._active_block_id = None

    def push_pending_user_input(self, raw_input:str)->None:
        """排队一条用户输入（agent 执行期间用户提前提交时使用）。"""
        self._pending_user_inputs.append(raw_input)

    def _pop_pending_user_input(self)->Optional[str]:
        """取出最早一条排队输入；队列为空返回 ``None``。"""
        if not self._pending_user_inputs:
            return None
        return self._pending_user_inputs.popleft()

    async def get_user_input(self)->Optional[str]:
        if self._active_block_id !="agent_replay":
            self._stop_active_live(transient=True)
        try:
            queued_input = self._pop_pending_user_input()
            if queued_input is None:
                self.app._input_future = asyncio.Future()
                raw_input = await self.app._input_future
            else:
                raw_input = queued_input
            raw_input = raw_input.encode("utf-8","surrogatepass").decode("utf-8","replace")
            text= raw_input.strip()
            if not text:
                return None

            return process_user_input(text)
        except asyncio.CancelledError:
            return None

    def on_application_start(self,version:str) -> None:
        task = asyncio.create_task(self.app.run_async(mouse=True))
        task.add_done_callback(self._handle_app_result)

        asyncio.create_task(self._render_banner_after_ready(version))

    # --- 命令结果渲染 ----------------------------------------------------
    #
    # ``TUIApplication._handle_command`` 的一次执行是这样的：
    #
    #   await on_command_result_start()            # 结果区开演
    #   begin_command_output_capture()             # 打开「已打印」记录窗口
    #   result = await cmd.execute(...)            # 命令自己可能已经往屏上写了东西
    #   if not has_printed_command_result(result): # 写过就别再写一遍
    #       await on__command_result(result, include_title=False)
    #
    # 所以这四个方法是一条**去重**链路：命令可以自选是直接-print 还是返回结构化
    # 结果，界面上最终只出现一次。任何一步失败都不许把命令执行打断。

    async def on_command_result_start(self)->None:
        """命令结果渲染开始前的清场。

        收起可能残留的流式/思考 live 块（否则命令输出会跟正在流式的内容混在
        一起），并把上一轮命令的「已渲染」记录清掉——记录跨命令保留会让第二条
        命令的结果被误判成「已经打印过」而显示不出来。
        """
        title=f"[bold {SUCCESS_COLOR}]Command Result[/]"
        self.app.add_text(f"\n{title}")

    def begin_command_output_capture(self)->None:
        """打开「本条命令已经往屏上写过什么」的记录窗口。

        窗口只在本次**命令执行期间**有效：:meth:`on_command_result_start` 建、
        :meth:`has_printed_command_result` 查、:meth:`on__command_result` 记。

        这里同时记下聊天区此刻的子节点数量作为**基线**：之后做屏幕去重时只扫描
        基线之后新挂上的块。不设基线的话，上一条命令渲染过的同名文本会命中判据，
        导致用户重复执行同一条命令时"什么都没出来"。
        """
        if isinstance(self.console, TUIConsoleAdapter):
            self.console.begin_command_capture()

    def has_printed_command_result(self,message:object)->bool:
        """这条命令结果是否**已经**出现在屏幕上了。

        两个判据，命中任一即认为已打印：

        - 本次命令窗口内由 :meth:`on__command_result` 记录过的文本（窗口由
          :meth:`on_command_result_start` 重置，所以**去重只在本次命令内有效**）；
        - 聊天区里已经存在内容相同的 ``Static``——命令绕过 observer 自己 mount
          过的情形。

        **拿不准时一律返回 ``False``**：漏判的代价是重复显示一行，误判的代价是
        用户什么都看不到，所以宁可重复。
        """
        return isinstance(self.console,TUIConsoleAdapter) and self.console.has_printed_message(message)

    async def on__command_result(
        self,
        message:object,
        include_title:bool=True,
    )->None:
        if include_title:
            await self.on_command_result_start()
        self.app.add_text(message)

    @staticmethod
    def _command_result_text(result:object)->str:
        """把命令产物归一化为可比较、可渲染的文本；取不到返回 ``''``。

        ``result`` 在调用方已经被剥过一层 ``.message``，这里仍需兜住其他形态
        （``str`` / 带 ``message``/``text``/``content``/``output`` 的对象 / dict）。
        只认 **非空字符串**：命令返回的数字、None、空容器都不是可渲染的结果。
        """
        candidate:object = result
        if isinstance(candidate, Mapping):
            found: object = None
            for key in ("message","text","content","output"):
                value = candidate.get(key)
                if isinstance(value,str) and value.strip():
                    found = value
                    break
            candidate = found
        elif not isinstance(candidate,str):
            found = None
            for attr in ("message","text","content","output"):
                value = getattr(candidate,attr,None)
                if isinstance(value,str) and value.strip():
                    found = value
                    break
            candidate = found
        if candidate is None:
            return ""
        return str(candidate).strip()

    async def on_tool_call_start(
        self,
        agent_name:str,
        tool_name:str,
        args_summary:str,
        tool_id: typing.Optional[str]=None,
        timeout:typing.Optional[int]=None,
        cwd:typing.Optional[bool]=None,
        timestamp:typing.Optional[int]=None,
        thinking_operation_id: typing.Optional[str]=None,
        parent_instance_id:typing.Optional[str]=None,
        model_id: typing.Optional[str]=None,
        display_name:typing.Optional[str]=None,
    ) ->None:
        if not args_summary.strip():
            return
        block_id=f"tool_{tool_name}"
        self._prepare_live_block(block_id, transient=False)
        if tool_id:
            self._tool_start_times[tool_id] = timestamp or datetime.now().isoformat()
        handler= self._get_tool_handler(tool_name)
        renderable= handler.render_update(tool_name, args_summary, is_final=False, block_id=block_id)
        self._update_active_live(renderable)
        self._start_tool_running_updates(tool_name, args_summary, block_id=block_id, tool_id=tool_id)
        await self._persist_tool_call(
            agent_name,
            tool_name,
            True,
            args_summary,
            tool_id,
            timeout,
            cwd,
            timestamp,
            thinking_operation_id,
        )

    async def on_tool_call_end(
        self,
        agent_name:str,
        tool_name:str,
        tool_id:typing.Optional[str]=None,
        timestamp:typing.Optional[int]=None,
        error:typing.Optional[bool]=None,
        result_summary:typing.Optional[str]=None,
        parent_instance_id:typing.Optional[str]=None,
        status:typing.Optional[str]=None,
        model_id:typing.Optional[str]=None,
        display_name:typing.Optional[str]=None,
        external_data_cards: typing.Optional[list[dict[str,str|None]]]=None,
        structured_result: typing.Optional[StructuredToolResult]=None,
    )->None:
        self._cancel_tool_running_updates(tool_name,tool_id)
        duration_ms=0
        if tool_id and tool_id in self._tool_start_times:
            start_time =datetime.fromisoformat(self._tool_start_times[tool_id])
            end_time =datetime.fromisoformat(timestamp or datetime.now().isoformat())
            duration_ms = int((end_time - start_time).total_seconds()*1000)
            del self._tool_start_times[tool_id]
        await self._persist_tool_call(
            agent_name,
            tool_name,
            False,
            None,
            tool_id,
            None,
            None,
            timestamp,
            None,
            error,
            result_summary,
        )

        if self._live_static and self._active_block_id == f"tool_{tool_name}":
            handler= self._get_tool_handler(tool_name)
            current_renderable= cast(Any, self._live_static.content)
            fixed_renderable= handler.render_end(tool_name, duration_ms, error, current_renderable)
            self.app.safe_update(self._live_static, fixed_renderable)
        self._finalize_live_block(transient=False, print_newline=True)

    async def on_plan_update(self,
        agent_name:str,
        plan_subtasks: typing.List[execution_plan.Subtask],
        instance_id:typing.Optional[str]=None,
    ) ->None:
        subtasks=[
            {
                "name": task.name,
                "description": task.description,
                "state": task.state,
            }
            for task in plan_subtasks
        ]
        if plan_subtasks:
            plan_name= plan_subtasks[0].name if plan_subtasks else ""
            self.app.update_task_status_widget(plan_name, plan_subtasks)

    async def on_thinking_operation_start(self,
        agent_name:str,
        operation_id:str,
        initial_prompt: typing.Optional[str]=None,
        available_tools: typing.Optional[typing.List[str]]=None,
        instance_id:typing.Optional[str]=None,
        parent_instance_id:typing.Optional[str]=None,
        *,
        operation_kind: typing.Optional[str]=None,
        ) ->None:

        if self._should_ignore_thinking_udpate():
            return
        self._thinking_buffer = ""
        block_id="agent_replay_thinking"
        self._prepare_live_block(block_id, transient=True)
        spinner = self.thinking_handler.render_update(agent_name,"")
        self._update_active_live(spinner)
    async def on_thinking_operation_update(
        self,
        agent_name:str,
        operation_id:str,
        content:str,
        content_kind: Optional[str]=None,
        instance_id: Optional[str]=None,
        )->None:
        if not content.strip():
            return
        if self._should_ignore_thinking_udpate():
            return
        self._thinking_buffer += content

        block_id="agent_replay_thinking"
        self._prepare_live_block(block_id, transient=False)
        spinner = self.thinking_handler.render_update(agent_name,self._thinking_buffer)
        self._update_active_live(spinner)


    async def on_thinking_operation_end(
        self,
        agent_name:str,
        operation_id:str,
        final_content:typing.Optional[str],
        end_timestamp:str,
        input_tokens:int=0,
        output_tokens:int=0,
        reasoning_tokens:int=0,
        tool_call_count: int=0,
        has_tool_calls: bool=False,
        tool_call_ids: typing.Optional[typing.List[str]]=None,
        reasoning_content: typing.Optional[str]=None,
        cached_tokens:int=0,
        instance_id:typing.Optional[str]=None,
        mode_id:typing.Optional[str]=None,
        *,
        operation_kind: typing.Optional[str]=None,
    ) ->None:
        self._last_input_tokens = input_tokens
        self._last_output_tokens = output_tokens

        if input_tokens==0 and output_tokens==0 and final_content:
            estimate_tokens = self._estimate_tokens_from_text(final_content)
            self._last_output_tokens = estimate_tokens
        if self._active_block_id == "agent_replay_thinking":
            self._finalize_live_block(transient=True)
            self._thinking_buffer = ""

    async def on_tool_call_streaming(
        self,
        agent_name:str,
        tool_name:str,
        tool_id:str,
        operation_id: str,
        input_preview:str,
        input_stream_phase: typing.Optional[typing.Literal["identity","arguments"]]=None,
        input_fragment:typing.Optional[str]=None,
        input_fragment_index:typing.Optional[int]=None,
        input_fragment_kind: typing.Optional[str]=None,
        instance_id:typing.Optional[str]=None,
    )->None:
        pass

    async def on_agent_call_start(self,
       agent_name:str,
       task:str,
       instance_id: typing.Optional[str]=None,
       parent_id: typing.Optional[str]=None,
       agent:Optional[Any]|None=None,
        ):
        if self._active_block_id!="agent_replay":
            self._stop_active_live(transient=True)
        self._stream_buffer =""
        self._thinking_buffer =""
        self._context_window_rendered =False
        self._agent_replay_finalized = False
        self._final_response_text =""
        self.call_depth +=1
        indent =" "
        self.app.add_text(f"\n{indent}[bold magent]>{agent_name}[/][dim]Task: {task}[/]")
        self.app.update_task_status_widget(task,[])
        # 起点必须与 on_agent_call_end 传入的 end_timestamp 时区一致：调用方
        # （agent/service/agent_call_lifecycle_hooks.py:188）传的是
        # datetime.now(UTC)，带 tzinfo。若这里用 naive 的 datetime.now()，
        # on_agent_call_end 里 end_time - start_time 会抛
        # "can't subtract offset-naive and offset-aware datetimes"。
        self._agent_start_times[agent_name] = datetime.now(UTC).isoformat()
        self._check_and_start_global_spinner()

    async def on_agent_call_end(self,
        agent_name:str,
        end_timestamp:str,
        error:typing.Optional[str]=None,
        instance_id:typing.Optional[str]=None,
        parent_id:typing.Optional[str]=None,
        ) ->None:
        if self._active_block_id!="agent_replay":
            self._stop_active_live(transient=False)
        duration_ms=0
        if agent_name in self._agent_start_times:
            start_time =datetime.fromisoformat(self._agent_start_times[agent_name])
            end_time =datetime.fromisoformat(end_timestamp)
            # 两端可能一边带 tzinfo、一边不带（例如历史记录是 naive 本地时间）。
            # 直接相减会抛 "can't subtract offset-naive and offset-aware datetimes"，
            # 这里统一对齐到 UTC 后再算时长，观测失败不影响主流程。
            if (start_time.tzinfo is None) != (end_time.tzinfo is None):
                start_time = start_time.replace(tzinfo=UTC)
                end_time = end_time.replace(tzinfo=UTC)
            duration_ms = int((end_time - start_time).total_seconds()*1000)
            del self._agent_start_times[agent_name]
        if error:
            is_retryable,error_type, max_attempts, delay= self._is_retryable_error(error)
            if is_retryable:
                if agent_name not in self._retry_states:
                    self._retry_states[agent_name] = RetryState(
                        max_attempts=max_attempts, delay=delay, error_type=error_type, start_time=end_timestamp
                    )
                retry_state = self._retry_states[agent_name]
                retry_state.attempts+=1
                retry_state.error=error

                self.interact_display.render_retry_status(
                    agent_name=agent_name,
                    attemps=retry_state.attempts,
                    max_attempts=retry_state.max_attempts,                    delay=retry_state.delay,
                    error=error,
                    error_type=error_type,
                    depth=self.call_depth,
                )
                return
            else:
                self.interact_display.render_agent_call_end(agent_name, duration_ms,error,self.call_depth)
        self.call_depth =max(0, self.call_depth-1)
        if self.call_depth >0:
            self._check_and_start_global_spinner()
        else:
            if self._active_block_id == "agent_replay":
                self._stop_active_live(transient=True)


    def _update_active_live(self, renderable:Any)->None:
        if self._active_block_id == "global_spinner" and self._live_static:
            self.app.remove_widget(self._live_static)
            self._live_static = None
            self._active_block_id = None

        if not self._live_static:
            self._live_static = Static(renderable)
            self.app.add_widget(self._live_static)
        else:
            self.app.safe_update(self._live_static, renderable)


    async def on_token_update(
        self,
        agent_name:str,
        input_tokens:int=0,
        output_tokens:int=0,
        reasoning_tokens:int=0,
        cached_tokens:int=0,
        model:typing.Optional[str]=None,
    ) ->None:
        pass

    async def on_agent_text_stream(
        self,
        agent_name:str,
        text:str,
        timestamp:typing.Optional[str]=None,
        operation_id:typing.Optional[int]=None,
        instance_id:typing.Optional[str]=None,
    ) ->None:
        self._stream_buffer += text
        think,reply = split_think_content(self._stream_buffer)
        if think is not None and reply:
            self._thinking_buffer = think
            block_id ="agent_reply_thinking"
            if self._active_block_id and self._active_block_id !=block_id:
                self._stop_active_live(transient=False)
            self._active_block_id = block_id
            spinner = self.thinking_handler.render_update("Reply",self._thinking_buffer)
            self._update_active_live(spinner)
            return
        if reply:
            self._final_response_text =reply.strip()
            block_id ="agent_reply"
            if self._active_block_id and self._active_block_id !=block_id:
                is_prev_thinking=self._active_block_id == "agent_reply_thinking"
                self._stop_active_live(transient=is_prev_thinking)
            self._active_block_id = block_id
            self._agent_replay_finalized =False

            renderable= self.replay_handler.render_update(self._stream_buffer)
            self._update_active_live(renderable)

    async def on_agent_text_stream_end(
        self,
        agent_name:str,
        timestamp:typing.Optional[str]=None,
    ) ->None:
        if self._stream_buffer:
            await self._persist_agent_text_stream_complete(agent_name,self._stream_buffer, timestamp)
        self._thinking_buffer =""
        tokens = self._last_input_tokens
        max_tokens=self._last_max_tokens
        if tokens==0 and self._stream_buffer:
            estimated_tokens = self._estimate_tokens_from_text(self._stream_buffer)
            tokens =estimated_tokens
        self._finalize_agent_response_once(tokens,max_tokens, source="agent_text_stream_end", text=self._stream_buffer)

    def _finalize_agent_response_once(
        self,
        tokens:int,
        max_tokens:int,
        source:str,
        text:str="",
    )->None:
        if not self._live_static or self._active_block_id == "agent_replay":
            if text.strip():
                self._active_block_id = "agent_replay"
                self._agent_replay_finalized = True
                self._final_response_text = self._normalized_reply_text(text)
                self._update_active_live(self.replay_handler.render_update(text))
            else:
                return

        if text.strip():
            self._final_response_text = self._normalized_reply_text(text)
        if not self._agent_replay_finalized and self._live_static:
            duration_ms=0
            updated = self.replay_handler.render_end(duration_ms, self._live_static.content, tokens, max_tokens)
            self.app.safe_update(self._live_static, updated)
            self._agent_replay_finalized = True
        self._render_context_window_once(tokens, max_tokens, source=source)

    def _render_context_window_once(self, tokens:int, max_tokens:int, source:str)->None:
        if self._context_window_rendered or max_tokens<=0:
            return
        percentage = min(tokens/max_tokens*100,100)
        block=" "
        empty=" "
        total_blocks=20
        filled_blocks = int(total_blocks*percentage//100)
        empty_blocks= total_blocks - filled_blocks
        bar = block* filled_blocks+empty*empty_blocks
        context_text = Text(f"Context Window: {tokens}/{max_tokens} [{bar}] {percentage:1f}%",style="dim")
        self.app.add_widget(Static(context_text))
        self._context_window_rendered = True

    async def on_generate_response(
        self,
        agent_name:str,
        response: str,
        is_final: bool=False,
        instance_id:typing.Optional[str]=None,
    ) ->None:
        """渲染 agent 的最终回复。

        修改前
        ------
        本方法把回复交给 ``on_agent_replay_start`` / ``on_agent_replay_end``，
        而这两个方法**在 ``RabbitObserver`` 基类里只有空实现**（``pass``），
        本类也没有覆盖它们。于是模型答案被原样丢弃——

            _flush_final_response: final_content='# 使用帮助\n\n我是这个多智能体...'
            _flush_final_response: observer='CompositeRabbitObserver'
            _flush_final_response: -> observer.on_generate_response
            （屏上什么都没有）

        现象是「LLM 明明返回 200、回复文本也拿到了，屏上就是没有答案」。

        ``agent_call_lifecycle_hooks`` 的文档也印证了正确路径：
        「先 ``on_agent_text_stream`` 把正文推过去，再 ``on_agent_text_stream_end``
        让 TUI 把手上的 live 区定稿」——这条路径本类**确实实现了**
        （见 ``on_agent_text_stream`` / ``on_agent_text_stream_end``），
        里面才有 live 区管理、思考/回复分流、上下文窗口渲染。

        改为走这条真实渲染链路。
        """
        if not response or not response.strip():
            return
        await self.on_agent_text_stream(
            agent_name,
            response,
            timestamp=datetime.now().isoformat(),
            operation_id=instance_id,
            instance_id=instance_id,
        )
        if is_final:
            await self.on_agent_text_stream_end(
                agent_name,
                timestamp=datetime.now().isoformat(),
            )

    async def on_agent_text_stream_start(self, text:str)->None:
        if not text.strip():
            return
        if self._stream_buffer:
            return
        if self._is_duplicate_final_response(text):
            return
        think, reply = split_think_content(text)
        if think is not None and not reply:
            if not self._thinking_buffer:
                block_id = "agent_reply_thinking"
                self._prepare_live_block(block_id, transient=False)
                spinner = self.thinking_handler.render_update("Rabbit",think)
                self._update_active_live(spinner)
                return
        block_id = "agent_reply"
        if self._active_block_id and self._active_block_id != block_id:
            is_prev_thinking = self._active_block_id == "agent_reply_thinking"
            self._stop_active_live(transient=is_prev_thinking)
        elif self._active_block_id == block_id:
            self._live_static = None
            self._active_block_id = None


        if not self._stream_buffer:
            self._active_block_id = block_id
            self._agent_reply_finalized=False
            self._final_response_text = self._normalized_reply_text(text)
            rendearble = self.replay_handler.render_update(text)
            self._update_active_live(rendearble)

    async def on_agent_reply_start(self, *args,**kwargs):
           pass

    async def on_agent_reply_end(self, *args,**kwargs):
           pass

    async def on_agent_reply_end(
        self,
        text:str,
        end_timestamp:str,
        input_tokens: int=0,
        output_tokens: int=0,
        max_tokens:int=128000,
    )->None:
        is_agent_replay= self._active_block_id == "agent_replay"
        is_agent_replay_thinking = self._active_block_id == "agent_reply_thinking"

        if input_tokens==0 and output_tokens==0:
            input_tokens = self._last_input_tokens
            output_tokens = self._last_output_tokens
            max_tokens = self._last_max_tokens
        tokens= input_tokens

        if tokens==0 and text:
            estimate_tokens= self._estimate_tokens_from_text(text)
            tokens =estimate_tokens


        if self._is_duplicate_final_response(text) and self._context_window_rendered:
            return
        self._finalize_agent_response_once(tokens, max_tokens, source="agent_reply_end", text=text)

    def _prepare_live_block(self, block_id: str, transient: bool=False)->None:
        if self._active_block_id and self._active_block_id != block_id:
            self._stop_active_live(transient=transient)
        self._active_block_id = block_id

    def _finalize_live_block(self, transient: bool=False, print_newline: bool=False)->None:
        self._stop_active_live(transient=transient)
        self._active_block_id = None
        self._check_and_start_global_spinner()

    def _get_tool_handler(self, tool_name: str)->BaseToolHandler:
        return self.tool_handlers.get(tool_name, self.default_tool_handler)

    def _check_and_start_global_spinner(self)->None:
        if self.call_depth >0 and self._live_static is None:
            if self._live_static is None:
                self._live_static = DynamicSpinner()
                self.app.add_widget(self._live_static)
                self._active_block_id="global_spinner"

    @staticmethod
    def _normalized_reply_text(text:str)->str:
        _, reply= split_think_content(text)
        return reply.strip()

    def _is_duplicate_final_response(self, text:str)->bool:
        reply= self._normalized_reply_text(text)
        if not reply or not self._final_response_text:
            return False
        if reply != self._final_response_text:
            return False
        return self._agent_reply_finalized or self._context_window_rendered

    def _estimate_tokens_from_text(self,text:str)->int:
        if not text:
            return 0
        chinese_chars = sum(1 for c in text if "\u4e00"<=c<="\u9fff")
        other_chars =len(text)-chinese_chars
        estimated= int(chinese_chars*1.5)+int(other_chars/4)
        return max(estimated,1)

    def _is_retryable_error(self, error_msg:str)->tuple[bool,str,int,float]:
        error_msg_lower = error_msg.lower()
        if any(p in error_msg for p in ["429", "Rate limit", "Too many requests", "rate_limit"]):
            return (True,"rate_limit", 10,5.0)
        if any(p in error_msg_lower for p in ["timeout", "timed out"]):
            return (True,"timeout", 10,5.0)

        if any(p in error_msg for p in ["407", "Proxy Authentication"]):
            return (True,"proxy_auth", 3,2.0)

        if any(p in error_msg_lower for p in ["connection", "network", "unreachable"]):
            return (True,"connection", 5,3.0)
        return (False,"", 0,0.0)

    # ------------------------------------------------------------------
    # 补齐：交互历史持久化 + 工具运行态刷新
    #
    # 原文件里这四处只有调用点、没有实现：
    #   on_tool_call_start       -> _start_tool_running_updates + _persist_tool_call
    #   on_tool_call_end         -> _cancel_tool_running_updates + _persist_tool_call
    #   on_agent_text_stream_end -> _persist_agent_text_stream_complete
    #
    # 两条贯穿始终的约束（沿用本文件既有的「只降级、不打断」风格）：
    #
    # 1. 持久化目标是 ``self._chat_history_manager``（生产装配处
    #    ``cli/tui_application.py`` 注入的是
    #    ``session_persistence.infrastructure.file_event_log.AppV3EventPersistence``）。
    #    那个类目前**只有 __init__**，一个写入方法都没有，全项目也没有别处
    #    调用过它。所以这两个 _persist_* 走「能力探测」：谁在就先给谁。谁都不在
    #    就只记 debug——写流水失败只是可观测性损失，绝不能把正在跑的一轮对话
    #    打断。等以后写入器落地（无论叫 append_event 还是 append），这里不用改。
    # 2. 运行态刷新任务只在本 observer 仍持有**那一个** live 块时才更新；块一旦
    #    换掉（工具结束、或被别的块抢占）任务自己退出，退出时把自己从表里摘掉，
    #    不留孤儿任务、不去写已经收起来的块。
    # ------------------------------------------------------------------

    #: 工具运行态刷新的间隔（秒）。只画「已跑多久」，所以 1 秒足够：
    #: 再密也只是重画同一帧，白烧 UI 线程。
    _RUNNING_UPDATE_INTERVAL:float=1.0

    #: 事件写入方法的候选名，越具体越先选；两个 ``_persist_*`` 共用这份清单。
    #: 末尾两个是通用兜底（「追加一条记录」这种形态）。
    _EVENT_SINK_METHODS:tuple[str,...]=(
        "append_event",
        "record_event",
        "persist_event",
        "save_event",
        "write_event",
        "log_event",
        "append",
        "record",
    )

    async def _persist_tool_call(
        self,
        agent_name:str,
        tool_name:str,
        is_start:bool,
        args_summary:typing.Optional[str]=None,
        tool_id:typing.Optional[str]=None,
        timeout:typing.Optional[int]=None,
        cwd:typing.Optional[bool]=None,
        timestamp:typing.Optional[int]=None,
        thinking_operation_id:typing.Optional[str]=None,
        error:typing.Optional[bool]=None,
        result_summary:typing.Optional[str]=None,
    )->None:
        try:
             session_id= get_session_id()
             if not session_id:
                 return
             data=EventProtocol.build_tool_execution(
                 agent_name=agent_name,
                 tool_name=tool_name,
                 is_start=is_start,
                 tool_id=tool_id,
                 args_summary=args_summary,
                 timeout=timeout,
                 cwd=cwd,
                 timestamp=timestamp,
                 error=error,
                 result_summary=result_summary,
                 thinking_operation_id=thinking_operation_id,
             )
             await self._persist_event(session_id, "tool-execution", data)
        except Exception as e:
            logger.error(e)

    def _start_tool_running_updates(
        self,
        tool_name:str,
        args_summary:str,
        block_id:str,
        tool_id:typing.Optional[str],
    )->None:
        handler=self._get_tool_handler(tool_name)
        if not isinstance(handler, ExecuteCommandHandler):
            return
        task_key = self._tool_running_task_key(tool_name,tool_id)
        self._cancel_tool_running_updates(tool_name,tool_id)
        self._tool_running_update_tasks[task_key]=asyncio.create_task(
            self._refresh_tool_running_display(task_key,tool_name, args_summary,block_id),
        )
    @staticmethod
    def _tool_running_task_key(tool_name:str, tool_id: Optional[int])->str:
        return tool_id or f"tool_{tool_name}"

    def _cancel_tool_running_updates(
        self,
        tool_name:str,
        tool_id:typing.Optional[str]=None,
    )->None:
        task_key= self._tool_running_task_key(tool_name,tool_id)
        task= self._tool_running_update_tasks.pop(task_key,None)
        if task and not task.done():
            task.cancel()

    async def _persist_agent_text_stream_complete(self,
        agent_name:str,complete_text:str, timestamp:Optional[str]=None)->None:
        try:
            session_id=get_session_id()
            if not session_id:
                return
            data=EventProtocol.build_agent_text(
                agent_name=agent_name,
                content=complete_text,
                is_streaming=False,
                operation_id = None,
            )
            await self._persist_event(session_id, "agent", data)
        except Exception as e:
            logger.error(e)

    #: 运行态刷新的转轮帧。处理器自己会画（``render_update``）时用不上它，
    #: 只在处理器还没实现 ``render_update`` 时兜底——目前
    #: ``tui.display.tools`` 下所有 Handler 都只有 ``handle``，所以这条兜底
    #: 分支才是今天真正跑的那条。
    _RUNNING_SPINNER_FRAMES:tuple[str,...]=(
        "⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏",
    )

    async def _refresh_tool_running_display(
        self,
        task_key:str,
        tool_name:str,
        args_summary:str,
        block_id:str,
    )->None:
        started_at = time.monotonic()
        try:
            while True:
                await asyncio.sleep(self.TOOL_RUNNING_UPDATE_INTERRVAL_SECONDS)
                if self._active_block_id != block_id or not self._live_static:
                    return
                handler = self._get_tool_handler(tool_name)
                if not isinstance(handler, ExecuteCommandHandler):
                    return

                elapsed_ms= (time.monotonic()-started_at)*1000
                renderable= handler.render_update(
                        tool_name, args_summary, is_final=False, block_id=block_id, elapsed_ms=elapsed_ms,
                    )
                self.app.safe_update(self._live_static, renderable)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(e)
        finally:
            current_task= asyncio.current_task()
            if self._tool_running_update_tasks.get(task_key) is current_task:
                self._tool_running_update_tasks.pop(task_key,None)


    async def _persist_event(self, session_id:str, event_type :str, data:dict[str,Any])->None:
        if not self._should_persist(event_type):
            return

        if self._chat_history_manager:
            try:
                if "timestamp" not in data:
                    data["timestamp"]= datetime.now().isoformat()
                message={"type":event_type,**data}
                message["session_id"]= session_id
                version_id = await self._chat_history_manager.persist(session_id, message)
            except Exception as e:
                logger.error(e)

    def _get_model_context_window(self, model_name:str|None)->int:
        return get_context_window_for_model(model_name)

    def _should_ignore_thinking_udpate(self) -> bool:
        return (
            self._active_block_id=="agent_replay"
            or self._agent_reply_finalized
            or self._context_window_rendered
            or bool(self._final_response_text)
        )
    def _should_persist(self, event_type: str)->bool:
        return event_type not in self.EPHEMERAL_EVENT_TYPES
