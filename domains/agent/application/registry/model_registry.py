"""模式注册表。

``RabbitApplicationCompositionState`` 在启动时把内置模式定义逐个注册进来
（``self.mode_registry.register(mode_def, source="builtin")``），之后由
``PluginOrchestrator`` 读取当前可用模式集合。

关于命名：类名 ``ModelRegistry`` 沿用项目既定命名（实际承载的是
「模式 / mode」注册表，``plugin_orchestrator.py`` 以 ``ModeRegistry`` 别名
注解引用同一个类）。为避免两处不一致，这里保留原名，仅通过文档说明语义。

注册表是进程内共享的可变状态，因此用可重入锁保护读写；同名重复注册采用
「后写覆盖」策略并记录一条 warning，便于发现内置模式与插件模式的冲突。
"""

from __future__ import annotations

import collections.abc
import logging
import threading
import typing

import pydantic

import domains.plugin.domain as plugin_domain
import domains.agent.domain.entities.model_registration as model_registration

logger = logging.getLogger(__name__)

class RegisteredMode(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        arbitrary_types_allowed = True,
        extra = "forbid",
        frozen=True,
        strict = True
    )
    declaration: pydantic.InstanceOf[plugin_domain.ModeDeclaration]
    builder_override : collections.abc.Callable[...,object]|None=None
    source: str

    @property
    def name(self) -> str:
        """模式名称（取自声明），便于调用方无需深入 ``declaration`` 即可取用。"""
        return self.declaration.name

    def __getattr__(self, item: str) -> typing.Any:
        """把本模型上未定义的属性转发给被包装的模式声明。

        ``ModelRegistry.get()`` 返回的是 ``RegisteredMode`` 包装对象，但全库消费点
        都按 ``ModeDeclaration`` **直接**使用它：

        - ``AgentOrchestrator._interpret_mode``：``mode_decl.agent_slots`` /
          ``mode_decl.root_agent``
        - ``AgentOrchestrator._resolve_skill_agent_slot``：
          ``registered_mode.default_contribution_policy``
        - ``RootAgentLoader._root_slot``：``declaration.agent_slots``
        - ``GroupchatAgentBuilder._active_declaration``：整体当作声明返回

        此前两边契约不一致：包装对象上没有这些属性 → ``AttributeError:
        'RegisteredMode' object has no attribute 'agent_slots'``，
        TUI 启动即崩（``main.py`` → ``_create_agents`` → ``_build_root_agent_for_mode``）。

        在包装层做一次属性转发，使「包装对象」与「声明本身」两种用法都成立，
        且不影响 ``.name`` / ``.declaration`` 等自有属性。
        """
        # pydantic 模型字段存放在 __dict__；直接取，避免再次触发 __getattr__ 造成递归。
        declaration = self.__dict__.get("declaration")
        if declaration is not None:
            try:
                return getattr(declaration, item)
            except AttributeError:
                pass
        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {item!r}"
        )


__all__ = ["ModelRegistry"]


class ModelRegistry:
    """运行模式注册表（进程内，线程安全）。"""

    def __init__(self) -> None:
        self._mode_stacks: dict[str, list[RegisteredMode]]= {}
        self._lock = threading.RLock()

    def register(
        self,
        mode: plugin_domain.ModeDefinition,
        source: str="builtin",
    ) ->  None:
        self.replace_source({mode.declaration.name:mode},source=source, preserve_other_source_modes=True)

    def replace_source(
        self,
        modes: collections.abc.Mapping[str, plugin_domain.ModeDefinition],
        *,
        source:str,
        preserve_other_source_modes: bool = False,
    )->None:
        if not source:
            raise ValueError("source is required")
        registrations:list[tuple[str, RegisteredMode]]=[]
        for mode_name, mode in modes.items():
            if mode_name != mode.declaration.name:
                raise ValueError("mode name mismatch")
            if mode.declaration.root_agent not in mode.declaration.agent_slots:
                raise ValueError("mode name mismatch")
            registrations.append((
                mode_name,
                RegisteredMode(
                    declaration=mode.declaration,
                    builder_override=mode.builder_override,
                    source=source,
                )
            ))
        with self._lock:
            next_stacks= {name: list(stack) for name, stack in self._mode_stacks.items()}
            if preserve_other_source_modes:
                replaced_names=set(modes)
                for mode_name in replaced_names:
                    stack=next_stacks.get(mode_name,[])
                    next_stacks[mode_name]=[item for item in stack if item.source != source]
            else:
                for mode_name, stack in list(next_stacks.items()):
                    filtered= [item for item in stack if item.source != source]
                    if filtered:
                        next_stacks[mode_name]=filtered
                    else:
                        next_stacks.pop(mode_name,None)
            for mode_name,registration in registrations:
                next_stacks.setdefault(mode_name,[]).append(registration)
            self._mode_stacks= next_stacks


    def get(self, mode_name: str) -> RegisteredMode|None:
        """按名字取模式声明；不存在返回 ``None``。"""
        with self._lock:
            stack= self._mode_stacks.get(mode_name)
            return stack[-1] if stack else None

    def get_shadows(self, mode_name: str) -> list[RegisteredMode]:
        """按名字取模式声明；不存在返回 ``None``。"""
        with self._lock:
            stack = self._mode_stacks.get(mode_name,[])
            return list(stack[-1])

    def available_modes(self) -> list[str]:
        """返回当前可用模式的名称列表（去重，按注册顺序）。"""
        with self._lock:
            return list(self._mode_stacks.keys())

    def available_mode_descriptors(self) -> list[plugin_domain.ModeDeclaration]:
        """返回当前可用模式的声明列表。

        ``AgentOrchestrator._build_root_agent_for_mode`` 在「模式查不到」的错误
        分支里用它列出可用模式清单——此前该方法在 ``ModelRegistry`` 上并不存在，
        一旦走到那个分支就会二次抛 ``AttributeError``，把原始错误掩盖掉。
        """
        with self._lock:
            return [
                item.declaration
                for stack in self._mode_stacks.values()
                for item in stack
            ]

    def source_count(self)->dict[str,int]:
        with self._lock:
            counts:dict[str,int]= {}
            for stack in self._mode_stacks.values():
                for item in stack:
                    counts[item.source] =counts.get(item.source,0)+1
        return counts


    def unregister_by_source(self, source: str) -> bool:
        with self._lock:
            next_stacks= dict[str,list[RegisteredMode]]={}
            removed: list[str]=[]
            for mode_name, stack in self._mode_stacks.items():
                filtered= [item for item in stack if item.source != source]
                if len(filtered) !=len(stack):
                    removed.append(mode_name)
                if filtered:
                    next_stacks[mode_name]=filtered
            self._mode_stacks= next_stacks
            return removed

    def __len__(self) -> int:
        with self._lock:
            return len(self._mode_stacks)

    def __repr__(self,mode_name:str) -> bool:
        with self._lock:
            return bool(self._mode_stacks.get(mode_name))

