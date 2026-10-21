from prompt_toolkit.history import FileHistory
from textual.widgets import TextArea, OptionList
import logging
from typing import Protocol,cast
from domains.agent.domain.value_objects.runtime_context import get_user_config_dir
from collections.abc import Iterable
from textual import events


logger = logging.getLogger(__name__)


class _UserInputApp(Protocol):

    def handle_completion(self)->None:...

    def hide_list(self)->None:...

    def action_submit(self)->None:...



class UserInputArea(TextArea):
    MAX_HISTORY_ENTRIES=1000
    BINDINGS=[
        # 动作名必须与 ``action_select_all`` 对应，写成 ``selct_all`` 时 ctrl+a 不生效。
        ("ctrl+a","select_all","Select All")
    ]

    def on_mount(self)->None:
        # 基类 ``TextArea.on_mount`` 负责刷新滚动条，重写时必须先转发。
        super().on_mount()
        self.show_line_numbers =False
        self.styles.height =3
        self.focus()
        self._initialize_history()
        self.history_index =-1
        self.current_temp=""

    def _initialize_history(self)->None:
        self.history_store = self._create_history_store()
        self.chat_histroy =self._load_history_entries(self.history_store)


    def _create_history_store(self)->FileHistory|None:
        try:
            history_file = get_user_config_dir()/".input_history"
            history_file.parent.mkdir(exist_ok=True, parents=True)
            return FileHistory(str(history_file))
        except Exception as e:
            logger.debug("failed to create input history store: %s", e)
            return None

    def _load_history_entries(self,history_store: FileHistory|None)->list[str]:
        if history_store is None:
            return []
        try:
            newest_first=list(history_store.load_history_strings())
        except Exception as e:
            logger.debug("failed to load input history: %s", e)
            return []
        return self._deduplicate_history(reversed(newest_first))[-self.MAX_HISTORY_ENTRIES:]
    @staticmethod
    def _deduplicate_history(entries: Iterable[str])->list[str]:
        history:list[str]=[]
        for entry in entries:
            normalized = entry.strip()
            if normalized and (not history or normalized != history[-1]):
                history.append(normalized)
        return history

    def _append_history(self, countent:str)->None:
        normalized= countent.strip()
        if not normalized:
            return
        chat_history = getattr(self, "chat_histroy", None)
        if chat_history is None:
            return
        if chat_history and normalized == chat_history[-1]:
            return
        chat_history.append(normalized)
        if len(chat_history)>self.MAX_HISTORY_ENTRIES:
            self.chat_histroy = chat_history[-self.MAX_HISTORY_ENTRIES:]

        history_store=getattr(self,"history_store",None)
        if history_store is None:
            return
        try:
            history_store.append_string(normalized)
        except Exception as e:
            logger.warning(e)

    async def _on_key(self, event:events.Key)->None:
        """按键路由：补全列表导航 > 普通输入（回车提交/历史）> 交回基类插入字符。

        基类 ``TextArea._on_key`` 是异步协程，负责把可打印字符写进文档，
        因此只有本方法判定为「已消费」的按键才拦截，其余必须转发给基类，
        否则输入框会打不出字。
        """
        if self._route_key(event):
            return
        await super()._on_key(event)

    def _route_key(self, event:events.Key)->bool:
        app=cast(_UserInputApp, self.app)
        try:
            list_widget= self.app.query_one("#completion-list", OptionList)
            completion_visible = list_widget.has_class("show")
        except Exception as error:
            logger.debug("completion list unavailable: %s", error)
            list_widget=None
            completion_visible=False

        if completion_visible and list_widget is not None:
            return self._handle_completion_list_key(event, app,list_widget)
        return self._handle_normal_input_key(event,app)

    def _handle_completion_list_key(self, event:events.Key,app:_UserInputApp, list_widget:OptionList)->bool:
        if event.key  in ("enter","tab"):
            # 补全列表可见时按 enter 会走 handle_completion() 回填**高亮候选**。
            # 问题：输入 `/exit` 时补全列表正开着（且列表是在敲下第一个字符 `/` 时
            # 一次性列出全部 33 个命令，之后继续打字并不会重新过滤，列表始终陈旧），
            # 高亮项是 `agents`，回填后输入框变成 `/exitagents` —— 用户明明敲了
            # 完整命令，回车却被改写，结果 `/exit` 永远退不出去。
            # （E2E ConPTY 实测：退出后残留输出 `/exitagents`，进程不退出）
            # 正确语义：**已完整输入的已注册命令应当直接提交**，不做补全。
            # 判据见 _is_exact_command_match —— 问命令注册表而非陈旧的候选列表。
            # `/hel` 这类前缀不完整的输入仍走补全，补全体验不受影响。
            if event.key == "enter" and self._is_exact_command_match(list_widget):
                event.stop()
                event.prevent_default()
                app.hide_list()
                app.action_submit()
                return True
            event.stop()
            event.prevent_default()
            app.handle_completion()
            return True
        if event.key =="up":
            event.stop()
            event.prevent_default()
            self._navigate_completion(list_widget, direction=-1)
            return True
        if event.key =="down":
            event.stop()
            event.prevent_default()
            self._navigate_completion(list_widget, direction=1)
            return True
        if event.key =="escape":
            event.stop()
            event.prevent_default()
            app.hide_list()
            return True
        return False


    def _is_exact_command_match(self, list_widget:OptionList)->bool:
        """判断当前输入是否是一个**完整且已注册**的斜杠命令。

        为什么不能只看补全候选列表
        --------------------------
        实测（ConPTY E2E，_trace_submit.txt）输入 `/exit` 时列表里躺着
        **33 个候选**——补全列表是在敲下第一个字符 `/` 时一次性列全部命令，
        之后继续打字并不会重新过滤，列表始终是陈旧的。于是「按当前候选判断
        是否精确匹配」必然失真：`/exit` 看着像非精确（候选 agents 会把它改成
        `/exitagents`），但那只是列表没更新。

        正确判据是**问命令注册表**：输入去掉 `/` 后若恰好等于某个已注册命令名，
        说明用户已经敲完了完整命令，回车就应当直接提交，不能被补全改写。
        这样 `/exit`、`/help`、`/status` 等都能正常退出/执行，
        而 `/hel` 这类不完整输入仍走补全，保留原有体验。
        """
        try:
            text = self.text
        except Exception:
            return False
        stripped = text.strip()
        if not stripped.startswith("/"):
            return False
        # 只处理单个词：带子命令或参数的输入交给原有补全逻辑
        parts = stripped.split()
        if len(parts) != 1:
            return False
        wanted = parts[0].lstrip("/")
        if not wanted:
            return False

        registry = getattr(self.app, "command_registry", None)
        if registry is not None:
            lister = getattr(registry, "list_commands", None)
            if callable(lister):
                try:
                    for command in lister() or []:
                        name = (
                            command
                            if isinstance(command, str)
                            else getattr(command, "name", None)
                        )
                        if isinstance(name, str) and name.lower() == wanted.lower():
                            return True
                except Exception:
                    pass
        # 退化：app 上没有注册表时用命令名快照（构造期从注册表缓存）
        names = getattr(self.app, "_command_names", None)
        if isinstance(names, (set, frozenset)):
            return any(
                isinstance(name, str) and name.lower() == wanted.lower()
                for name in names
            )
        return False

    @staticmethod
    def _navigate_completion(list_widget:OptionList,direction:int)->None:
        option_count=list_widget.option_count
        if option_count:
            highlighted = list_widget.highlighted or 0
            list_widget.highlighted = (highlighted+ direction)%option_count

    def _handle_normal_input_key(self, event:events.Key, app:_UserInputApp)->bool:
        if event.key =="enter":
            event.prevent_default()
            event.stop()
            content= self.text.strip()
            self._append_history(content)
            app.action_submit()
            return True
        if event.key =="up":
            return self._handle_history_up(event)
        if event.key =="down":
            return self._handle_history_down(event)
        if event.key in ("ctrl+j","ctrl+enter"):
            self.insert("\n")
            self._adjust_height()
            event.prevent_default()
            event.stop()
            return True
        if event.key =="tab":
            event.stop()
            event.prevent_default()
            return True
        return False

    def _handle_history_up(self, event:events.Key)->bool:
        if self.cursor_location[0]!=0:
            return False
        chat_history = getattr(self, "chat_histroy", None)
        if not chat_history:
            return False
        event.stop()
        event.prevent_default()

        if self.history_index==-1:
            self.current_temp=self.text
        if self.history_index<len(chat_history)-1:
            self.history_index+=1
            self.text =chat_history[-(self.history_index+1)]
            self._move_cursor_to_end()
            self._adjust_height()
        return True


    def _handle_history_down(self, event:events.Key)->bool:
        if self.cursor_location[0]!=self.document.line_count-1:
            return False
        if self.history_index <0:
            return False
        chat_history = getattr(self, "chat_histroy", None)
        if not chat_history:
            return False
        event.stop()
        event.prevent_default()

        self.history_index-=1
        if self.history_index==-1:
            self.text= self.current_temp
        else:
            self.text=chat_history[-(self.history_index+1)]
        self._move_cursor_to_end()
        self._adjust_height()
        return True

    def action_select_all(self) -> None:
        self.select_all()

    def on_text_area_changed(self, event:TextArea.Changed)->None:
        # 实际方法名带下划线（``_adjust_height``）；写成 ``adjust_height`` 会在
        # 每次文本变化（含首次输入）时抛 AttributeError，直接把 TUI 打崩。
        self._adjust_height()

    def _move_cursor_to_end(self)->None:
        last_line_idex= self.document.line_count-1
        last_line_content = self.document[last_line_idex]
        self.move_cursor(last_line_idex, len(last_line_content))

    def _adjust_height(self)->None:
        display_line_count= self.wrapped_document.height
        target_text_lines = max(1,min(display_line_count,5))
        new_total_height = target_text_lines+2

        curretn_height = self.styles.height
        if curretn_height is not None:
            if hasattr(curretn_height,"value"):
                curretn_height = curretn_height.value
        if curretn_height is None or curretn_height != new_total_height:
            self.styles.height = new_total_height
            self.app.query_one("#input-container").styles.height = new_total_height
