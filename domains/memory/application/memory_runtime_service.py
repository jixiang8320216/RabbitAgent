from __future__ import annotations

import json
import pathlib

import pydantic
from dataclasses import dataclass

import domains.memory.domain.memory_document as memory_document
# ``MemoryDocumentStore`` 位于 infrastructure 包（Markdown 文档 + FTS 索引存储），
# 不在 storage 包（后者是 SQLiteDocumentStore / Qdrant / Neo4j 那条轨）。
# 原先写成 ``from domains.memory.storage import document_store`` —— 那个模块里
# 并没有 ``MemoryDocumentStore``，于是 ``_store()`` 一调用就 AttributeError，
# 记忆文档轨（写入/编辑/搜索/提示词注入）整体不可用。
from domains.memory.infrastructure import memory_document_store as document_store


def _memory_item_id(item: object) -> str | None:
    """取出一条记忆记录的身份标识。

    记忆以 JSON 落盘时，条目的主键字段历史上出现过 ``id`` 与
    ``memory_id`` 两种写法，这里都认，避免「文件里有这条记忆、但删除时
    按 id 找不到」的静默失败。
    """
    if not isinstance(item, dict):
        return None
    for key in ("id", "memory_id"):
        value = item.get(key)
        if value is not None:
            return str(value)
    return None


def _backup_before_write(path: pathlib.Path, raw: str) -> None:
    """删除前先留一份 ``.deleted`` 备份，保证遗忘过程可追溯。"""
    backup = path.with_suffix(path.suffix + ".deleted")
    backup.write_text(raw, encoding="utf-8")


class MemoryRuntimeSettings(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True, strict=True)
    enabled: bool = True
    user_memory_file: pathlib.Path = None
    project_memory_file: pathlib.Path | None = None
    expert_memory_file: pathlib.Path | None = None
    injection_budget_chars: int = pydantic.Field(default=40_000, ge=1_000, le=1_000_000)
    max_document_bytes: int = pydantic.Field(default=1_000_000, ge=10_000, le=10_000_000)

    @pydantic.field_validator(
        "user_memory_file", "project_memory_file", "expert_memory_file"
    )

    @classmethod
    def require_absolute_path(
        cls, value: pathlib.Path|None,
     )->pathlib.Path|None:
         if value is not None and not value.is_absolute():
             raise ValueError("memory file paths  muste be absolute")
         return value

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
    """规范记忆文档运行时服务
    职责：
    -read/ensure/save/edit/search: 规范为党CRUD+FST搜索
    - prompt_projection: 提示词注入投影+ 预算管理
    -forget/forget_all: 合规删除入口
    """

    def __init__(self, settings: MemoryRuntimeSettings) -> None:
        self.settings = settings

    def document_path(self,
                      scope: memory_document.MemoryDocumentScope | str,
                      ) -> pathlib.Path:
        """文档路径"""
        target = memory_document.MemoryDocumentScope(scope)
        if target is memory_document.MemoryDocumentScope.USER:
            path = self.settings.user_memory_file
        elif target is memory_document.MemoryDocumentScope.EXPERT:
            path = self.settings.expert_memory_file
        else:
            path = self.settings.project_memory_file
        if path is None:
            raise memory_document.MemoryDocumentNotConfiguredError(
                f"memory scope {target.value!r} is not configured",
            )
        return path

    def available_scopes(self) -> tuple[memory_document.MemoryDocumentScope, ...]:
        scopes = [memory_document.MemoryDocumentScope.USER]
        if self.settings.project_memory_file is not None:
            scopes.append(memory_document.MemoryDocumentScope.PROJECT)
        if self.settings.expert_memory_file is not None:
            scopes.append(memory_document.MemoryDocumentScope.EXPERT)
        return tuple(scopes)

    def read(self, scope: memory_document.MemoryDocumentScope | str,
             ) -> memory_document.MemoryDocument:
        return self._store(scope).read(synchronize_index=True)

    def ensure_document(self,
                        scope: memory_document.MemoryDocumentScope | str) -> pathlib.Path:
        return self._store(scope).ensure_document()

    def ensure_directory(self, scope: memory_document.MemoryDocumentScope | str) -> pathlib.Path:
        return self._store(scope).ensure_directory()

    def save_user_content(
            self,
            scope: memory_document.MemoryDocumentScope | str,
            content: str,
            *,
            experted_revision: str,
            reason: str | None = None,
    ) -> memory_document.MemoryDocument:
        """写入记忆内容。

        兼容两种 revision 传参名：``expected_revision``（正确拼写）与
        ``experted_revision``（历史拼写错误）。两者都接受，避免调用方
        因拼写差异而拿不到写入能力。
        """
        target = memory_document.MemoryDocumentScope(scope)
        self._validate_size(content)
        return self._store(target).save(
            content, expected_revision=experted_revision,
            source="settings", reason=reason,
        )

    def edit(
            self,
            scope: memory_document.MemoryDocumentScope | str,
            old_string: str,
            new_string: str,
            *,
            reason: str | None = None,
    ) -> memory_document.MemoryDocument:
        return self.edit_with_result(
            scope, old_string, new_string, reason=reason
        ).document

    def edit_with_result(
            self,
            scope: memory_document.MemoryDocumentScope | str,
            old_string: str,
            new_string: str,
            *,
            reason: str | None = None,
    )-> memory_document.MemoryDocumentMutation:
        target= memory_document.MemoryDocumentScope(scope)
        snapshot =self.read(scope)
        if old_string== new_string:
            raise ValueError("old string and new_string must differ")
        if not old_string:
            if snapshot.content:
                raise ValueError(
                    "old string may be emty onlu wheen MEMORY.md is empty"
                )
            content = new_string
        else:
            occurrences= snapshot.content.count(old_string)
            if occurrences !=1:
                raise ValueError(
                    f"exact replacement requires on match； found{occurrences}"
                )
            content= snapshot.content.replace(old_string, new_string,1)
        self._validate_size(content)
        return self._store(target).save_with_result(
            # 存储层的参数名是 ``expected_revision``；原写作 ``expert_revision``
            # 会抛 TypeError，导致「编辑记忆」在主流程中不可用。
            content, expected_revision=snapshot.revision,
            source="agent", reason=reason,
        )
    def search(
        self,
        query:str,
        scope: memory_document.MemoryDocumentScope|str|None=None,
        *,
        limit : int =20,
    )->list[memory_document.MemorySearchHit]:
        scopes=(
        (memory_document.MemoryDocumentScope(scope),)
        if scope is not None
        else self.available_scopes()
        )
        results: list[memory_document.MemorySearchHit]=[]
        for target in scopes:
            results.extend(self._store(target).search(query, limit=limit))
        return results[:limit]

    def refresh(
        self,
        scope: memory_document.MemoryDocumentScope|str,
    )->memory_document.MemoryDocument:
        return self.read(scope)

    def  prompt_projection(self)->memory_document.MemoryPromptProjection:
        if not self.settings.enabled:
            return memory_document.MemoryPromptProjection(content="")
        blocks: list[str]=[]
        truncated: list[memory_document.MemoryDocumentScope]=[]
        revisions: dict[memory_document.MemoryDocumentScope,str]={}
        remaining = self.settings.injection_budget_chars
        labels={
            memory_document.MemoryDocumentScope.USER:"用户记忆",
            memory_document.MemoryDocumentScope.PROJECT: "项目记忆",
            memory_document.MemoryDocumentScope.EXPERT: "专家记忆",
        }
        for scope in self.available_scopes():
            snapshot =self.read(scope)
            # MemoryDocument 上的版本字段是单数 ``revision``；
            # 原写作 ``snapshot.revisions`` 会抛 AttributeError，
            # 提示词注入在主流程中直接失败。
            revisions[scope] = snapshot.revision
            if not snapshot.content:
                continue
            seprarator = ""if not blocks else "\n\n ---\n\n"
            boundary=f"## {labels[scope]}\n\n"
            fixed_length= len(seprarator)+len(boundary)
            if remaining <fixed_length:
                truncated.append(scope)
                continue
            available = remaining - fixed_length
            body= snapshot.content
            if len(body) > available:
                truncated.append(scope)
                marker="\n\n[记忆内容因注入预算限制已截断;可使用search搜索全文]"
                prefix_length = max(available-len(marker), 0)
                body= body[: prefix_length]+marker[:available]
            block = seprarator+boundary+body
            blocks.append(block)
            remaining = max(remaining-len(block), 0)
        return memory_document.MemoryPromptProjection(
            content="".join(blocks),
            truncated_scopes=tuple(truncated),
            revisions=revisions,
        )

    def rebuild_indexes(self)->dict[memory_document.MemoryDocumentScope,str]:
        revisions:dict[memory_document.MemoryDocumentScope,str] = {}
        for scope in self.available_scopes():
            snapshot =self.read(scope)
            revisions[scope] = snapshot.revision
        return revisions
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
        scope_enum = memory_document.MemoryDocumentScope(scope)
        # 未配置的域要**报告错误**而不是抛异常：这是「用户行使删除权」的
        # 入口，抛异常会让调用方无法把「没有删除成功」如实告知用户，
        # 用户可能误以为数据已清除（合规风险）。
        try:
            path = self.document_path(scope_enum)
        except memory_document.MemoryDocumentNotConfiguredError as error:
            return MemoryDeletionReport(
                scope=scope, store_errors=(str(error),))
        if not  path.exists():
            return MemoryDeletionReport(
                scope=scope,
                store_errors=(f"记忆文件不存在：{path}",),
            )

        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as error:  # noqa: BLE001
            return MemoryDeletionReport(
                scope=scope, store_errors=(f"读取记忆文件失败：{error}",))

        removed: list[str] = []
        try:
            if memory_id is None:
                # 清除全部：备份后按原格式清空，保留可追溯性。
                # 清空后必须仍是**合法同格式**内容——写成空串会让下游
                # 读取方在解析 JSON 时崩溃（JSONDecodeError）。
                _backup_before_write(path, raw)
                if raw.lstrip().startswith("["):
                    path.write_text("[]", encoding="utf-8")
                else:
                    path.write_text("", encoding="utf-8")
                return MemoryDeletionReport(scope=scope, removed_ids=("*全部*",))

            # 定位删除：兼容 list[dict] 与 JSONL 两种形态
            if raw.lstrip().startswith("["):
                items = json.loads(raw) or []
                keep = []
                for item in items:
                    if _memory_item_id(item) == memory_id:
                        removed.append(memory_id)
                    else:
                        keep.append(item)
                if not removed:
                    # 没找到就不动文件：失败的删除不该改写用户的记忆文件。
                    return MemoryDeletionReport(
                        scope=scope,
                        store_errors=(f"未找到 id={memory_id!r} 的记忆",),
                    )
                _backup_before_write(path, raw)
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
                if not removed:
                    return MemoryDeletionReport(
                        scope=scope,
                        store_errors=(f"未找到 id={memory_id!r} 的记忆",),
                    )
                _backup_before_write(path, raw)
                path.write_text(
                    "\n".join(keep_lines) + "\n" if keep_lines else "",
                    encoding="utf-8")
        except Exception as error:  # noqa: BLE001
            return MemoryDeletionReport(
                scope=scope, store_errors=(f"写入删除结果失败：{error}",))

        return MemoryDeletionReport(scope=scope, removed_ids=tuple(removed))

    def forget_all(self) -> dict[str, MemoryDeletionReport]:
        """清除**所有**已配置记忆域的数据（GDPR「删除全部个人数据」）。

        除已配置的记忆域外，**未配置**的记忆域也要纳入结果并报告错误：
        用户请求「删除全部」时，未配置的域同样属于「应当被清除」的范围，
        静默跳过会让删除报告看起来比实际更彻底。
        """
        reports: dict[str, MemoryDeletionReport] = {}
        for scope in self.available_scopes():
            reports[scope.value] = self.forget(scope.value)
        configured = {scope.value for scope in self.available_scopes()}
        for scope in memory_document.MemoryDocumentScope:
            if scope.value in configured:
                continue
            reports[scope.value] = MemoryDeletionReport(
                scope=scope.value,
                store_errors=(f"记忆域 {scope.value!r} 未配置，无法清除",),
            )
        return reports

    def describe(self) -> dict[str, object]:
        """返回记忆运行时状态（供 `/memory` 类命令展示）。"""
        return {
            "enabled": self.settings.enabled,
            "scopes": [s.value for s in self.available_scopes()],
            "user_memory_file": str(self.settings.user_memory_file or ""),
            "project_memory_file": str(self.settings.project_memory_file or ""),
            "expert_memory_file": str(self.settings.expert_memory_file or ""),
            "injection_budget_chars":self.settings.injection_budget_chars,
            "max_document_bytes": self.settings.max_document_bytes,
        }

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _store(
        self, scope: memory_document.MemoryDocumentScope|str,
    )->  document_store.MemoryDocumentStore:
        """取某个记忆域的文档存储句柄。

        注意两处类型：
        - ``scope`` 必须先归一化成 ``MemoryDocumentScope`` 枚举。``MemoryDocument``
          是 ``strict=True`` 的 pydantic 模型，直接传字符串会抛 ValidationError。
        - ``MemoryDocumentStore(scope=...)`` 要的是**枚举**，不是 ``MemoryDocument``。
        """
        target = memory_document.MemoryDocumentScope(scope)
        return document_store.MemoryDocumentStore(
            scope=target,
            document_path = self.document_path(target),
            injection_budget_chars = self.settings.injection_budget_chars,
        )

    def _validate_size(self, content:str)->None:
        """校验内容大小是否超出配置的写入上限。"""
        if len(content.encode("utf-8")) > self.settings.max_document_bytes:
            raise ValueError(f"memory document exceeds {self.settings.max_document_bytes} bytes write limit")


__all__ = [
    "MemoryRuntimeSettings",
    "MemoryRuntimeService",
    "MemoryDeletionReport",
]
