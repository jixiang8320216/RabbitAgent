"""Hook 资源模型：配置快照、执行策略与运行期目录。

与 ``resource_catalog/infrastruction/local_hook_resources.py`` 的关系：
那里负责「从磁盘读并合并」，产出 :class:`HookResources`（带路径与指纹，
属于加载器内部产物）；这里定义「进入运行期后被消费的领域对象」，即
``HookSnapshot`` / ``HookPolicy``，它们不持有 IO 句柄，只承载数据。

这样分层的理由是：``RabbitApplicationCompositionState`` 需要在没有配置文件、
甚至完全没有 hook 运行时的场景下给出一个合法的空快照（``HookSnapshot.empty()``），
这个空快照必须与「读到了文件」的快照同型，才能让下游代码无分支地使用。
"""

from __future__ import annotations

import typing
from dataclasses import dataclass, field


@dataclass(frozen=True)
class HookPolicy:
    """Hook 执行策略。

    控制 hook 失败时是否中断主流程、以及单个 hook 的默认超时。默认值取「失败
    不阻断 + 10 秒超时」：hook 属于增强能力，其故障不应让用户的正常请求失败。
    """

    continue_on_error: bool = True
    default_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if self.default_timeout_seconds <= 0:
            raise ValueError("default_timeout_seconds must be positive")


@dataclass(frozen=True)
class HookSnapshot:
    """某一时刻生效的 hook 配置快照。

    ``hooks`` 是「事件名 -> hook 定义列表」的映射，键通常取 :class:`HookEvent`
    的字符串值，值是该事件下按顺序执行的 hook 定义。

    快照被设计为不可变（``frozen=True``），因为 hook 运行时会跨协程共享它；
    配置变更走「生成新快照并整体替换」的路径（``HookLifecycleCoordinator``
    负责），而不是原地修改，从而避免并发读到半更新的配置。
    """

    hooks: typing.Mapping[str, typing.Sequence[typing.Any]] = field(default_factory=dict)
    version: str = ""
    source_paths: tuple[str, ...] = ()

    @classmethod
    def empty(cls) -> "HookSnapshot":
        """返回一个不含任何 hook 的空快照。

        用于 hook 配置缺失（未启用、文件不存在、解析失败）时的降级路径，
        保证调用方无需判空。
        """
        return cls(hooks={}, version="", source_paths=())

    @property
    def is_empty(self) -> bool:
        """是否不含任何 hook。"""
        return not any(self.hooks.values())

    def event_names(self) -> tuple[str, ...]:
        """返回快照中出现过的事件名。"""
        return tuple(self.hooks.keys())

    def for_event(self, event: str) -> tuple[typing.Any, ...]:
        """返回指定事件下的 hook 定义序列；无配置时返回空元组。"""
        return tuple(self.hooks.get(str(event), ()) or ())


@dataclass(frozen=True)
class HookAgentCompomnentSource:
    """某个 Agent 的 hook 组件来源描述。

    ``HookAgentCompomnentSource`` 的拼写（``Compomnent``）沿用项目既定 typo，
    与 ``rabbit_application_composition.py`` 的调用点保持一致，避免两处不一致
    导致运行期 ``AttributeError``。
    """

    agent_id: str = ""
    app_id: str = ""
    source: str = ""
    detail: typing.Mapping[str, typing.Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookRuntimeDirectories:
    """Hook 运行期涉及的目录集合。

    由 composition 层在装配 hook 运行时构造，注入 ``HookManagementUseCases``，
    用于定位「项目级配置」与「rabbit 用户数据目录」这两处 hook 落盘位置。
    """

    project_dir: str = ""
    rabbit_data_dir: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_dir", str(self.project_dir or ""))
        object.__setattr__(self, "rabbit_data_dir", str(self.rabbit_data_dir or ""))
