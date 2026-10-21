from domains.permission.domain import ConfirmationStrategy


class CLIConfirmationStrategy(ConfirmationStrategy):
    """
    CLI/TUI 确认策略。

    构造时接收 ``TUIObserver``，用于在需要用户确认（如执行敏感工具、
    覆盖文件等）时通过 observer 向用户展示提示并等待应答。

    当前基类 ``ConfirmationStrategy`` 是空壳（无抽象方法），
    且启动流程尚未调用本类的确认方法，这里先保留 observer 引用，
    后续补 ``confirm``/``request_permission`` 等方法时即可直接使用。
    """

    def __init__(self, observer=None) -> None:
        self.observer = observer


#: 历史 typo 别名（见 ``domains/permission/cli_strategy.py`` 的同名别名说明）。
CLIConfirmationStartegy = CLIConfirmationStrategy
