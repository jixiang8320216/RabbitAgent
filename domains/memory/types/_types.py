"""domains.memory.types 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import typing
from domains.memory.types.working import WorkingMemory
from domains.memory.types.episodic import EpisodicMemory, Episode
from domains.memory.types.semantic import SemanticMemory, Entity, Relation
from domains.memory.types.perceptual import PerceptualMemory, Perception


"""记忆类型层模块

按照第8章架构设计的记忆类型层：
- WorkingMemory: 工作记忆 - 短期上下文管理
- EpisodicMemory: 情景记忆 - 具体交互事件存储
- SemanticMemory: 语义记忆 - 抽象知识和概念存储
- PerceptualMemory: 感知记忆 - 多模态数据存储
"""
