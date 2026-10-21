import asyncio
import inspect
import logging
import sys

from prompt_toolkit.completion import CompleteEvent, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import to_plain_text
from rich.text import Text

from textual.widget import Widget
from textual import events,on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option
from typing import Any, Optional,Dict,List
from domains.agent.domain.aggregates.execution_context import  get_session_id

from interfaces.tui.user_input.completer import CliCompleter
from interfaces.tui.widgets.user_input_area import UserInputArea

logger = logging.getLogger(__name__)

_TRACE_PATH = r"D:\AI\AIProject\RabbitAgent\_trace_submit.txt"


def _tui_trace(message: str) -> None:
    """临时诊断：把提交链路的关键节点写到独立文件（不走 logging）。

    日志系统里看不到 ``tui.*`` 的输出，排查「消息发出去但没回复」时需要一条
    独立于日志配置的证据链。确认问题后本函数连同调用点一起移除。
    """
    try:
        import time as _time

        with open(_TRACE_PATH, "a", encoding="utf-8") as fh:
            fh.write("%.3f %s\n" % (_time.time(), message))
    except Exception:  # noqa: BLE001
        pass

_MODULE_LOAD_STDOUT = sys.stdout


def _stdout_is_tty() -> bool:
    """判定**原始** stdout 是否为真实终端。

    Textual 启动后会把 ``sys.stdout`` 换成自己的重定向对象，其 ``isatty``
    恒为 True；在 ``on_mount`` 里直接查 ``sys.stdout`` 会永远得到
    「是终端」的错误结论。所以要在模块加载时捕获原始流来判断。
    """
    for stream in (getattr(sys, "__stdout__", None), _MODULE_LOAD_STDOUT, sys.stdout):
        try:
            if stream is not None and stream.isatty():
                return True
        except Exception:
            continue
    return False


class TUIApp(App):

    CSS="""
    /*基础布局*/
Screen{
   background: #0d0d0d;
}
#layout {layout: vertical;}
#main-container{
   width:1fr;
   height:100%;
}

#chat-area {
    height: 1fr;
    margin: 0 1;
    overflow-y:scroll;
    scrollbar-size: 1 1;
    scrollbar-color: #333333;
    scrollbar-background: #0d0d0d;
 }
 
#input-container{
   border-left: solid #3b82f6;
   background: #161616;
   height:3;
   margin:0 1 1 1;
   transition: height 100ms;
}

#user-input{
   background: rgba(0,0,0,0);
   border: none;
   color: #f4f4f5;
   padding: 1 1;
   scrollbar-size: 1 1;
   scrollbar-color: #333333;
   scrollbar-background: #0d0d0d;
}

#user-input:focus { border:none;}
#user-input > .text-area-cursor-line{
   background:transparent;
}

/* 消息格式*/
.user-msg{
   color:#cbd5e1;
   background: #151515;
   margin: 1 0 1 0;
   border-left: solid #3b82f6;
   padding: 0 1;
}
.keys-hint{
   color: #6b7280;
   padding-left: 1;
   margin: 0 1 0 1;
   text-wrap: nowrap;
   height: 1;
}
.keys-hint.copy-enabled{
   color: #22c55e;
}

.keys-hint.copy-disabled{
   color: #6b7280;
}

#completion-list {
    width: auto;
    display:none;
    background: $surface;
    border: none;
    padding: 0;
    layer: top;
}

#completion-list.show{
   display:block;
}

#completion-list >.option-list--item{
    padding: 0 1;
}

#interrupt-prompt {
    background: $warning-muted;
    color: $text;
    padding: 0 1;
    border: tall $warning;
    margin: 0 1;
    display: block;
}

#interrupt-prompt.hidden {
        display: none;
}

"""


    BINDINGS=[
        ("ctrl+q","none","Disable Quit"),
        ("escape","interrupt","Interrupt"),
        ("f2","toggle_cope_mode","Copy Mode")
    ]

    def __init__(self,command_registry:Any=None,tui_application:Any=None) -> None:
        App.__init__(self)
        commands=[]
        command_descriptions={}
        if command_registry:
            commands=[cmd.name for cmd in command_registry.list_commands()]
            command_descriptions={cmd.name:cmd.description for cmd in command_registry.list_commands()}
        self.cli_comleter = CliCompleter(commands,command_descriptions)
        # 原先 command_registry 只被消费进 CliCompleter 就丢掉了，app 上取不回来。
        # UserInputArea._is_exact_command_match 需要它来判定「输入是否已是完整
        # 已注册命令」，否则 `/exit` 会被过期的补全列表改写成 `/exitagents`。
        self.command_registry = command_registry
        # 命令名集合的快照：注册表是权威来源，但每次按键都 list_commands() 有开销，
        # 这里缓存一份名字列表供精确匹配使用。
        self._command_names: frozenset[str] = frozenset(
            name for name in commands if isinstance(name, str)
        )

        self._input_future: Optional[asyncio.Future] = None
        self._input_value: Optional[str] = None
        self._pending_submissions:list[str]=[]
        self.ready_event=asyncio.Event()
        self._interrupt_handler=None
        self.pending_input:str=""
        self.awaiting_interrupt_confirm=False
        self._is_interrupted=False
        self._mouse_reporting_suspended=False
        self.model_name="unknown"
        self.working_directory="."
        self._key_hint_widget:Optional[Label]=None
        self._return_code=0
        self.tui_application=tui_application

    def set_model_completions(self, model_descriptions:dict[str,str]) -> None:
        self.cli_comleter.set_model_completions(model_descriptions)

    def set_model_descriptions(self, model_descriptions:dict[str,str]) -> None:
        """由 ``TUIApplication._sync_model_completions`` 调用，同步模型补全候选。

        原代码只存在 ``set_model_completions``，而调用点是 ``set_model_descriptions``，
        缺这个方法会在 ``TUIApplication.__init__`` 里抛 ``AttributeError``，直接阻断启动。
        二者语义一致——都是把模型名→描述塞进补全器——这里直接转发，任何异常只记 debug。
        """
        try:
            self.set_model_completions(model_descriptions)
        except Exception as e:
            logger.debug("failed to set model descriptions: %s", e)

    def set_terminal_mouse_reporting(self, enabled: bool) -> bool:
        driver=getattr(self,"_driver",None)
        method_name ="_enable_mouse_support" if enabled else "_disable_mouse_support"
        method= getattr(driver,method_name,None)
        if not callable(method):
            return False
        try:
            method()
        except Exception as e:
            return False

        self._mouse_reporting_suspended = not enabled
        self._refresh_key_init()
        return True

    def action_toggle_copy_mode(self) -> None:
        self.set_terminal_mouse_reporting(self._mouse_reporting_suspended)

    @classmethod
    def key_hint_text(
        cls,
        model_name:str="unknown",
        working_directory:str=".",
        copy_mode_enabled:bool=False,
    )->str:
        newline_key="ctrl+enter" if sys.platform == "win32" else "ctrl+j"
        model = cls._compact_text(model_name or "unknown",32)
        workdir = cls._compact_text(working_directory or ".", 48)
        copy_label= "ON" if copy_mode_enabled else "OFF"
        shortcuts= f"/ commands | @ files | ! bash | F2 select/copy | Copy:{copy_label} | {newline_key} newline"
        return f"Model: {model} | cmd: {workdir} | {shortcuts}"

    @classmethod
    def key_hint_renderable(
        cls,
        model_name: str="unknown",
        working_directory: str=".",
        copy_mode_enabled: bool=False,
    ) -> str:
        """底部按键提示条内容。"""
        newline_key="ctrl+enter" if sys.platform == "win32" else "ctrl+j"
        model = cls._compact_text(model_name or "unknown",32)
        workdir = cls._compact_text(working_directory or ".", 48)
        copy_label= "ON" if copy_mode_enabled else "OFF"
        copy_style = "bold #22c55e" if copy_mode_enabled else "#f59e0b"
        text=Text.assemble(
            "Model:",
            (model,"#9ca3af"),
            " |cwd:",
            (workdir,"#9ca3af"),
            " | / commands | @ files |! bash | F2 select/copy | Copy:",
            (copy_label, copy_style),
            f" |{ newline_key} newline",
            style="#6b7280",
        )
        return text
        # copy_hint = "F2 关闭复制模式" if copy_mode_enabled else "F2 复制模式"
        # return f"{mode_name} | {working_directory} | Enter 发送 · Esc 中断 · {copy_hint}"
    @staticmethod
    def _compact_text(value: str, max_chars:int)->str:
        if len(value) <= max_chars:
            return value
        suffix_len =max(8,max_chars//2)
        prefix_len= max_chars -suffix_len-3
        return f"{value[:prefix_len]}...{value[-suffix_len:]}"

    def set_status_context(self, model_name: str | None = None, working_directory: str | None = None) -> None:
        if model_name:
            self.model_name = model_name
        if working_directory:
            self.working_directory = working_directory
        self._refresh_key_init()

    def _refresh_key_init(self) -> None:
        if self._key_hint_widget is None:
            return
        copy_mode_enabled = self._mouse_reporting_suspended
        self._key_hint_widget.set_class(copy_mode_enabled, "copy-enabled")
        self._key_hint_widget.set_class(not copy_mode_enabled, "copy-disabled")
        self._key_hint_widget.update(
            self.key_hint_renderable(self.model_name, self.working_directory, copy_mode_enabled)
        )

    def compose(self)->ComposeResult:
        with Container(id="layout"):
            with Container(id="main-container"):
                yield VerticalScroll(id="chat-area")
                yield Static(
                    "当前Agent 正在运行。输入[b]Y[/b]确认打断并执行，输入[b]N[/b]取消",
                    id="interrupt-prompt",
                    classes="hidden",
                )
                with Horizontal(id="input-container"):
                    yield UserInputArea(id="user-input", soft_wrap=True)
                pl= OptionList(id="completion-list")
                pl.can_focus=False
                # 默认收起，由 show_completions()/hide_list() 控制显隐
                yield pl
                self._key_hint_widget = Label(
                    self.key_hint_renderable(self.model_name,self.working_directory,self._mouse_reporting_suspended),
                    id="key-hint",
                    classes="keys-hint copy-disabled",
                )
                yield self._key_hint_widget

    def on_mount(self) -> None:
        """挂载完成：置位就绪事件并聚焦输入框。

        ``TUIObserver._render_banner_after_ready`` 会一直等 ``ready_event``，
        没有任何地方置位的话 banner 永远不会渲染。
        """
        self.text_area = self.query_one(UserInputArea)
        self.list_widget = self.query_one(OptionList)
        self.text_area.focus()
        # 非 TTY（PyCharm Run 窗口、重定向 stdout）里，光标闪烁会让 Textual
        # 以约 0.5s 一次的频率全屏重绘；这些重绘在真终端里是原地覆盖，
        # 在非 TTY 里则一行行往下堆，看起来就是整屏内容不停滑动。
        # 关掉闪烁能显著缓解；治本仍需在真终端 / 终端模拟里运行。
        try:
            if not sys.stdout.isatty():
                self.text_area.cursor_blink = False
                logger.info("stdout is not a tty; TextArea cursor blink disabled")
        except Exception:
            logger.debug("failed to adjust cursor blink", exc_info=True)

    def _handle_exception(self, error: Exception) -> None:
        """未捕获异常的收尾。

        ``textual.App._handle_exception`` 会先把 ``_return_code`` 置成 1，
        但 ``App.exit()`` 的第一个位置参数是 ``result``——它会成为
        ``run_async()`` 的返回值；真正的退出码必须用关键字参数
        ``return_code=`` 传。

        写成 ``self.exit(self._return_code)`` 会把 1 当 result 返回，
        ``TUIObserver._handle_app_result`` 于是只看到 ``result=1``、
        打出 "CLI APP EXIST ABNORMALLY" 却看不到真正的异常堆栈。
        """
        super()._handle_exception(error)
        logger.error("TUI app exiting after unhandled exception", exc_info=error)
        self.exit(return_code=self._return_code)

    def on_ready(self) -> None:
        #触发信号
        self.ready_event.set()

    def on_click(self,event:events.Click) -> None:
        target =event.widget
        if target and target.id !="user-input":
            self.query_one("#user-input").focus()

    def _adjust_input_height(self, text_area: UserInputArea) -> None:
        """提交后把输入框高度收回单行。

        ``_process_submission`` 清空输入框后会调用本方法；缺失时（本方法此前未定义）
        每次提交都会抛 ``AttributeError``，直接打断会话主循环。
        高度计算复用 ``UserInputArea._adjust_height``，异常只记 debug。
        """
        try:
            text_area._adjust_height()
        except Exception as error:
            logger.debug("failed to adjust input height: %s", error)

    def action_submit(self)->None:
        if self.awaiting_interrupt_confirm:
            return
        text_area = self.query_one("#user-input", UserInputArea)
        user_input = text_area.text.strip()
        if not user_input:
            return
        if self._interrupt_handler is not None:
            _tui_trace("action_submit: interrupt branch, handler=%r" % (self._interrupt_handler,))
            self.pending_input = user_input
            self.awaiting_interrupt_confirm = True
            self.query_one("#interrupt-prompt").remove_class("hidden")
            self.set_focus(None)
            return
        _tui_trace("action_submit: normal branch, text=%r" % (user_input[:40],))
        self._process_submission(user_input)

    def _process_submission(self, user_input:str, mount=True)->None:
        text_area = self.query_one("#user-input", UserInputArea)
        text_area.text=""
        self._adjust_input_height(text_area)
        # 转义 markup 起始符，避免用户输入被当成 Rich 标签解析
        user_input = user_input.replace("[","\\[")

        if mount:
            chat_area = self.query_one("#chat-area")
            chat_area.mount(Static(f"{user_input}",classes="user-msg"))
            # Textual 8 没有 call_after_ready，只有 call_after_refresh
            self.call_after_refresh(chat_area.scroll_end,animate=False)

        if self._input_future  and not self._input_future.done():
            self._input_future.set_result(user_input)
            self._input_future = None
            _tui_trace("submission -> future set_result")
        else:
            # 主循环此刻没在等输入（例如 agent 仍在执行、或界面刚就绪）。
            #
            # 这里原先只塞进 ``self._pending_submissions``，而真正消费输入的
            # ``TUIObserver.get_user_input()`` 只读 ``observer._pending_user_inputs``，
            # 两个队列互不相通，且 ``pop_pending_submissions`` 全项目无人调用——
            # 于是排队消息永远不会被取走，现象就是「界面上消息发出去了、
            # 但永远没有回复」。
            #
            # 修复：优先投递给 observer 的队列（真实消费者）；拿不到 observer
            # 时才退回本地队列，并保留日志痕迹。
            delivered = False
            observer = getattr(self, "tui_application", None)
            observer = getattr(observer, "observer", None)
            pusher = getattr(observer, "push_pending_user_input", None)
            if callable(pusher):
                try:
                    pusher(user_input)
                    delivered = True
                    _tui_trace("submission -> QUEUED to observer (%d pending)"
                               % len(getattr(observer, "_pending_user_inputs", [])))
                except Exception:
                    logger.debug("push_pending_user_input failed", exc_info=True)
            if not delivered:
                self._pending_submissions.append(user_input)
                _tui_trace("submission -> QUEUED locally, pending=%d"
                           % len(self._pending_submissions))
                logger.warning(
                    "main loop is not waiting for input; queued submission (%d pending)",
                    len(self._pending_submissions),
                )

    def pop_pending_submissions(self)->Optional[str]:
        if not self._pending_submissions:
            return None
        return self._pending_submissions.pop(0)

    def ont_agent_start_interrupt(self) :
        self._is_interrupted = True

    def register_interrupt_handler(self, handler_func)->None:
        if handler_func:
            self._interrupt_handler = handler_func

    def remove_interrupt_handler(self)->None:
        self._interrupt_handler = None

    async def action_interrupt(self) -> None:
        """Esc：中断当前正在执行的 agent。

        按优先级依次处理：
        1. 补全列表开着 -> 只收起列表，不中断；
        2. 正在等「是否中断」确认 -> 视为取消（等同按 N）；
        3. 已注册中断回调 -> 置中断标志并调用回调（回调可以是协程）；
        4. 都没有 -> 退化为「清空输入框」。
        """
        try:
            from domains.permission.application.confirmation_registry import ConfirmationStrategy
            session_id = get_session_id()
            if session_id  and ConfirmationStrategy.check_interrupt(session_id):
                 return

            # 先调通过 register_interrupt_handler 注册的回调
            # （action_submit 正是靠它判断要不要弹「是否中断」确认框）
            handler = self._interrupt_handler
            if handler is not None:
                try:
                    result = handler()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.debug("interrupt handler raised", exc_info=True)

             # 通过TUIApplication 取消所有agent任务
            if self.tui_application and hasattr(self.tui_application,"cancel_all_agent_tasks"):
                 self.tui_application.cancel_all_agent_tasks()
            elif session_id:
                 await ConfirmationStrategy.signal_interrupt(session_id,source="user_esc")

               #停止全局spinner
            if self.tui_application and hasattr(self.tui_application,"observer"):
                observer =self.tui_application.observer
            if hasattr(observer,"_live_static") and observer._live_static:
                if hasattr(observer,"_active_block_id") and observer._active_block_id =="global_spinner":
                    observer._stop_active_live(transient=True)

             #将焦点设置回输入框（异步等待设置完成）
            try:
                 self.query_one("#user-input").focus()
            except Exception as e:
                 logger.debug("failed to schedule session list refresh", exc_info=True)

            #清除所有打断相关状态， 确保用户可以继续输入
            self.awaiting_interrupt_confirm = False
            self._interrupt_handler = None
            self.pending_input = ""

            #隐藏打断提示框（CSS 是 .hidden{display:none}，隐藏要 add_class）
            try:
                self.query_one("#interrupt-prompt").add_class("hidden")
            except Exception as e:
                 logger.debug("failed to schedule session list refresh", exc_info=True)

            self._is_interrupted = True
        except Exception as e:
            self.awaiting_interrupt_confirm = False
            self._interrupt_handler = None
            self.pending_input = ""
            self._is_interrupted = True

    def get_ready_event(self)->asyncio.Event:
        """返回 app 就绪事件；``TUIObserver._render_banner_after_ready`` 会等待它。"""
        return self.ready_event

    def add_text(self, text:str)->None:
        self.add_widget(Static(text))

    def add_widget(self, widget:Widget)->None:
        try:
            chat_area = self.query_one("#chat-area",VerticalScroll)
            self.safe_mount(chat_area,widget)
        except Exception as e:
            logger.error(e)

    def remove_widget(self, widget:Widget)->None:
        self.call_next(widget.remove)


    def safe_mount(self, parent, widget, widget_to_mount=None) -> bool:
        """把 widget 挂到容器上；原本贴底就继续贴底，任何异常只记日志。

        兼容两种调用形态：
        - ``safe_mount(parent, widget)``            —— 常规两参数；
        - ``safe_mount(parent, container, widget)`` —— 历史三参数形态，
          此时第二个参数是容器、第三个才是真正要挂载的对象。

        退出竞态
        --------
        挂载走 ``call_next``（排到 Textual 的回调队列，下一帧执行），而不是直接
        ``mount()``。问题是 agent 回复是**流式**的：LLM 一边吐字 observer 一边往
        聊天区挂 widget，而 ``/exit`` 随时可能把用户拽走。此时 Textual 已经
        ``exit()``、回调队列不再被消费，队列里排队的 ``chat_area.mount(...)``
        就落在「``#chat-area`` 自身尚未 is_mounted」的窗口里，抛::

            textual.widget.MountError: Can't mount widget(s) before
            VerticalScroll(id='chat-area') is mounted

        这个异常从 ``message_pump._flush_next_callbacks`` 里冒出来，被 Textual 判成
        「unhandled exception」，进而让 ``App.return_code=1``——**本来干净的退出
        被记成异常退出**，日志里还附一条误导性的 ``CLI APP EXIST ABNORMALLY``。

        修法：排队之前就判一次容器状态，未挂载则直接丢弃这次挂载（退出过程中的
        渲染没有价值），并对排队的回调本身兜一层异常。
        """
        chat_area = self.query_one("#chat-area",VerticalScroll)
        if widget_to_mount is None:
            # 两参数形态：第二个参数本身就是待挂载对象，父容器是 ``parent``。
            # 原实现固定执行 ``widget.mount(widget_to_mount)``，两参数调用时等价于
            # ``widget.mount(None)``，Textual 直接抛 MountError 打断会话。
            mount_parent, mount_widget = parent, widget
        else:
            mount_parent, mount_widget = widget, widget_to_mount
        # 退出过程中容器可能还没挂上（或已经在拆卸），此时挂载必失败，直接跳过。
        if not getattr(mount_parent, "is_mounted", False):
            logger.debug("safe_mount skipped: container not mounted")
            return False
        try:
            is_at_bottom = chat_area.scroll_offset.y >= chat_area.max_scroll_y
        except Exception:
            is_at_bottom = False
        self.call_next(mount_parent.mount, mount_widget)
        if is_at_bottom:
            self.call_after_refresh(chat_area.scroll_end, animate=False)
        return True

    def safe_update(self, widget, message) -> bool:
        # 与 ``safe_mount`` 同一个退出竞态：widget 可能已被卸载，
        # ``call_next`` 排队的 update 会在 Textual 退出后落到半死对象上。
        if not getattr(widget, "is_mounted", False):
            logger.debug("safe_update skipped: widget not mounted")
            return False
        try:
            chat_area = self.query_one("#chat-area",VerticalScroll)
            is_at_bottom = chat_area.scroll_offset.y >= chat_area.max_scroll_y
        except Exception:
            is_at_bottom = False
        self.call_next(widget.update,message)
        if is_at_bottom:
            self.call_after_refresh(chat_area.scroll_end, animate=False)
        return True

    def update_task_status_widget(self,plan_name:str, subtasks: List[Dict[str,Any]]) -> None:
        return


    @on(TextArea.Changed)
    async def on_text_changed(self, event:TextArea.Changed) -> None:
        if self.awaiting_interrupt_confirm:
            text = self.text_area.text
            if text:
                last_char= text[-1].lower()
                if last_char in ("y", "n"):
                    await self._handle_interrupt_confirm(last_char)
                    return
        self.trigger_completer()

    #----Session 列表管理
    def refresh_session_list(self)->None:
        return

    #--- 处理补全逻辑
    def trigger_completer(self) -> None:
        """按输入框当前文本与光标位置计算候选，并弹出补全列表。"""
        text = self.text_area.text
        row,col = self.text_area.cursor_location
        if "\n" in text:
            lines = text.split("\n")
            if row < len(lines):
                current_line = lines[row]
            else:
                current_line = text
        else:
            current_line = text

        content_width = self.text_area.content_region.width -self.text_area.gutter_width
        # wrapped_document 在当前 Textual 上不存在，别让它把整条补全链路打断
        wrapped = getattr(self.text_area, "wrapped_document", None)
        if wrapped is not None and content_width > 0:
            try:
                wrapped.wrap(content_width)
            except Exception:
                logger.debug("failed to wrap document for completion", exc_info=True)

        doc = Document(text=current_line,cursor_position=col)
        complete_event = CompleteEvent(completion_requested=True)
        completions = list(self.cli_comleter.get_completions(doc, complete_event))

        if completions:
            if len(completions) ==1:
                comp = completions[0]
                start_pos = col+comp.start_position
                already_typed = current_line[start_pos:col]
                if already_typed == comp.text:
                    self.hide_list()
                    return
            self.show_completions(completions,row,col)
        else:
            self.hide_list()

    def show_completions(self, completions: list[Completion], row: int, col: int) -> None:
        """把候选渲染到 #completion-list 并显示。

        :param completions: 候选列表（prompt_toolkit ``Completion``）。
        :param row: 触发补全时的光标行，记录后供回填定位使用。
        :param col: 触发补全时的光标列，用于把浮层左缘对齐到光标。
        """
        self.list_widget.clear_options()
        if not completions:
            self.hide_list()
            return

        max_name_len =0
        for c in completions:
            name_len = len(c.display_text)
            if name_len > max_name_len:
                max_name_len = name_len
        #限制第一列的最大宽度
        column_one_width = max_name_len
        #定义两列之间的间距宽度
        gutter_width = 3

        max_totoal_line_width =0

        for c in completions:
            line = Text(no_wrap=True)
            display_name=c.display_text
            line.append(display_name.ljust(column_one_width), style="bold #EEEEEE")
            line.append(" "*gutter_width)
            if c.display_meta:
                meta_text = str(to_plain_text(c.display_meta))
                line.append(meta_text,style="#777777")
            if len(line)> max_totoal_line_width:
                max_totoal_line_width = len(line)

            # add_options 收可迭代对象；这里是逐条添加，用单数的 add_option
            self.list_widget.add_option(Option(prompt=line, id=f"{c.text}|{c.start_position}"))

        #3. 动态调整补全框的尺寸
        final_width = max_totoal_line_width+4
        # App 上是 size.width，没有 size_width 这个属性
        final_width = min(final_width, int(self.size.width*0.8))
        self.list_widget.styles.width = final_width

        display_count = len(completions)
        visible_rows = min(display_count,10)
        self.list_widget.styles.height = visible_rows

        # 4. 定位逻辑：浮层放到光标正下方，越界就翻到光标上方
        try:
            cursor = self.text_area.cursor_screen_offset
            fx, fy = cursor.x, cursor.y + 1
            if fy + visible_rows + 2 > self.size.height:
                fy = max(cursor.y - (visible_rows + 2), 0)
            if fx + final_width > self.size.width:
                fx = max(self.size.width - final_width - 1, 0)
            self.list_widget.styles.offset = (int(fx), int(fy))
        except Exception as e:
            self.list_widget.styles.offset = (10,10)

        # add_class 在 Widget 上，不在 styles 上
        self.list_widget.add_class("show")
        self.list_widget.highlighted =0

    # 注意：不要再加 on_text_area_changed 转发。
    # on_text_changed 已经用 @on(TextArea.Changed) 注册，Textual 的按命名空间
    # 自动派发（on_text_area_changed）会让它被触发两次。
    def hide_list(self) -> None:
        """收起并清空补全列表。"""
        self.list_widget.remove_class("show")

    def handle_completion(self) -> None:
        """把当前高亮的候选项回填到输入框。"""
        if self.list_widget.highlighted is None:
            return
        selected = self.list_widget.get_option_at_index(self.list_widget.highlighted)
        replacement, start_pos = str(selected.id).split("|")
        start_offset = int(start_pos)

        # cursor_location 在输入框上；OptionList 没有这个属性
        row, col = self.text_area.cursor_location
        start_col =col+start_offset

        self.text_area.replace(replacement,start=(row,start_col), end=(row,col))
        self.text_area.cursor_location = (row,start_col+len(replacement))
        self.hide_list()
        self.text_area.focus()

    async def on_key(self, event) -> None:
        """全局按键路由：中断确认 > 补全导航 > 回车提交。"""
        if not self.awaiting_interrupt_confirm:
            return
        key=event.key.lower()
        await self._handle_interrupt_confirm(key)
        event.stop()
        event.prevent_default()

    async def _handle_interrupt_confirm(self, key: str) -> None:
        """处理「agent 正在执行，是否中断并发送新输入」的二次确认。

        :param key: 按键名。``y``/``enter`` 视为确认，``n``/``escape`` 视为取消，
            其它按键直接忽略（保持确认框继续等）。
        """
        if key=="y":
            self.awaiting_interrupt_confirm = False
            self.query_one("#interrupt-prompt").add_class("hidden")
            pending_input = self.pending_input
            #1. 执行打断
            await self.action_interrupt()
            #2. 等待信号 ，不阻塞UI刷新
            timeout = 5.0
            start_time = asyncio.get_event_loop().time()

            while not self._is_interrupted and self._interrupt_handler is not None:
                if asyncio.get_event_loop().time() - start_time > timeout:
                    break
                await asyncio.sleep(0.1)

            #3 提交
            self._process_submission(pending_input,False)
            self._is_interrupted = False
            self.query_one("#user-input").focus()
        elif key=="n":
            self.awaiting_interrupt_confirm = False
            self.query_one("#interrupt-prompt").add_class("hidden")
            self.text_area.text = self.pending_input
            self.pending_input = ""
            self.query_one("#user-input").focus()





















