"""训练依赖的能力探测。

``trl`` 是可选依赖（``pip install trl``），因此需要探测其是否可用，
让调用方在缺少依赖时给出可操作的提示而不是 ImportError。
"""

try:
    import trl  # noqa: F401
    TRL_AVAILABLE = True
except ImportError:
    TRL_AVAILABLE = False

__all__ = ["TRL_AVAILABLE"]
