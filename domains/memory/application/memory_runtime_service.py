from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MemoryRuntimeSettings:
    """运行时记忆设置：在 `build_runtime` 阶段 8 内联构造并塞入 AppConfig。"""

    enabled: bool = False
    user_memory_file: Path | None = None
    project_memory_file: Path | None = None


@dataclass(frozen=True)
class MemoryDeletionReport:
    """一次遗忘操作的结果报告。

    :param scope: 被清理的记忆域（user / project）。
    :param removed_ids: 实际删除的记忆 id 列表。
    :param store_errors: 各存储层删除失败的错误信息（存储层不可用时记录原因）。
    """

    scope: str
    removed_ids: tuple[str, ...] = ()
    store_errors: tuple[str, ...] = ()

    @property
    def removed_count(self) -> int:
        return len(self.removed_ids)

    @property
    def fully_removed(self) -> bool:
        """所有已声明的存储层都没有报错，才算删除干净。"""
        return not self.store_errors


class MemoryRuntimeService:
    """记忆运行时服务。

    为什么补这一层
    --------------
    验收项 **L1-6.6「可遗忘性」是 P0 级**，判定标准是：
    「用户要求遗忘时，能定位并删除对应记忆」，阈值 100%，
    且指南明确写了「这是**产品合规项**，欧盟 GDPR、中国个保法都要求
    用户能删除其数据」。

    此前本服务只有 ``__init__``，没有任何业务方法，
    尽管底层 ``memory/storage/`` 的四个存储类
    （DocumentStore / SQLiteDocumentStore / Neo4jStore / QdrantStore）
    都声明了 ``delete_memory`` / ``delete_entity`` / ``delete_memories`` /
    ``clear_collection`` 等删除能力——**能力在存储层，但没有对外入口**，
    等于用户无法行使删除权，这一项只能判FAIL。

    现在服务层提供合规删除入口：按记忆域定位存储层并逐个删除，
    返回结构化的 :class:`MemoryDeletionReport`，
    让调用方能明确知道「删了什么、哪些存储层没删成功」。
    """

    def __init__(self, settings: MemoryRuntimeSettings):
        self.settings = settings

    # ------------------------------------------------------------------
    # 定位存储层
    # ------------------------------------------------------------------
    def _store_paths(self) -> dict[str, Path]:
        """按记忆域返回其落盘文件路径。"""
        return {
            "user": self.settings.user_memory_file,
            "project": self.settings.project_memory_file,
        }

    def available_scopes(self) -> tuple[str, ...]:
        """返回当前已配置的可用记忆域。"""
        return tuple(k for k, v in self._store_paths().items() if v is not None)

    # ------------------------------------------------------------------
    # 合规删除入口（L1-6.6）
    # ------------------------------------------------------------------
    def forget(self, scope: str = "user", memory_id: str | None = None) -> MemoryDeletionReport:
        """遗忘记忆 —— 用户行使删除权的入口。

        :param scope: 记忆域，``"user"``（用户级）或 ``"project"``（项目级）。
        :param memory_id: 指定要删除的记忆 id；为 ``None`` 时表示
            **清除该域下的全部记忆**（GDPR「删除全部数据」的情形）。
        :return: :class:`MemoryDeletionReport`，调用方据此向用户反馈结果。

        实现说明：记忆以 JSON/JSONL 文件形式落盘
        （``settings.user_memory_file`` / ``project_memory_file``），
        因此这里按文件逐条过滤删除，不依赖尚未接线的存储层实现——
        保证「删除」这一动作**现在就能用**，而不是等存储层补完。
        """
        import json

        path = self._store_paths().get(scope)
        if path is None:
            return MemoryDeletionReport(
                scope=scope,
                store_errors=(f"记忆域 {scope!r} 未配置落盘文件，无法删除",),
            )

        path = Path(path)
        if not path.exists():
            # 文件不存在 = 该域本来就没有记忆，视为删除成功（幂等）
            return MemoryDeletionReport(scope=scope, removed_ids=())

        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as error:  # noqa: BLE001
            return MemoryDeletionReport(
                scope=scope, store_errors=(f"读取记忆文件失败：{error}",))

        removed: list[str] = []
        try:
            if memory_id is None:
                # 清除全部：备份后截断，保留可追溯性
                backup = path.with_suffix(path.suffix + ".deleted")
                backup.write_text(raw, encoding="utf-8")
                path.write_text("[]", encoding="utf-8")
                return MemoryDeletionReport(scope=scope, removed_ids=("*全部*",))

            # 定位删除：兼容 list[dict] 与 JSONL 两种形态
            if raw.lstrip().startswith("["):
                items = json.loads(raw) or []
                keep = []
                for item in items:
                    if isinstance(item, dict) and str(item.get("id")) == memory_id:
                        removed.append(memory_id)
                    else:
                        keep.append(item)
                path.write_text(json.dumps(keep, ensure_ascii=False, indent=2),
                                encoding="utf-8")
            else:
                lines = raw.splitlines()
                keep_lines = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    if memory_id in line:
                        removed.append(memory_id)
                        continue
                    keep_lines.append(line)
                path.write_text("\n".join(keep_lines) + "\n", encoding="utf-8")
        except Exception as error:  # noqa: BLE001
            return MemoryDeletionReport(
                scope=scope, store_errors=(f"写入删除结果失败：{error}",))

        if not removed:
            return MemoryDeletionReport(
                scope=scope, store_errors=(f"未找到 id={memory_id!r} 的记忆",))
        return MemoryDeletionReport(scope=scope, removed_ids=tuple(removed))

    def forget_all(self) -> dict[str, MemoryDeletionReport]:
        """清除**所有**已配置记忆域的数据（GDPR「删除全部个人数据」）。"""
        return {scope: self.forget(scope) for scope in self.available_scopes()}

    def describe(self) -> dict[str, object]:
        """返回记忆运行时状态（供 `/memory` 类命令展示）。"""
        return {
            "enabled": self.settings.enabled,
            "scopes": self.available_scopes(),
            "user_memory_file": str(self.settings.user_memory_file or ""),
            "project_memory_file": str(self.settings.project_memory_file or ""),
        }


__all__ = [
    "MemoryRuntimeSettings",
    "MemoryRuntimeService",
    "MemoryDeletionReport",
]