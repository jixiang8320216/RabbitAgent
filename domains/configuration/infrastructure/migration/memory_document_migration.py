
import pathlib
import sqlite3
import pydantic
import logging
from contextlib import closing

import domains.persistence.infrastructure.file as file_persistence
from domains.memory.infrastructure.memory_document_store import MemoryDocumentStore
import domains.memory.domain.memory_entry as memory_entry
import domains.memory.domain.memory_document as memory_document
import domains.memory.domain.memory_governance as memory_governance
import domains.memory.infrastructure.markdown.markdown_memory_repository as markdown_repository


logger = logging.getLogger(__name__)

_MIGRATION_MARKER_NAME=".memory-document-migration-v1-complete"
_MIGRATION_LOCK_NAME=".memory-document-migration.lock"

_TYPE_HEADINGS:dict[memory_entry.MemoryType,str]={
    memory_entry.MemoryType.ARCHITECTURE:"architecture",
     memory_entry.MemoryType.PITFALL:"Pitfalls",
     memory_entry.MemoryType.CONVENTION:"Conventions",
    memory_entry.MemoryType.PREFERENCE:"Preferences",
    memory_entry.MemoryType.DECISION:"Decisions",
    memory_entry.MemoryType.ERROR_SOLUTION:"Error solutions",
    memory_entry.MemoryType.PATTERN:"Patterns",
}

_TYPE_ORDER =tuple(_TYPE_HEADINGS)

_LEGACY_TYPE_ALIASES:dict[str,memory_entry.MemoryType]={
    "architecture": memory_entry.MemoryType.ARCHITECTURE,
    "pattern":memory_entry.MemoryType.PATTERN,
    "patterns":memory_entry.MemoryType.PATTERN,
    "pitfall": memory_entry.MemoryType.PITFALL,
    "pitfalls":memory_entry.MemoryType.PITFALL,
    "convension":memory_entry.MemoryType.CONVENTION,
    "convensions":memory_entry.MemoryType.CONVENTION,
    "decision":memory_entry.MemoryType.DECISION,
     "decisions":memory_entry.MemoryType.DECISION,
    "error-solution":memory_entry.MemoryType.ERROR_SOLUTION,
    "error_solution":memory_entry.MemoryType.ERROR_SOLUTION,
    "preference":memory_entry.MemoryType.PREFERENCE,
    "preferences": memory_entry.MemoryType.PREFERENCE,
}

class LegacyMemoryItem(pydantic.BaseModel):

    model_config = pydantic.ConfigDict(extra="forbid",frozen=True,strict=True)
    type: memory_entry.MemoryType
    content:str = pydantic.Field(min_length=1)
    source_type:str |None=None


class MemoryDocumentInitializationResult(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True,strict=True)
    user_memory_file: pathlib.Path
    project_memory_file: pathlib.Path|None = None

def initialize_memory_documents(
    *,
    user_data_home: pathlib.Path,
    project_home: pathlib.Path,
)-> MemoryDocumentInitializationResult:

    user_memory_file = (user_data_home / "memory"/"MEMORY.md").resolve(strict=False)
    _initialize_scope(
        scope=memory_document.MemoryDocumentScope.USER,
        root= user_memory_file.parent,
    )
    project_memory_file:pathlib.Path|None=None
    if project_home is not None:
        project_root=(project_home/".rabbit"/"memory").resolve(strict=False)
        try:
            _initialize_scope(
                scope = memory_document.MemoryDocumentScope.PROJECT,
                root = project_root,
            )
        except OSError as exc:
            logger.warning(exc)
        else:
            project_memory_file = project_root/"MEMORY.md"
    return MemoryDocumentInitializationResult(
        user_memory_file = user_memory_file,
        project_memory_file = project_memory_file,
    )


def _initialize_scope(*, scope:memory_document.MemoryDocumentScope, root: pathlib.Path) -> None:
    marker_path = root/_MIGRATION_MARKER_NAME
    # 一次性迁移的完成标记必须**在抢锁之前**检查。
    # 原实现先with file_lock(...) 再看marker_path.exists()，而 file_lock 在锁被
    # 占用时会阻塞到超时（本项目 30s）。于是每次启动都要白等锁超时，日志里出现
    # "Timed out waiting for file lock: .../.memory-document-migration.lock"，
    # 启动被硬拖慢 30 秒——即使迁移早在首次运行就已完成。
    # 迁移本身是幂等的：标记存在即代表已完成，直接返回，无需任何锁。
    if marker_path.exists():
        return
    with file_persistence.file_lock(root/_MIGRATION_LOCK_NAME):
        # 双检：拿到锁后可能已被并发的另一个进程完成。
        if marker_path.exists():
            return
        document_path = root/"MEMORY.md"
        existing = document_path.read_text(encoding="utf-8") if document_path.exists() else ""
        if not existing.strip():
            items = _read_markdown_entries(root,scope)
            source ="legacy_markdown_entries"
            if not items:
                items= _read_knowledge_database(root/"knowledge.db",scope)
                source="legacy_knowledge_db"
            content = _render_items(items)
            if content:
                store=MemoryDocumentStore(
                    scope =scope,
                    document_path =document_path,
                    inject_budget_chars = 40000,
                )
                try:
                    store.save(
                        content,
                        expected_revision=store.revision(existing),
                        source="migration",
                        reason=f"One-time initialization from {scope} ",
                    )
                except memory_document.MemoryRevisionConfflictError:
                    logger.error("skipped Memory migration because %s changed concurrently", document_path)

    file_persistence.atomic_write_text(
        marker_path,
        "Memory document migration v1 completed.\n",
        encoding="utf-8",
    )


def _read_markdown_entries(
    root: pathlib.Path,
    scope: memory_document.MemoryDocumentScope,
)->list[LegacyMemoryItem]:
    if not root.exists():
        return []
    legacy_scope = memory_entry.MemoryScope(scope.value)
    repository = markdown_repository.MarkdownRepository(legacy_scope,root)
    items: list[LegacyMemoryItem] = []
    try:
        for path in repository.iter_files():
            try:
                entry=repository.read_file(path)
            except (
                OSError,
                ValueError,
                markdown_repository.MarkdownRepositoryError,
            ) as error:
                logger.warning(error)
                continue
            if entry.status is memory_entry.MemoryStatus.ACTIVE and entry.content.strip():
                items.append(
                    LegacyMemoryItem(
                        type= entry.type,
                        content= entry.content.strip(),
                        source_type = entry.frontmatter.source_type,
                    )
                )
    finally:
        repository.close()
    return items

def _read_knowledge_database(
    database_path: pathlib.Path,
    scope: memory_document.MemoryDocumentScope,
)->list[LegacyMemoryItem]:
    if not database_path.exists():
        return []
    try:
        with closing(sqlite3.connect(str(database_path))) as connection:
            rows = connection.execute(
                "SELECT content,type,scope,source_type,confidence"
                "FROM memories WHERE status IS NULL OR status='active'"
            ).fetchall()
    except sqlite3.Error as exc:
       logger.warning("Unable to read legacy Memory database %s: %s", database_path, exc)
       return []

    items: list[LegacyMemoryItem]=[]
    seen: set[tuple[memory_entry.MemoryType,str]] = set()
    for raw_content,raw_type,raw_scope, raw_source_type,raw_confidence in rows:
        row_scope =str(raw_scope or scope.value).strip() or scope.value
        normalized_type = _LEGACY_TYPE_ALIASES.get(str(raw_type or "").strip().lower())
        if row_scope != scope.value and normalized_type is None:
            continue
        try:
            candidate = memory_governance.MemoryCandidate(
                content=str(raw_content or ""),
                type = normalized_type.value,
                scope = scope.value,
                source_type = str(raw_source_type or "migration"),
                confidence=str(raw_confidence or memory_entry.MemoryConfidence.FACT.value),
                actor="migration",
            )
        except ValueError:
            continue

        decision = memory_governance.run_memory_filters(candidate,duplicate_lookup=None)
        key=(normalized_type, memory_entry.normalize_for_hash(decision.sanitized_content))
        if decision.rejected or key in seen:
            continue
        seen.add(key)
        items.append(
            LegacyMemoryItem(
                type=normalized_type,
                content=decision.sanitized_content.strip(),
               source_type= candidate.source_type,
            )
        )
    return items

def _render_items(items: list[LegacyMemoryItem])->str:
    lines: list[str] = []
    for memory_type in _TYPE_ORDER:
        selected = [item for item in items if item.type == memory_type]
        if not selected:
            continue
        lines.extend((f"###{_TYPE_HEADINGS[memory_type]}", ""))
        for item in selected:
            body= item.content.replace("\r\n", "\n").replace("\r", "\n")
            indented = body.replace("\n", "\n ")
            source= item.source_type or "legacy"
            lines.append(f"-{indented}_(source: {source})_")
        lines.append("")
    return "\n".join(lines).rstrip()+("\n" if lines else "")

















