"""从 YAML 文件加载授权规则。

用途：让部署方在**不改代码**的前提下收紧或放宽授权策略。内置规则
（``domains.permission.domain.policy.default_rules()``）始终生效，本 provider
只负责**追加**外部规则。

YAML 结构（列表或带 ``rules:`` 键的映射皆可）::

    rules:
      - operation_type: shell_command
        pattern: "my-script\\.sh"
        risk_level: high        # low | medium | high | critical
        action: confirm          # allow | confirm | deny
        scope: once              # once | session | never
        description: "运行项目脚本"

失败策略——**降级而非抛错**：
    规则文件缺失或解析失败时返回空规则集（仅用内置规则），并记录警告。
    授权系统不可用时整体不可用，比「因为配置写错就整个 Agent 起不来」更可取；
    同时日志里会明确指出文件路径与原因，便于排查。
"""

from __future__ import annotations

import logging
import pathlib
import typing

logger = logging.getLogger(__name__)


class YamlAuthorizationRuleProvider:
    """从 YAML 文件读取授权规则的实现。"""

    def __init__(self, path: str | pathlib.Path | None = None) -> None:
        self._path = pathlib.Path(path) if path is not None else None
        self._rules: list[typing.Any] = []
        self._loaded = False

    @property
    def path(self) -> pathlib.Path | None:
        """规则文件路径；未配置返回 None。"""
        return self._path

    def rules(self) -> typing.Sequence[typing.Any]:
        """返回已加载的规则；首次调用时触发加载。"""
        if not self._loaded:
            self.reload()
        return tuple(self._rules)

    def reload(self) -> None:
        """重新读取规则文件。"""
        self._rules = []
        self._loaded = True
        if self._path is None:
            return
        if not self._path.exists():
            logger.warning("授权规则文件不存在，仅使用内置规则：%s", self._path)
            return
        try:
            import yaml

            payload = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        except Exception as error:  # noqa: BLE001 — 规则加载失败不应阻断授权系统
            logger.warning("授权规则文件解析失败，仅使用内置规则：%s（%s）", self._path, error)
            return

        self._rules = list(self._parse(payload))

    def _parse(self, payload: typing.Any) -> typing.Iterator[typing.Any]:
        """把 YAML 载荷转成授权规则对象。"""
        from domains.permission.domain.contracts import (
            AuthorizationRule,
            RiskLevel,
            RuleAction,
            Scope,
        )

        if payload is None:
            return
        entries = payload.get("rules") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            operation_type = entry.get("operation_type")
            pattern = entry.get("pattern")
            if not operation_type or not pattern:
                logger.warning("授权规则缺少 operation_type/pattern，已跳过：%r", entry)
                continue
            try:
                yield AuthorizationRule(
                    operation_type=str(operation_type),
                    pattern=str(pattern),
                    risk_level=RiskLevel(str(entry.get("risk_level", "medium"))),
                    action=RuleAction(str(entry.get("action", "confirm"))),
                    scope=Scope(str(entry.get("scope", "once"))),
                    description=str(entry.get("description", "")),
                )
            except Exception as error:  # noqa: BLE001 — 单条规则非法不应拖垮整份文件
                logger.warning("授权规则非法，已跳过：%r（%s）", entry, error)


__all__ = ["YamlAuthorizationRuleProvider"]