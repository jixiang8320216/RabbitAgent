"""domains.skill.domain 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import typing
from domains.skill.domain.runs import SelfEvolutionRun
from domains.skill.domain.lifecycycle_protection import  LifecycleDispatchResult,LifecycleDispatchStatus,LifecycleOutcomeStatus


__ALL__ = [
    "SelfEvolutionRun",
    "LifecycleDispatchResult",
    "LifecycleDispatchStatus",
    "LifecycleOutcomeStatus",
]
