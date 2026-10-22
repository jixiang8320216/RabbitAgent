"""路径越界防护。

指南 L4-2.2（路径穿越）标为 P0，要求 100% 覆盖：
``../`` 相对逃逸、绝对路径、符号链接、URL 编码、双重编码都必须被拦截。

为什么放在 permission 域：
    「某个路径是否位于授权根目录内」本质是权限裁决的一部分——它回答的是
    「这个操作是否越权」。放在 infrastructure 里会被工具层各自复制一份，
    最终出现「有的工具有校验、有的没有」的不一致缺口。

安全设计——**先解析再判定**：
    ``os.path.normpath`` 是纯字符串运算，无法阻止符号链接指向外部。
    这里统一用 ``Path.resolve()`` 把符号链接全部解析成真实路径后再判定，
    并额外做一次 ``os.path.realpath`` 兜底（覆盖 Windows junction/挂载点）。
    判定一律使用 ``is_relative_to``，不用字符串前缀比较——
    ``/data_evil`` 不应因为以 ``/data`` 开头而被判为在 ``/data`` 内。

判定语义：
    返回 True 表示**允许访问**。非法输入一律拒绝（fail-close）。
"""

from __future__ import annotations

import os
import pathlib
import typing

#: URL/双重编码的常见变体。判定前先做一次解码还原，
#: 否则 ``..%2f..%2f`` 会被当成普通文件名而绕过判定。
_ENCODINGS = ("utf-8",)


def _decode_repeatedly(value: str, max_rounds: int = 3) -> str:
    """反复尝试 URL 解码，直到结果不再变化。

    单次解码挡不住双重编码（``..%252f`` → ``..%2f`` → ``../``）。
    限制轮数避免构造出无终止的解码循环。
    """
    from urllib.parse import unquote

    current = value
    for _ in range(max_rounds):
        decoded = current
        for encoding in _ENCODINGS:
            try:
                decoded = unquote(decoded, encoding=encoding, errors="strict")
            except (UnicodeDecodeError, ValueError):
                continue
        if decoded == current:
            break
        current = decoded
    return current


def resolve_candidate(path: typing.Any, base: typing.Any | None = None) -> pathlib.Path:
    """把候选路径解析为绝对真实路径（展开 ``~``、``..``、符号链接）。

    :param path: 候选路径，可为相对路径。
    :param base: 相对路径的基准目录；为空时以当前工作目录为基准。
    :raises TypeError: ``path`` 不是字符串或 ``os.PathLike``。
    """
    # 非法输入必须显式拒绝：str(None) 得到 "None"、str(123) 得到 "123"，
    # 会被当成一个「恰好在授权目录内的文件名」而静默放行。
    if isinstance(path, (bytes, bytearray)):
        raise TypeError("路径必须是 str 或 os.PathLike，不接受 bytes")
    if not isinstance(path, (str, os.PathLike)):
        raise TypeError(f"路径类型非法：{type(path).__name__}")

    text = _decode_repeatedly(os.fspath(path) if isinstance(path, os.PathLike) else path)
    expanded = pathlib.Path(os.path.expanduser(text))
    if not expanded.is_absolute():
        root = pathlib.Path(base) if base is not None else pathlib.Path.cwd()
        expanded = pathlib.Path(root) / expanded
    # 解析符号链接与 .. / .；strict=False 保证路径不存在时也能判定
    resolved = expanded.resolve()
    # realpath 兜底：覆盖 junction / 挂载点等 resolve 未处理的场景
    return pathlib.Path(os.path.realpath(resolved))


def is_within(path: typing.Any, root: typing.Any) -> bool:
    """判断 ``path`` 是否位于 ``root`` 之内（含 root 自身）。

    :return: 位于授权目录内返回 True；越界或输入非法返回 False（fail-close）。
    """
    try:
        resolved_root = resolve_candidate(root)
        resolved_path = resolve_candidate(path, base=resolved_root)
    except (OSError, RuntimeError, ValueError, TypeError):
        # 解析失败（权限不足、路径非法、循环链接）一律拒绝
        return False
    try:
        return resolved_path.is_relative_to(resolved_root)
    except (AttributeError, ValueError):
        # 兼容不支持 is_relative_to 的旧环境
        try:
            resolved_path.relative_to(resolved_root)
            return True
        except ValueError:
            return False


def validate_path(
    path: typing.Any,
    root: typing.Any,
    *,
    allow_symlink_escape: bool = False,
) -> pathlib.Path:
    """校验路径归属，越界即抛 :class:`PathTraversalError`。

    :param allow_symlink_escape: 保留开关。当前实现**始终**按真实路径判定，
        该参数仅为让调用方意图显式化。
    :return: 校验通过的绝对真实路径，可直接用于后续文件操作。
    :raises PathTraversalError: 路径越出授权根目录。
    """
    resolved_root = resolve_candidate(root)
    resolved_path = resolve_candidate(path, base=resolved_root)
    if not is_within(resolved_path, resolved_root):
        raise PathTraversalError(
            f"路径越界：{path!r} 解析为 {resolved_path}，超出授权目录 {resolved_root}"
        )
    return resolved_path


class PathTraversalError(PermissionError):
    """路径逃逸出授权目录。

    继承 :class:`PermissionError` 而非 ``ValueError``，让调用方的
    ``except PermissionError`` 能一并捕获权限类问题。
    """


__all__ = [
    "PathTraversalError",
    "is_within",
    "resolve_candidate",
    "validate_path",
]