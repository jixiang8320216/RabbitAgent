"""可观测性领域模型。

本模块此前是**空文件**，而 ``observability/application/tracking.py``
第 103 行的函数签名里写着::

    def end_query_tracking(
        status: event_models.CancelStatusEnum = "ok",
        ...
    )

因为 ``tracking.py`` 启用了 ``from __future__ import annotations``，
类型注解不求值，所以运行时不报错、``end_query_tracking`` 也能正常调用——
这类「注解引用了不存在的符号」的缺陷非常隐蔽：

- 日常调用完全正常，问题不暴露；
- 但任何走 ``typing.get_type_hints()`` 的路径（pydantic 模型构建、
  依赖注入、文档生成、IDE 跳转）都会抛
  ``NameError: name 'CancelStatusEnum' is not defined``。

本模块按调用点实际使用的语义补齐该枚举：终态只有三种
``ok`` / ``cancel`` / ``error``（见 ``end_query_tracking`` 的 docstring：
「终态，调用方目前用 ``"ok"`` / ``"cancel"`` / ``"error"``」）。

这里刻意用 ``Literal`` 而不是 ``enum.Enum``：调用方传的是**裸字符串**
（``status="ok"``），不是枚举成员。用 ``Enum`` 会让 ``"ok"`` 与
``CancelStatusEnum.OK`` 成为不同对象，类型系统虽能校验，
运行期比较却不成立。``Literal`` 既约束了取值范围，
又与现有调用方式完全兼容。
"""

from __future__ import annotations

import typing

#: 一次查询的终态。
#:
#: - ``ok``：正常完成
#: - ``cancel``：被用户中断（ESC）或上层取消
#: - ``error``：因异常失败，配合 ``error_type`` 记录异常类名
CancelStatusEnum = typing.Literal["ok", "cancel", "error"]

#: 允许的全部终态值，供运行时校验使用。
CANCEL_STATUS_VALUES: tuple[str, ...] = typing.get_args(CancelStatusEnum)

__all__ = ["CancelStatusEnum", "CANCEL_STATUS_VALUES"]