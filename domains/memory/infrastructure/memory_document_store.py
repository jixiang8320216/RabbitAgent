
"""规范基于文档存储：文件系统+SQLite FTS5 全文索引"""

import datetime
import difflib
import logging
import pathlib
import threading
import sqlite3

import domains.memory.domain.memory_document as memory_document

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_document_metadata(
    scope TEXT PRIMARY KEY,
    revision TEXT NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_document_chunks(
    scope TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    heading TEXT,
    content TEXT NOT NULL,
    PRIMARY KEY(scope, ordinal)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_document_chunks_fts USING fts5(
    scope UNINDEXED,
    ordinal UNINDEXED,
    heading,
    content,
    tokenize="trigram"
)
"""


class MemoryDocumentStore:
    """操作一份显式注入的规范记忆文档。

    架构说明
    --------
    - **正文**存放在文件系统（``document_path``）——可被用户直接查看与编辑；
    - **检索索引**存放在同目录的 ``*_fts.sqlite``（SQLite FTS5）——只加速搜索，
      正文永远是权威来源，索引丢了可以重建。

    历史问题（修复记录）
    --------------------
    本文件此前的**全部公开方法都缩进在 ``__init__`` 内部**（多了一层缩进），
    等于把它们变成了构造函数里的局部闭包——``MemoryDocumentStore`` 实例上
    一个公开方法都没有。调用方 ``MemoryRuntimeService`` 里的
    ``self._store(scope).read(...)`` / ``.save(...)`` / ``.search(...)``
    因此必然抛 ``AttributeError``，**记忆文档轨（写入/编辑/搜索/注入）整体不可用**。

    同时修掉了随缩进错位暴露出的若干契约错误：
    - ``ensure_documet`` -> ``ensure_document``（拼写）
    - ``MemoryDocument(...)`` 传了模型并不存在的 ``path=`` 字段
      （该模型 ``extra="forbid"``，会直接抛 ValidationError）
    - ``MemoryDocument.CURRETNT`` -> ``MemoryIndexStatus.CURRENT``
    - ``save()`` 接受 ``expected_revision``，而调用方传的是 ``experted_revision``
    - ``save()`` 不接受 ``source`` / ``reason``，但 ``save_with_result`` 会传
    - ``_get_index_conn`` 与实际定义的 ``_get_index_con`` 名字对不上
    - SQL 里 ``fst5`` -> ``fts5``、``ordinial`` -> ``ordinal``、
      ``indexex_at`` -> ``indexed_at``（与建表语句不一致）
    """

    def __init__(
        self,
        *,
        scope: memory_document.MemoryDocumentScope,
        document_path: pathlib.Path,
        injection_budget_chars: int = 40_000,
    ) -> None:
        self.scope = scope
        self.document_path = document_path
        self.injection_budget_chars = injection_budget_chars
        # 用 **RLock**（可重入）而不是 Lock：``save()`` 在持有锁的情况下会调用
        # ``read()``，而 ``read()`` -> ``_synchronize_index()`` -> ``_get_index_conn()``
        # 又要拿同一把锁。用普通 Lock 会**自我死锁**——表现为「保存记忆时进程
        # 永久挂起」，没有任何报错。
        self._lock = threading.RLock()
        # 索引文件：``<文档名>_fts.sqlite``，与正文同目录。
        # 用 ``with_name`` 拼接而不是 ``with_suffix`` —— 后者要求后缀以 "." 开头
        # 且不能含有额外路径分隔符，传 "_fts.sqlite" 会抛
        # ``ValueError: Invalid suffix '_fts.sqlite'``，记忆文档轨因此不可用。
        self._index_path = self.document_path.with_name(
            f"{self.document_path.stem}_fts.sqlite"
        )
        self._index_conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # 文档读写
    # ------------------------------------------------------------------
    def ensure_directory(self) -> pathlib.Path:
        """确保文档所在目录存在。"""
        self.document_path.parent.mkdir(parents=True, exist_ok=True)
        return self.document_path.parent

    def ensure_document(self) -> pathlib.Path:
        """确保文档文件存在（不存在则创建空文件）。"""
        self.ensure_directory()
        if not self.document_path.exists():
            self.document_path.write_text("", encoding="utf-8")
        return self.document_path

    def read(self, *, synchronize_index: bool = True) -> memory_document.MemoryDocument:
        """读取当前文档快照。"""
        exists = self.document_path.exists()
        content = self.document_path.read_text(encoding="utf-8") if exists else ""
        revision = memory_document._compute_revision(content)
        stat = self.document_path.stat() if exists else None
        updated_at = (
            datetime.datetime.fromtimestamp(stat.st_mtime, tz=datetime.timezone.utc)
            if stat
            else None
        )
        doc = memory_document.MemoryDocument(
            scope=self.scope,
            content=content,
            revision=revision,
            exists=exists,
            updated_at=updated_at,
            size_bytes=len(content.encode("utf-8")),
            index_status=memory_document.MemoryIndexStatus.CURRENT,
            exceeds_injection_budget=len(content) > self.injection_budget_chars,
        )

        if synchronize_index:
            self._synchronize_index(content, revision)
        return doc

    def save(
        self,
        content: str,
        *,
        expected_revision: str,
        source: str = "settings",
        reason: str | None = None,
    ) -> memory_document.MemoryDocument:
        """按乐观并发写回文档。

        :param expected_revision: 期望的当前版本号；与实际不符则抛冲突异常。
        """
        with self._lock:
            current = self.read(synchronize_index=False)
            if current.revision != expected_revision:
                raise memory_document.MemoryRevisionConflictError(
                    expected_revision=expected_revision,
                    actual_revision=current.revision,
                )
            self.ensure_directory()
            self.document_path.write_text(content, encoding="utf-8")
            logger.debug("memory document saved: scope=%s source=%s", self.scope, source)
            return self.read(synchronize_index=True)

    def save_with_result(
        self,
        content: str,
        *,
        expected_revision: str,
        source: str = "agent",
        reason: str | None = None,
    ) -> memory_document.MemoryDocumentMutation:
        """写回文档并返回带 diff 的变更结果。"""
        with self._lock:
            before = self.read(synchronize_index=False)
            after = self.save(
                content,
                expected_revision=expected_revision,
                source=source,
                reason=reason,
            )
            diff = "\n".join(
                difflib.unified_diff(
                    before.content.splitlines(keepends=True),
                    after.content.splitlines(keepends=True),
                    fromfile=f"{self.scope.value} (before)",
                    tofile=f"{self.scope.value} (after)",
                )
            )
            return memory_document.MemoryDocumentMutation(document=after, diff=diff)

    # ------------------------------------------------------------------
    # FTS 搜索
    # ------------------------------------------------------------------
    def search(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> list[memory_document.MemorySearchHit]:
        """在文档索引上做全文检索。

        中文可用性说明
        ------------
        SQLite FTS5 自带的 ``unicode61`` 分词器**不切分中文**——整段中文会被
        当作一个 token，导致「搜索中文词永远搜不到」。这里改用内置的
        ``trigram`` 分词器（SQLite 3.34+），它按 3 字符滑窗切分，对中英文都
        有效；但 FTS5 的 MATCH 语法要求查询词长度 **>= 3**，两字中文（如
        「中文」）仍会无命中。

        因此这里做**双路检索**：
        - 长查询（>= 3 字符）走 FTS5 索引（快，带排序）；
        - 短查询（1-2 字符）以及 FTS5 无命中时，回退到 ``LIKE`` 扫描
          （正确性兜底，代价是全表扫描——记忆文档体量小，可接受）。

        索引不可用时返回空列表而不是抛异常——搜索是**可选加速**，
        不该让「记忆读取」整体失败。
        """
        cleaned = (query or "").strip()
        if not cleaned:
            return []

        hits = self._search_fts(cleaned, limit)
        if hits:
            return hits
        return self._search_like(cleaned, limit)

    def _search_fts(
        self, query: str, limit: int,
    ) -> list[memory_document.MemorySearchHit]:
        """走 FTS5 索引检索（长查询专用）。"""
        # FTS5 trigram 要求查询词至少 3 个字符
        if len(query) < 3:
            return []
        try:
            conn = self._get_index_conn()
            rows = conn.execute(
                """
                SELECT chunks.heading, chunks.content, chunks.ordinal
                from memory_document_chunks_fts fts
                JOIN memory_document_chunks chunks
                  ON chunks.scope = fts.scope AND chunks.ordinal = fts.ordinal
                WHERE fts.content MATCH ? AND fts.scope=?
                ORDER BY rank
                LIMIT ?
                """,
                (f'"{query}"', self.scope.value, limit),
            ).fetchall()
        except sqlite3.Error as error:
            logger.debug("memory document fts search failed: %s", error)
            return []

        return [
            memory_document.MemorySearchHit(
                scope=self.scope,
                heading=row[0],
                content=row[1],
                ordinal=row[2],
            )
            for row in rows
        ]

    def _search_like(
        self, query: str, limit: int,
    ) -> list[memory_document.MemorySearchHit]:
        """LIKE 扫描兜底（短查询 / FTS5 无命中时）。

        对中文与英文都有效：``LIKE '%中文%'`` 是子串匹配，不依赖分词。
        """
        try:
            conn = self._get_index_conn()
            rows = conn.execute(
                """
                SELECT heading, content, ordinal
                from memory_document_chunks
                WHERE scope=? AND content LIKE ?
                ORDER BY ordinal
                LIMIT ?
                """,
                (self.scope.value, f"%{query}%", limit),
            ).fetchall()
        except sqlite3.Error as error:
            logger.debug("memory document like search failed: %s", error)
            return []

        return [
            memory_document.MemorySearchHit(
                scope=self.scope,
                heading=row[0],
                content=row[1],
                ordinal=row[2],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # 索引同步
    # ------------------------------------------------------------------
    def _synchronize_index(self, content: str, revision: str) -> None:
        """把正文同步进 FTS 索引（内容未变则跳过）。"""
        conn = self._get_index_conn()
        with self._lock:
            row = conn.execute(
                "SELECT revision FROM memory_document_metadata WHERE scope = ?",
                (self.scope.value,),
            ).fetchone()

            if row and row[0] == revision:
                return  # 索引已是最新

            # 清理旧索引
            conn.execute(
                "DELETE FROM memory_document_chunks WHERE scope = ?",
                (self.scope.value,),
            )
            conn.execute(
                "DELETE FROM memory_document_chunks_fts WHERE scope = ?",
                (self.scope.value,),
            )
            # 按 Markdown 标题分块
            chunks = self._chunk_markdown(content)
            for ordinal, (heading, body) in enumerate(chunks):
                conn.execute(
                    "INSERT INTO memory_document_chunks(scope, ordinal, heading, content) "
                    "VALUES (?,?, ?, ?)",
                    (self.scope.value, ordinal, heading, body),
                )
                conn.execute(
                    "INSERT INTO memory_document_chunks_fts(scope, ordinal, heading, content) "
                    "VALUES (?,?, ?, ?)",
                    (self.scope.value, ordinal, heading, body),
                )
            conn.execute(
                """
                INSERT INTO memory_document_metadata(scope, revision, indexed_at)
                VALUES (?,?, ?)
                ON CONFLICT (scope) DO UPDATE SET
                    revision = excluded.revision,
                    indexed_at = excluded.indexed_at
                """,
                (
                    self.scope.value,
                    revision,
                    datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
                ),
            )
            conn.commit()

    @staticmethod
    def _chunk_markdown(content: str) -> list[tuple[str | None, str]]:
        """按 Markdown 标题（##/###）分块。"""
        if not content.strip():
            return []

        chunks: list[tuple[str | None, str]] = []
        current_heading: str | None = None
        current_lines: list[str] = []
        for line in content.splitlines(keepends=True):
            stripped = line.strip()
            if stripped.startswith("##"):
                if current_lines:
                    chunks.append((current_heading, "".join(current_lines)))
                current_heading = stripped.lstrip("# ").strip()
                current_lines = [line]
            else:
                current_lines.append(line)
        if current_lines:
            chunks.append((current_heading, "".join(current_lines)))
        return chunks

    def _get_index_conn(self) -> sqlite3.Connection:
        """获取（并在首次调用时初始化）FTS 索引连接。"""
        with self._lock:
            if self._index_conn is None:
                self._index_path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(self._index_path), check_same_thread=False)
                conn.executescript(_SCHEMA)
                self._ensure_fts_tokenizer(conn)
                conn.commit()
                self._index_conn = conn
            return self._index_conn

    def _ensure_fts_tokenizer(self, conn: sqlite3.Connection) -> None:
        """确保 FTS 表使用 trigram 分词器。

        ``CREATE VIRTUAL TABLE IF NOT EXISTS`` 对**已存在**的表不会改变分词器。
        旧版本建的表用的是 ``unicode61``（不切分中文），若不重建，中文检索
        永远无命中。这里检测分词器并在需要时删表重建——索引只是加速层，
        重建不会丢正文（正文的权威来源是文件系统）。
        """
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='memory_document_chunks_fts'"
            ).fetchone()
        except sqlite3.Error:
            return
        if not row:
            return
        ddl = (row[0] or "").lower()
        if "trigram" in ddl:
            return
        logger.info(
            "重建记忆文档 FTS 索引以启用 trigram 分词器（中文检索）: %s",
            self._index_path,
        )
        try:
            conn.execute("DROP TABLE IF EXISTS memory_document_chunks_fts")
            conn.executescript(_SCHEMA)
        except sqlite3.Error as error:
            logger.debug("recreate fts table failed: %s", error)
