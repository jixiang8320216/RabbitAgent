"""反思轨迹记忆。

原定义在 ``domains.agent.domain.aggregates.reflection_agent`` 中，作为
``ReflectionAgent`` 的内部辅助类。该 agent 类已由 ``ReActAgent`` 的
``preset_type="reflection"`` 预设承接并被删除，但本类有两个真实消费者：

- ``domains.memory.types.working``（对外 re-export，``domains.memory.types.Memory``
  的惰性导出目标指向它）；
- ``test/stage3_cost/test_l2_orchestration.py::test_reflection_memory``。

因此把它迁到 memory 域的正规位置，而不是随旧 agent 类一起删除。
"""

from typing import Any, Dict, List


class Memory:
    """简单的短期记忆模块，用于存储智能体的行动与反思轨迹。"""

    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []

    def add_record(self, record_type: str, content: str) -> None:
        """向记忆中添加一条新纪录"""
        self.records.append({"type": record_type, "content": content})
        print(f"📝 记忆已更新，新增一条 '{record_type}' 记录。")

    def get_trajectory(self) -> str:
        """将所有记忆记录格式化为一个连贯的字符串文本"""
        trajectory = ""
        for record in self.records:
            if record["type"] == "execution":
                trajectory += f"--- 上一轮尝试 (代码) ---\n{record['content']}\n\n"
            elif record["type"] == "reflection":
                trajectory += f"--- 评审员结论---\n{record['content']}\n\n"
        return trajectory.strip()

    def get_last_execution(self) -> str:
        """获取最近一次的执行结果"""
        for record in reversed(self.records):
            if record["type"] == "execution":
                return record["content"]
        return ""


__all__ = ["Memory"]
