"""Hook 管理用例（应用层）。

``RabbitApplicationCompositionState`` 在配置 hook 运行时可用时构造本类，聚合
快照管理器、运行时、状态、运行记录存储、配置存储与目录信息，供 CLI / composition
层读取与管理 hook。

当前为最小可用实现：仅持有装配产物，未引入额外 IO 或决策逻辑——后续能力
（增删改查 hook 定义、配置热加载等）在此之上叠加即可。
"""


class HookManagementUseCases:
    """Hook 管理用例：聚合 hook 运行时的各项装配产物。"""

    def __init__(
        self,
        snapshots: object,
        runtime: object,
        state: object,
        hook_run_store: object,
        configuration_store: object,
        directories: object,
    ) -> None:
        self.snapshots = snapshots
        self.runtime = runtime
        self.state = state
        self.hook_run_store = hook_run_store
        self.configuration_store = configuration_store
        self.directories = directories


__all__ = ["HookManagementUseCases"]
