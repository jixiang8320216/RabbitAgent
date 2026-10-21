"""输入溢出检查结果（InputOverflowResult）。

当一条用户输入（或它携带的引用/文件内容）超过阈值时，需要先把它「卸载」
成引用再交给模型，避免把上下文窗口一次性撑爆。这个类型是那次判定的回执。

**注意命名冲突**：``app/application/orchestrators/session_orchestrator.py`` 里
已经内联定义了一份同名 dataclass，且 ``__all__`` 里也导出了它。本模块是
``agent/model`` 下的规范实现，字段与那份**保持一致**（``offloaded`` / ``reason``
/ ``estimated_size`` / ``threshold`` / ``detail`` + ``not_offloaded`` 工厂），
这样两边可以互相替换而不会炸。

判定逻辑放在 :meth:`from_text`，方便 ``SessionTurnExecutor.check_input_overflow``
直接用：阈值缺失（``None``）即视为未启用，一律返回未卸载。
"""

from __future__ import annotations

import dataclasses
import typing

__all__ = ["InputOverflowResult"]


@dataclasses.dataclass(frozen=True)
class InputOverflowResult:
    """输入溢出检查的结果。

    :param offloaded: 该输入是否被判定为「需要卸载」（超长、需转成引用）。
    :param reason: 判定依据，便于日志定位（``disabled`` / ``under_threshold`` ...）。
    :param estimated_size: 输入估算大小（字符数）；无法估算时为 ``None``。
    :param threshold: 判定所用阈值；未配置（即未启用检查）时为 ``None``。
    :param detail: 附加信息，供卸载回调使用。
    """

    offloaded: bool = False
    reason: str = ""
    estimated_size: int | None = None
    threshold: int | None = None
    detail: dict[str, object] = dataclasses.field(default_factory=dict)

    @classmethod
    def not_offloaded(cls, reason: str, *, estimated_size: int | None = None,
                      threshold: int | None = None, **detail: object) -> "InputOverflowResult":
        """构造一个「未卸载」结果。

        ``estimated_size`` / ``threshold`` 走具名字段；其余关键字进 ``detail``。
        全部塞进 ``detail`` 会让调用方读 ``result.estimated_size`` 时拿到 ``None``。
        """
        return cls(
            offloaded=False,
            reason=reason,
            estimated_size=estimated_size,
            threshold=threshold,
            detail=dict(detail),
        )

    @classmethod
    def offload(cls, reason: str, *, estimated_size: int | None = None,
                threshold: int | None = None, **detail: object) -> "InputOverflowResult":
        """构造一个「需要卸载」结果。"""
        return cls(
            offloaded=True,
            reason=reason,
            estimated_size=estimated_size,
            threshold=threshold,
            detail=dict(detail),
        )

    @classmethod
    def from_text(
        cls,
        text: str | None,
        threshold: int | None,
        **detail: object,
    ) -> "InputOverflowResult":
        """按字符数阈值判定；阈值缺失即视为未启用。"""
        if threshold is None:
            return cls.not_offloaded("disabled", **detail)
        size = len(text) if isinstance(text, str) else 0
        if size > threshold:
            return cls.offload(
                "over_threshold", estimated_size=size, threshold=threshold, **detail
            )
        return cls.not_offloaded("under_threshold", estimated_size=size, threshold=threshold, **detail)

    def as_dict(self) -> dict[str, object]:
        return {
            "offloaded": self.offloaded,
            "reason": self.reason,
            "estimated_size": self.estimated_size,
            "threshold": self.threshold,
            "detail": dict(self.detail),
        }
