import uuid
import asyncio
from typing import TypeVar,Any,cast,Awaitable
import logging
import signal
import pydantic
from collections.abc import Mapping
# 注意：原先此处 ``from pip._internal.commands.help import HelpCommand`` 从 pip
# 内部导入了同名但完全无关的类，只因为下面 commands.implementation 的同名导入
# 恰好覆盖了它才没出事。属于随时会咬人的脏导入，已移除。
from domains.runtime.application.request_context import bind_app_request_context
from domains.configuration.domain.runtime_state.prompt_expansion import PromptExpansion
from domains.runtime.application.rabbit_application import RabbitApplication
from domains.agent.agent_turn_queue import AgentTurnQueue
from domains.configuration.domain.model.app_config import AppConfig
from interfaces.commands.markdown_command import MarkdownCommand
from interfaces.cli.agent_executor import AgentExecutorThread
from interfaces.commands.registery import CommandRegistry
from domains.permission.cli_strategy import CLIConfirmationStrategy
from domains.configuration import get_app_storage_dir
from domains.session.infrastructure.file_event_log import AppV3EventPersistence
from interfaces.tui.tui_observer import TUIObserver
from domains.agent.domain.value_objects.message import Msg
from domains.permission import ConfirmationRegistry, InterruptManager
from domains.agent.domain.aggregates.execution_context import get_session_id
from domains.observability.application.observability_context import generate_trace_id,set_trace_id
from domains.llm.provider.application.authentication.error_message import format_cli_user_message_error
from interfaces.commands.implementation import (
    ShellCommand,
    ExitCommand,
    SettingsCommand,
    BTWCommand,
    ConfigCommand,
    ModelCommand,
    ThemeCommand,
    ClearCommand,
    ConfigEncryptCommand,
    ExitCommand,
    HelpCommand,
    HookCommand,
    SkillsCommand,
    AgentsCommand,
    PlanCommand,
    LSPCommand,
    MCPCommand,
    MemoryCommand,
    ModeCommand,
    ResetCommand,
    SessionCommand,
    StatusCommand,
    ConfigDecryptCommand,
    MultilineCommand,
    TokensCommand,
    WebCommand,
    InterruptCommand,
)

__version__ ="1.0.0"
logger = logging.getLogger(__name__)

T=TypeVar("T")

class _TUIAgentTurn(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid",frozen=True)

    message: str
    prompt_expansion: PromptExpansion|None=None

def _session_paramenters_from_overrides(overrides:object)->tuple[str|None, bool|None]:
    if not isinstance(overrides, Mapping):
        return None, None
    session_config = overrides.get("session")
    if not isinstance(session_config, Mapping):
        return None, None
    raw_session_id =session_config.get("session_id")
    raw_resume = session_config.get("resume",False)
    session_id = raw_session_id if isinstance(raw_session_id, str) and raw_session_id else None
    is_resume = raw_resume if isinstance(raw_resume, bool)  else False
    return session_id, is_resume



class TUIApplication:
    """Textual TUI 应用安装包"""


    def __init__(self
        , app_config:AppConfig, project_home:str="."):
        """初始化应用"""
        self.app_config = app_config
        self.project_home = project_home

        #中断状态
        self.interrupted = False
        self._agent_executor: AgentExecutorThread| None = None
        self._selected_model_name: str|None = None

        #1. 创建命令注册表
        self.command_registry= CommandRegistry(self.project_home)

        #2. 先注册命令（必须在创建observer之前！））
        self._setup_commands()
        self.command_registry.discover_markdown_commands()

        #2.5 初始化共享App V3 event persistence 用于持久化TUI交互历史
        sessions_dir = get_app_storage_dir()
        self.chat_history_manager =AppV3EventPersistence(sessions_dir)

        #3 创建TUIOberserver（基于Textual TUI）-此时命令列表已填充
        tui_observer_cls =cast(Any,TUIObserver)
        self.observer = tui_observer_cls(
            command_registry=self.command_registry,
            chat_history_manager=self.chat_history_manager,
            tui_application=self,  #传递TUIApplication 引用用于ESC中断
            project_home=self.project_home,
        )

        # 注册确认策略
        self.confirmation_strategy = CLIConfirmationStrategy(self.observer)
        # 注册表门面会同步写入领域层类级注册表，两种取回方式拿到的是同一实例
        ConfirmationRegistry.register_strategy(self.confirmation_strategy)

        #4. 创建 RabbitApplication（业务逻辑）
        self.rabbit_app = RabbitApplication(app_config, observer=self.observer)
        self._agent_turn_queue = AgentTurnQueue(self._execute_queued_user_message)
        self._sync_model_completions()

        logger.info(f"TUIApplication initialized with{len(self.command_registry.list_commands())} commands")

    def _sync_status_bar_context(self) -> None:
        """把当前模型名与工作目录同步到 TUI 状态栏。

        状态栏文案由 ``TUIObserver.app.set_status_context`` 生成。该方法此前
        从未被调用，导致 ``model_name`` 恒为初始值 ``"unknown"``。这里从
        ``app_config`` 里取出首个 LLM profile 的模型名回填，取不到就保持原值，
        不做任何猜测。
        """
        try:
            app = getattr(self.observer, "app", None)
            setter = getattr(app, "set_status_context", None)
            if not callable(setter):
                return
            model_name = ""
            try:
                llm_cfg = None
                # app_config 是 AppConfig 对象而非 dict，且真实字段名是
                # ``llm_config``（不是 main.py 里构造 bootstrap 用的 llmConfigs）。
                getter = getattr(self.app_config, "llm_config", None)
                if callable(getter):
                    llm_cfg = getter()
                elif getter is not None:
                    llm_cfg = getter
                if llm_cfg is None and isinstance(self.app_config, dict):
                    llm_cfg = self.app_config.get("llm_config") or self.app_config.get(
                        "llmConfigs"
                    )
                model_name = str(getattr(llm_cfg, "model_name", "") or "")
                if not model_name and isinstance(llm_cfg, dict):
                    model_name = str(llm_cfg.get("model_name") or llm_cfg.get("model") or "")
            except Exception:
                model_name = ""
            setter(model_name=model_name or None, working_directory=self.project_home)
        except Exception:
            logger.debug("failed to sync status bar context", exc_info=True)

    def _setup_commands(self) -> None:
        """注册内置CLI 命令"""
        #shell command
        self.command_registry.register(ShellCommand())
        # Basic commands
        self.command_registry.register(ExitCommand())
        self.command_registry.register(HelpCommand(self.command_registry))
        self.command_registry.register(ResetCommand(cast(Any,None)))
        self.command_registry.register(StatusCommand(cast(Any,None)))
        self.command_registry.register(ConfigCommand())
        # Mode switching
        self.command_registry.register(ModeCommand())

        self.command_registry.register(ClearCommand())
        self.command_registry.register(MultilineCommand())
        self.command_registry.register(TokensCommand())
        self.command_registry.register(WebCommand())
        self.command_registry.register(InterruptCommand())

        #Session managerment
        self.command_registry.register(SessionCommand())
        #信息查询
        self.command_registry.register(SkillsCommand())
        self.command_registry.register(AgentsCommand())
        self.command_registry.register(PlanCommand())
        self.command_registry.register(MCPCommand())
        # 记忆管理：此前只import 了 MemoryCommand 却从未 register，
        # 导致 /memory 在 TUI 里查不到命令（属于功能缺失，不是设计如此）。
        self.command_registry.register(MemoryCommand())
        # Hook 管理：同上，补齐注册。
        self.command_registry.register(HookCommand())
        # LSP 状态：同上，补齐注册。
        self.command_registry.register(LSPCommand())

        #配置管理
        self.command_registry.register(SettingsCommand())
        self.command_registry.register(ModelCommand())
        self.command_registry.register(ThemeCommand())
        #加密管理
        self.command_registry.register(ConfigEncryptCommand())
        self.command_registry.register(ConfigDecryptCommand())

    def _sync_model_completions(self) -> None:
        model_command =self.command_registry.get_command("model")
        available_models_loader = getattr(model_command, "_available_models", None)
        if not callable(available_models_loader):
            return

        try:
            context={"application":self.rabbit_app}
            models = cast(Any,available_models_loader)(context)
        except Exception as e:
            logger.debug(e)
            return

        current_model =self._current_model_name()
        model_descriptions:dict[str,str]={}
        for model in models:
            model_name=getattr(model,"name",None)
            if not isinstance(model_name,str) or not model_name.strip():
                continue
            provider = getattr(model,"provider","unknown")
            provider_text = provider.strip() if isinstance(provider,str) else "unknown"
            label ="Current model" if model_name.strip()==current_model else "Configured model"
            if provider_text and provider_text != "unknown":
                label=f"{label}: {provider_text}"
            model_descriptions[model_name.strip()]=label
        if current_model and current_model in model_descriptions:
            model_descriptions[current_model]="Current model"
        self.observer.app.set_model_descriptions(model_descriptions)


    def _setup_signal_handlers(self) -> None:
        """设置信号处理器（Ctrl+C 中断）"""
        def handle_sigint():
            # 处理SIGINT信号
            self.interrupted = True
            logger.info("Interrupted signal received,shutting down ...")
        #注册信号处理器
        try:
            signal.signal(signal.SIGINT, lambda signum, frame: handle_sigint())
        except ValueError as e:
            logger.warning("Could not set SIGINT signal handler")

    def _start_agent_executor(self)->None:
        """Start the agent executor loop used bu agent and interrupt work"""
        if self._agent_executor is None:
            self._agent_executor =AgentExecutorThread()
        if not self._agent_executor.is_running:
            self._agent_executor.start()
            logger.info(f"Agent executor started ...")

    def _stop_agent_executor(self) -> None:
        """停止 agent executor（幂等；finally 清理用）。"""
        if self._agent_executor is not None:
            try:
                self._agent_executor.stop()
            except Exception as e:
                logger.warning(f"agent executor stop failed: {e}")

    async def _shutdown_agent_turn_queue(self) -> None:
        """关闭 agent turn 队列：清空待处理并取消活跃 task（清理路径用）。"""
        q = getattr(self, "_agent_turn_queue", None)
        if q is None:
            return
        try:
            q.discard_pending()
        except Exception as e:
            logger.warning(f"discard pending turns failed: {e}")
        active = getattr(q, "_active_task", None)
        if active is not None and not active.done():
            active.cancel()

    async def _cleanup_realy_app(self) -> None:
        """清理 RabbitApplication 资源（清理路径用；typo rabbit 沿用项目命名）。"""
        rabbit = getattr(self, "rabbit_app", None)
        if rabbit is None:
            return
        cleanup = getattr(rabbit, "cleanup", None) or getattr(rabbit, "close", None)
        if callable(cleanup):
            try:
                result = cleanup()
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                logger.warning(f"rabbit_app cleanup failed: {e}")

    async def run(self) -> None:
        #设置中断处理
        self._setup_signal_handlers()
        self._start_agent_executor()

        try:
           #初始化RabbitAppliation
           logger.info("TUIApplication initialized")

           session_id, is_resume= _session_paramenters_from_overrides(self.app_config.config_overrides)
           if session_id is not None:
               logger.info(f"TUIApplication session_id:{session_id} is_resume:{is_resume}")

           # Phase 2 requires all ReActAgents(except btw_agent) to have agent_context
           if not session_id:
               session_id =str(uuid.uuid4())

           bind_app_request_context(app_id=session_id, project_home=self.project_home, app_instance=self.rabbit_app)

           #load agent configs and create Plan agent +Sub-Agents
           sub_agent_configs, plan_agent_config = self.rabbit_app._load_agent_configs()

           if not plan_agent_config:
               logger.error("Cannot start: missing plan configuration")
               return
           #create agents(Plan Agent + Sub-Agents)
           await self._submit_agent_coro(self.rabbit_app._create_agents(sub_agent_configs,plan_agent_config))

           #Initialize session
           #CRITICAL FIX: PASS SESSION IS and is_reums to support resuming existinf sessions
           session_id =await self._submit_agent_coro(
               self.rabbit_app.init_session(session_id=session_id,is_resume=is_resume)
           )

           #update session context globally with the real session_id from init_session()
           if session_id:
               logger.info(f"Session initialized with session_id:{session_id}")
               bind_app_request_context(app_id=session_id, project_home=self.project_home, app_instance=self.rabbit_app)

           # 把真实模型名与工作目录推给状态栏。
           # ``TUIObserver.app.set_status_context`` 一直存在却全项目无人调用，
           # 于是 ``TUIObserver.app.model_name`` 永远停在初始值 "unknown"——
           # 配置里明明有 model_name，状态栏却恒显示 Model: unknown。
           self._sync_status_bar_context()

           # 启动Textual TUI应用
           #必须在进入主循环之前启动 Textual 应用，否则 get_user_input 会永远等待
           self._show_welcome_status(session_id)
           self.observer.on_application_start(__version__)

           #主循环: 获取用户输入并处理
           while not self.interrupted:
               try:
                   # 通过TUIOberserver 获取用户输入
                   user_input = await self.observer.get_user_input()
                   if user_input is None:
                       # 用户取消输入或EOF
                       break
                   # 检查是否为本地命令（以/ 或！开头）
                   if self._is_command_input(user_input):
                       expansion = await self._handle_command(user_input)
                       if expansion is not None:
                           await self._enqueue_user_message(
                               expansion.expanded_prompt,
                               prompt_expansion=expansion,
                           )
                       else:
                           # 处理普通用户消息
                           await self._enqueue_user_message(user_input)
               except asyncio.CancelledError:
                    # 任务被取消（通常有中断触发）
                    # 中断后继续执行，不退出主循环，允许用户继续输入
                    continue
               except Exception as e:
                   logger.error(e)
                   #继续进行，不退出

           #清理
           await self._shutdown_agent_turn_queue()
           await self._cleanup_realy_app()
        except Exception as e:
           logger.error(e)
           raise
        finally:
            self._stop_agent_executor()
            logger.info(f"TUIApplication shutdown complete")

    async def _submit_agent_coro(self, coro:Awaitable[T]) -> T:
        if self._agent_executor is not None and self._agent_executor.is_running:
            return await self._agent_executor.submit_async(coro)
        return await coro

    def _show_welcome_status(self,session_id) -> None:
        skills =getattr(self.app_config, "skills", []) or []
        self.observer.show_welcome(session_id=session_id or "", model=self._current_model_name(),skills=skills)

    @staticmethod
    def _is_command_input(user_input:str) -> bool:
        stripped= user_input.strip()
        return stripped.startswith("/") or stripped.startswith("!")

    def _current_model_name(self) -> str:
        if self._selected_model_name:
            return self._selected_model_name
        rabbit_app = getattr(self,"rabbit_app", None)
        rabbit_llm_config= getattr(rabbit_app,"llm_config", None)
        rabbit_mode_name=getattr(rabbit_llm_config,"mode_name", None)
        if isinstance(rabbit_mode_name, str) and rabbit_mode_name.strip():
            return rabbit_mode_name.strip()
        llm_config= getattr(self.app_config,"llm_config", None)
        return getattr(llm_config,"model_name", None) or getattr(self.app_config,"mode_name", None) or "gpt-4o"

    async def _enqueue_user_message(self,message:str,*, prompt_expansion: PromptExpansion |None= None) -> None:
        pending_position= self._agent_turn_queue.submit(
            _TUIAgentTurn(
                message=message,
                prompt_expansion=prompt_expansion,
            )
        )

        if pending_position:
            await self.observer.on_command_warning(f"Message queued:{pending_position} pending behind thr active turn")

    async def _execute_queued_user_message(self,turn:_TUIAgentTurn)->None:
        await self._handle_user_message(
            turn.message,
            prompt_expansion=turn.prompt_expansion,
        )

    async def _handle_user_message(
        self,
        message:str,
        *,
        prompt_expansion: PromptExpansion |None= None,
        )->None:
        """处理用户消息"""
        try:
            self.observer.prepare_for_agent_turn(message)
            user_msg = Msg(role="user", content=message,name="user")
            session_id = get_session_id()
            if not session_id:
                raise RuntimeError("No active session")
            await InterruptManager.clear_interrupt(session_id)

            trace_id = generate_trace_id()
            set_trace_id(trace_id)

            if prompt_expansion is None:
                agent_coro = self.rabbit_app.handle_user_message(user_msg,session_id)
            else:
                agent_coro = self.rabbit_app.handle_user_message(user_msg,session_id,prompt_expansion=prompt_expansion)
            await self._submit_agent_coro(agent_coro)
        except Exception as e:
            user_message_error = format_cli_user_message_error(e)
            if user_message_error:
                self.observer._stop_active_live(transient=True)
                await self.observer.on_command_warning(user_message_error)
                return
            #显示错误给用户
            await self.observer.on_error("UserMessage",str(e))

    @staticmethod
    def _selected_model_from_command_args(args:str)->str|None:
       model_name=args.strip()
       if not model_name:
           return None
       first_token= model_name.split(maxsplit=1)[0].lower()
       if first_token in {"list","ls","show","current"}:
           return None
       return model_name

    def _async_selected_model_status(self, model_name:str)->bool:
        self._selected_model_name=model_name
        self.observer.current_model = model_name
        self.observer.set_status_context(model_name=model_name)
        try:
            self.observer._last_max_tokens=self.observer._get_model_context_window(model_name)
        except Exception as e:
            logger.error(e)
        self._sync_model_completions()


    @staticmethod
    def _display_command_name(cmd_name:str)->str:
        return "!" if cmd_name =="!" else f"/{cmd_name}"

    @staticmethod
    def _command_args_text(args:str|list[str]) -> str:
        return args if isinstance(args,str) else " ".join(args)
    @staticmethod
    def _command_args_for_execute(cmd_name:str, args:str|list[str]) -> list[str]:
        if cmd_name =="!":
            shell_command=args if isinstance(args,str) else "".join(args)
            return [shell_command] if shell_command else []
        return args if isinstance(args,list) else args.split()

    async def _handle_command(self, command:str) -> PromptExpansion| None:
        try:
            cmd_type,cmd_name,parsed_args = self.command_registry.parse_input(command)
            if cmd_type not in {"slash","shell"}:
                return None
            args= self._command_args_text(parsed_args)
            cmd=self.command_registry.get_command(cmd_name)
            if cmd:
                execute_args= self._command_args_for_execute(cmd_name, parsed_args)
                if isinstance(cmd, MarkdownCommand):
                    return cmd.build_prompt_expansion(command,execute_args)
                await self.observer.on_command_result_start()
                self.observer.begin_command_output_capture()

                context={
                    "app":self.rabbit_app,
                    "application":self.rabbit_app,
                    "observer":self.observer,
                    "project_home":self.project_home,
                }

                result = await cast(Any,cmd).execute(execute_args,context)
                if result and cmd_name=="model" and getattr(result,"success",False):
                    selected_model = self._selected_model_from_command_args(args)
                    if selected_model:
                        self._async_selected_model_status(selected_model)
                if result:
                    result_message=result.message if hasattr(result,"message") else result
                    if not self.observer.has_printed_command_result(result_message):
                        await self.observer.on__command_result(result_message, include_title=False)
                    # 命令可以请求退出应用（``/exit`` 返回 should_exit=True）。
                    # 修复前这个标志全项目无人读取：ExitCommand 老老实实返回了
                    # ``CommandResult(success=True, should_exit=True, message="Bye")``，
                    # 但派发处只把 message 打到屏上就继续等下一条输入，于是
                    # ``/exit`` 打完 "Bye" 又把光标留在输入框——**TUI 永远退不出去**。
                    if getattr(result,"should_exit",False):
                        self._request_exit()
            else:
                await self.observer.on_command_warning(f"Unknown command: {self._display_command_name(cmd_name)}")
                await self.observer.on__command_result("Type/help for available commands")
        except Exception as e:
            await self.observer.on_error("CommandExceution",str(e))
        return None

    def _request_exit(self) -> None:
        """响应命令的 ``should_exit`` 请求，关闭 TUI 并终止主循环。

        两件事必须都做，缺一个都会让 ``/exit`` 失效：

        1. ``observer.app.exit()`` —— 结束 Textual 的 ``run_async`` 任务。
           只设 ``interrupted`` 的话，主循环虽然退出了，但 Textual 事件循环
           仍在跑，进程挂住不退。
        2. ``self.interrupted = True`` —— 让 ``run()`` 的 ``while`` 条件为假，
           主循环正常走 ``_shutdown_agent_turn_queue`` / ``_cleanup_realy_app``
           的收尾流程，而不是被硬杀。

        另外要把 ``get_user_input()`` 正挂着的那个 future 唤醒掉，否则主循环
        会永远卡在 ``await self.app._input_future`` 上（此时它还在等下一次输入）。
        """
        logger.info("exit requested by command, shutting down TUI")
        self.interrupted = True
        app = getattr(self.observer, "app", None)
        if app is not None:
            future = getattr(app, "_input_future", None)
            if future is not None and not future.done():
                # 唤醒主循环；空串会被 get_user_input 判为空输入返回 None → break
                future.set_result("")
            try:
                # 显式 return_code=0：``TUIObserver._handle_app_result`` 把
                # ``return_code`` 非零判成「CLI APP EXIST ABNORMALLY」并
                # ``os._exit(非零)``，那是给崩溃兜底的路径，正常退出不该走。
                app.exit(return_code=0)
            except Exception:
                logger.debug("app.exit() failed", exc_info=True)

