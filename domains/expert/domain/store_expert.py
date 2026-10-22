"""专家在持久化存储中的领域层记录。

迁移把 ``expert`` 域折叠进 ``domains.expert`` 后，``StoreExpert`` 这一领域模型
在目标目录里缺失（原定义散落在被合并的源顶层包中），导致 ``expert_repository``
与 ``expert_source`` 在导入期引用 ``domains.expert.domain.StoreExpert`` 失败。

这里给出最小可用的领域记录模型：仅承载「专家在磁盘上的存储表示」所需的字段，
不参与任何运行期决策，因此保持宽松（``extra="ignore"``），避免与具体存储实现
的字段差异耦合。
"""

import pydantic


class StoreExpert(pydantic.BaseModel):
    """专家在持久化存储中的记录（领域层视图）。"""

    model_config = pydantic.ConfigDict(extra="ignore")

    expert_id: str = ""
    name: str = ""
    description: str = ""
    scope: str = "builtin"
    config_path: str | None = None
    prompt_path: str | None = None


__all__ = ["StoreExpert"]
