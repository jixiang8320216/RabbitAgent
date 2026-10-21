"""非 TUI 的 UI 应用层占位。

**当前状态**：本模块为空文件，**全工程无引用**。

原工程（``RabbitAgent``）中此文件同样是 0 字节空壳 —— 它是给
「非 Textual 的 UI 应用层」预留的位置：TUI 走 ``tui_application``，
而纯文本 / JSON 输出模式（ACP、管道集成）将来走这里。

**为什么保留而不是删掉**：``interfaces/cli`` 下已有
``tui_application.py``（Textual 交互式界面）与 ``args_parse.py``（参数解析），
纯文本模式是这套 CLI 的第三种形态，位置留着比删了再想好。

**注意**：原文件名为 ``relay_ui_application``，与 ``rabbit_cli`` 同属
``relay`` 命名残留；``interfaces/rabbit_cli`` 已合并进 ``interfaces/cli``，
此处一并统一为 ``rabbit`` 前缀。
"""

__all__: list[str] = []
