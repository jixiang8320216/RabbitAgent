
AppMode = str
BUILTIN_APP_MODES= frozenset(["delegate","roleplay","solo","guarded","groupchat","pipeline","parallel","debate"])
# delegate 委托模式(默认)---主agent委托子agent执行任务
#roleplay 角色扮演---agent以特定角色身份交互
#solo 单干模式--- 单个agent独立完成所有任务
#guarded 守护模式--- 带额外安全约束的执行模式
#groupchat 群聊模式 --- 多agent群体协作对话
#pipeline 流水线模式 ---固定顺序多阶段处理
#parallel 并行模式 --- 独立处理后汇总
#debate 辩证 --- 对抗性多agent+裁判
#guarded 双层agent， 规划-执行分离

def canonicalize_app_mode(value: object) -> AppMode:
    """把任意输入归一化成合法的应用模式名。

    归一化规则：``strip()`` + ``lower()``。合法值见 :data:`BUILTIN_APP_MODES`。

    :raises ValueError: 归一化后不是内置模式

    **为什么放在 domain 层**：模式名是领域概念（哪些模式合法由领域定义），
    而具体「从配置/环境变量读哪个」的裁决逻辑在 infrastructure 层。
    领域层不该反向依赖 infrastructure，因此这里自带一份实现。
    """
    if not isinstance(value, str):
        raise ValueError(f"Invalid app mode: {value!r}")
    normalized = value.strip().lower()
    if normalized not in BUILTIN_APP_MODES:
        raise ValueError(f"Unknown app mode: {value!r}")
    return normalized


def is_valid_app_mode(value: object) -> bool:
    """判断给定值是否是合法的应用模式名。"""
    return (
        isinstance(value, str)
        and value.strip().lower() in BUILTIN_APP_MODES
    )
