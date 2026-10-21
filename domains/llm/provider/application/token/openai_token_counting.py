import abc
import logging
import math
import unicodedata

from domains.llm.provider.domain import runtime_configuration

logger = logging.getLogger(__name__)

# OpenAI chat 消息的固定开销：``<im_start>{role/name}\n{content}<im_end>\n``
# 每条 3 个 token；``gpt-3.5-turbo-0301`` 这条老规则是 4 个。
_TOKENS_PER_MESSAGE = 3
_TOKENS_PER_MESSAGE_LEGACY = 4
_TOKENS_PER_NAME = 1
# 回复被引头（assistant 的 <|im_start|>assistant）固定 3 个 token。
_TOKENS_REPLY_PRIMED = 3

# 无法使用 tiktoken 时的启发式估算：CJK/全角字符按 1 token/字，其余按 4 字符/token。
_CHARS_PER_TOKEN = 4


class  TokenCounterBase(abc.ABC):

    @abc.abstractmethod
    async def count(self,
                    message: list[runtime_configuration.JsonObject],
                    **kwargs: object) -> int:
        """"""



class OpenAILocalTokenCounter(TokenCounterBase):

    def __init__(self, model_name:str)->None:
        self.model_name = model_name
        self.use_tiktoken = False
        self.encoder = None
    #ai shengcheng
    async def count(self,
                    message: list[runtime_configuration.JsonObject],
                    **kwargs: object) -> int:
        """估算这条（批）chat 消息会消耗多少 token。

        入参遵循 OpenAI 的消息形态：``[{"role": "user", "content": "..."}, ...]``。
        优先用 ``tiktoken`` 精确编码；没装或编码失败时退化成启发式估算，保证任何
        环境下都能返回一个可用的数字（token 计数不该成为主流程的中断点）。

        基类把它定义成 ``async`` 只是接口契约，本实现是纯本地计算、不 await 任何东西。
        """

        if message is None:
            return 0
        if isinstance(message, (list, tuple)):
            messages = list(message)
        else:
            # 单条消息/裸文本也接受，统一成列表处理。
            messages = [message]
        if not messages:
            return 0

        if not getattr(self, "_tiktoken_checked", False):
            # 只探测一次：装了就用，没装就永久走估算，避免每次调用都 import。
            self._tiktoken_checked = True
            try:
                import tiktoken

                try:
                    self.encoder = tiktoken.encoding_for_model(self.model_name)
                except Exception:  # noqa: BLE001 - 未知模型名退到通用编码
                    self.encoder = tiktoken.get_encoding("cl100k_base")
                self.use_tiktoken = True
            except Exception as exc:  # noqa: BLE001 - 没装 tiktoken
                self.encoder = None
                self.use_tiktoken = False
                logger.debug(
                    "tiktoken unavailable for model=%s (%s); falling back to estimation",
                    self.model_name, exc,
                )

        def _text_of(value: object) -> str:
            """递归取出可计数的文本（兼容多模态 content 分片）。"""
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                parts = []
                for key in ("text", "content"):
                    inner = value.get(key)
                    if isinstance(inner, str):
                        parts.append(inner)
                    elif isinstance(inner, (list, tuple, dict)):
                        parts.append(_text_of(inner))
                return "".join(parts)
            if isinstance(value, (list, tuple)):
                return "".join(_text_of(item) for item in value)
            if value is None or isinstance(value, (int, float, bool)):
                return "" if value is None else str(value)
            return str(value)

        def _encode_len(text: str) -> int:
            if not text:
                return 0
            if self.encoder is not None:
                try:
                    return len(self.encoder.encode(text))
                except Exception:  # noqa: BLE001 - 编码失败就退到估算
                    logger.debug("tiktoken encode failed; estimating instead")
            wide = sum(
                1 for ch in text if unicodedata.east_asian_width(ch) in ("W", "F")
            )
            rest = len(text) - wide
            return max(1, math.ceil(wide + rest / _CHARS_PER_TOKEN))

        tokens_per_message = (
            _TOKENS_PER_MESSAGE_LEGACY
            if "0301" in (self.model_name or "")
            else _TOKENS_PER_MESSAGE
        )

        total = 0
        for item in messages:
            total += tokens_per_message
            if not isinstance(item, dict):
                # 裸字符串或任意对象：整体按内容计一次。
                total += _encode_len(_text_of(item))
                continue
            for key, value in item.items():
                total += _encode_len(_text_of(value))
                if key == "name":
                    total += _TOKENS_PER_NAME
        total += _TOKENS_REPLY_PRIMED
        return total
