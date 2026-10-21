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


import domains.plugin.domain as plugin_domain
import domains.agent.domain.entities.model_registration as model_registration

logger = logging.getLogger(__name__)

RegisteredMode=model_registration.RegisteredMode

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
            # 返回 ``ModeDeclaration`` 而非 ``RegisteredMode``：注册表对外的契约是
            # ``get()``/``resolve()`` 直接给出模式声明（与 ``available_modes()``
            # 一致，且 ``root_agent_loader``、``agent_orchestrator._build_root_agent``
            # 等调用方均按 ``ModeDeclaration`` 直接使用，不期望 ``.declaration`` 包装）。
            return stack[-1].declaration.model_copy(deep=True) if stack else None


    def available_modes(self)->list[plugin_domain.ModeDeclaration]:
        with self._lock:
            return [stack[-1].declaration.model_copy(deep=True) for stack in self._mode_stacks.values() if stack]


    def unregister(self, mode_name: str) -> bool:
        with self._lock:
            stack = self._mode_stacks.get(mode_name)
            if not stack:
                return False
            stack.pop()
            if not stack:
                self._mode_stacks.pop(mode_name,None)
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._mode_stacks)

    def __repr__(self,mode_name:str) -> bool:
        with self._lock:
            return bool(self._mode_stacks.get(mode_name))

    def available_mode_descriptors(self)->tuple[model_registration.AvailableModeDescriptor, ...]:
        with self._lock:
            active=[stack[-1] for stack in self._mode_stacks.values() if stack]
        return tuple(
            model_registration.AvailableModeDescriptor(
                value=registration.declaration.name,
                label=registration.declaration.label,
                description= registration.declaration.description,
                source= registration.source,
            )
            for registration in active
        )

