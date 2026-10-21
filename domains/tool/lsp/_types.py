"""domains.tool.lsp 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

from __future__ import annotations
import logging
import pathlib
import typing


"""LSP 工具集。

``RabbitApplicationCompositionState`` 里只声明了三个槽位::

    self._lsp_tools_instance: lsp_module.LSPTools | None = None
    self._lsp_root_paths: str | None = None
    self._lsp_language: str | None = None

即：**LSP 是可选的延迟能力**——装配阶段不做任何初始化，等到会话确定工作区
根路径与语言后，才按需构造 :class:`LSPTools`。

因此本模块的设计目标是「零成本的存在」：

- 构造不启动任何语言服务器进程、不做 IO；
- 未配置时不报错，只是 :attr:`LSPTools.available` 返回 ``False``；
- 真正的 server 连接由后续 :meth:`LSPTools.start` 触发，且失败只记录 warning。

这样既满足类型注解与调用点的存在性要求，又不会让「没有装 LSP server」的
环境在启动阶段就崩掉。

注意：本模块位于 ``tool/lsp/`` 包内而不是 ``tool/lsp.py``。尽管调用点写作
``import tool.lsp as lsp_module``（看起来像模块导入），Python 的包优先于同名
模块，因此实现必须放在包内，``tool/lsp.py`` 会被包目录遮蔽。
"""


logger = logging.getLogger(__name__)


class LSPTools:
    """语言服务器工具集（延迟初始化）。

    :param root_path: 工作区根目录；``None`` 时视为「尚未确定工作区」。
    :param language: 目标语言标识（如 ``"python"``）；``None`` 表示未指定。
    :param command: 显式指定语言服务器启动命令；``None`` 时交由后续策略推导。
    """

    __slots__ = ("_root_path", "_language", "_command", "_started")

    def __init__(
        self,
        root_path: str | pathlib.Path | None = None,
        language: str | None = None,
        *,
        command: typing.Sequence[str] | None = None,
    ) -> None:
        self._root_path = pathlib.Path(root_path).expanduser().resolve(strict=False) if root_path else None
        self._language = str(language) if language else None
        self._command = tuple(command) if command else None
        self._started = False

    # --- 只读状态 ---

    @property
    def root_path(self) -> pathlib.Path | None:
        """工作区根目录。"""
        return self._root_path

    @property
    def language(self) -> str | None:
        """目标语言标识。"""
        return self._language

    @property
    def command(self) -> tuple[str, ...] | None:
        """语言服务器启动命令。"""
        return self._command

    @property
    def started(self) -> bool:
        """语言服务器是否已启动。"""
        return self._started

    @property
    def available(self) -> bool:
        """当前是否具备可用条件（有工作区 + 有语言）。"""
        return self._root_path is not None and self._language is not None

    # --- 生命周期 ---

    def configure(
        self,
        *,
        root_path: str | pathlib.Path | None = None,
        language: str | None = None,
        command: typing.Sequence[str] | None = None,
    ) -> None:
        """更新配置；已启动的实例需要重新 ``start`` 才生效。"""
        if root_path is not None:
            self._root_path = pathlib.Path(root_path).expanduser().resolve(strict=False)
        if language is not None:
            self._language = str(language)
        if command is not None:
            self._command = tuple(command)

    async def start(self) -> bool:
        """启动语言服务器。

        当前阶段仅记录意图：真正的进程拉起与 LSP handshake 属于后续实现。
        返回是否具备启动条件，缺条件时只记 warning，不抛异常。
        """
        if not self.available:
            logger.warning(
                "LSP start skipped: root_path=%s language=%s",
                self._root_path,
                self._language,
            )
            return False
        self._started = True
        logger.info("LSP runtime configured for %s (%s)", self._root_path, self._language)
        return True

    async def stop(self) -> None:
        """停止语言服务器（幂等）。"""
        self._started = False

    def __repr__(self) -> str:
        return f"LSPTools(root_path={self._root_path!r}, language={self._language!r}, started={self._started})"
