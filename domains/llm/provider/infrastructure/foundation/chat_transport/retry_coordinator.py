

class ProviderRetryCoordinator:
    """一次 provider 调用的「重试生命周期」：计数 / 退避 / 通知 / 收敛。

    调用方（``request.py`` 的 ``execute_on_stream`` / ``_execute_non_stream``）都是
    ``while True`` 循环——失败后 ``await coordinator.retry(cause, error=exc)``，
    然后回到循环顶部再试一次。这带来一条**必须遵守**的契约：

    **:meth:`retry` 只在「还能再试」时正常返回；否则必须抛出。**
    一旦它默默返回，调用方的 ``while True`` 就会变成无限重试。抛出时优先抛
    调用方传进来的原始异常（保住真实错误，别用「重试次数用尽」把它盖掉）。

    另外三种终态各自对应一个方法：

    * :meth:`recovered` —— 这次成了，清空失败计数；
    * :meth:`cancelled` —— 任务被取消（``CancelledError``），调用方紧接着会
      ``raise``，所以这里**只能通知、不能抛**；
    * :meth:`stream_interrupted` —— 流本身中途断掉，同样只通知。

    后两者会把协调器标记成终态：之后的 :meth:`retry` 一律立刻抛错——已经
    取消 / 中断过的请求不该再被静默重试一轮。

    状态对象用的是 ``runtime.model.provider_retry_status.ProviderRetryStatus``。
    那个模块现在因为 ``runtime.model.message`` 的 pydantic schema 生成失败而
    **导入即崩**，所以这里：懒加载 + 缓存失败结果 + 构造不出来就用同形状替身。
    等上游修好，本文件不用改就能自动切回真身。
    """

    def __init__(
        self,
        provider: str = "",
        model_name: str = "",
        operation_id: str | None = None,
        agent_name: str = "",
        observer: object | None = None,
        sleeper: object | None = None,
        *,
        max_attempts: int | None = None,
        max_retries: int | None = None,
        base_delay: float = 0.5,
        max_delay: float = 8.0,
        policy: object | None = None,
        logger: object | None = None,
        **extra: object,
    ) -> None:
        """``provider`` … ``sleeper`` 六个参数与 ``request.py`` 的构造顺序一致。

        ``sleeper`` 是「可中断睡眠」（``InterruptibleSleep``），退避等待一律走它，
        这样用户在退避期间按 ESC 能立刻打断，而不是干等完再发现已中断。
        拿不到（或不可调用）时退回 ``asyncio.sleep``。

        ``max_attempts`` / ``max_retries`` 是同一个东西的两个名字（重试次数上限，
        不含首次调用）；``policy`` 有 ``retry_delay`` 时优先用它的退避口径。
        ``**extra`` 只是兜底：将来调用方多传字段不至于直接 ``TypeError``。
        """
        self._provider = _text(provider) or "unknown-provider"
        self._model_name = _text(model_name)
        self._operation_id = _text(operation_id)
        self._agent_name = _text(agent_name)
        self._observer = observer
        self._sleeper = sleeper if callable(sleeper) else None
        self._policy = policy
        self._logger = logger if logger is not None else _LOGGER
        self._extra = dict(extra)

        limit = max_attempts if max_attempts is not None else max_retries
        self._max_attempts = _coerce_int(limit, default=_DEFAULT_MAX_ATTEMPTS, minimum=0)
        self._base_delay = _coerce_float(base_delay, default=_DEFAULT_BASE_DELAY, minimum=0.0)
        self._max_delay = max(
            self._base_delay,
            _coerce_float(max_delay, default=_DEFAULT_MAX_DELAY, minimum=0.0),
        )

        self._attempts = 0
        self._state: str | None = None
        self._terminal = False
        self._terminal_reason = ""
        self._last_cause: object | None = None
        self._last_error: BaseException | None = None
        self._slept_total = 0.0

    # --- 只读状态（给调用方 / 日志 / 测试看） ----------------------------

    @property
    def attempts(self) -> int:
        """已经失败过的次数（``retry`` 每次加一，``recovered`` 清零）。"""
        return self._attempts

    @property
    def max_attempts(self) -> int:
        """重试次数上限（不含首次调用）。"""
        return self._max_attempts

    @property
    def state(self) -> str | None:
        """当前状态名；一次都没失败过时是 ``None``。"""
        return self._state

    @property
    def is_terminal(self) -> bool:
        """是否已进入终态（取消 / 中断 / 重试用尽）。"""
        return self._terminal

    @property
    def last_error(self) -> BaseException | None:
        return self._last_error

    # --- 对外四个动作 ----------------------------------------------------

    async def retry(
        self,
        cause: object,
        error: BaseException | str | None = None,
        *,
        delay: float | None = None,
        reason: str | None = None,
    ) -> None:
        """记一次失败：等退避、通知 observer；**不能再试就直接抛**。

        三种抛错情形：

        1. 重试次数用尽（``attempts > max_attempts``）——先推 ``EXHAUSTING`` 再抛；
        2. ``cause`` 为 ``None``——调用方没给出重试理由，重试没有意义；
        3. 协调器已处于终态（取消 / 中断过）——不该再静默重试一轮。

        抛的都是 ``error`` 本体（是异常就直接抛，是字符串包成 ``RuntimeError``），
        因为「重试次数用尽」不是病因，真正的病因是那个 429 / 超时。
        """
        self._last_cause = cause
        if error is not None:
            self._last_error = error if isinstance(error, BaseException) else None

        if self._terminal:
            message = f"provider call already {self._terminal_reason or 'finished'}"
            raise _as_exception(error, message)

        if cause is None:
            # 没有重试理由还要重试 => 只会变成死循环，直接收敛掉。
            self._mark_terminal("exhausted")
            self._state = _STATE_EXHAUSTING
            await self._notify(
                _STATE_EXHAUSTING, cause, error=error, delay=0.0,
                note="retry requested without a cause",
            )
            raise _as_exception(error, "provider retry requested without a cause")

        self._attempts += 1
        if self._attempts > self._max_attempts:
            self._mark_terminal("exhausted")
            self._state = _STATE_EXHAUSTING
            await self._notify(_STATE_EXHAUSTING, cause, error=error, delay=0.0)
            raise _as_exception(
                error,
                f"provider retry exhausted after {self._max_attempts} attempt(s)"
                + (f" ({self._provider}/{self._model_name})" if self._model_name else ""),
            )

        wait = self._delay_for(self._attempts) if delay is None else _coerce_float(
            delay, default=0.0, minimum=0.0
        )

        self._state = _STATE_WAITING
        await self._notify(_STATE_WAITING, cause, error=error, delay=wait)

        # 退避前先探一次中断：已经按了 ESC 就没必要再干等一轮。
        if self._is_interrupted():
            self._mark_terminal("interrupted")
            self._state = _STATE_INTERRUPTED
            await self._notify(_STATE_INTERRUPTED, cause, error=error, delay=0.0)
            raise asyncio.CancelledError("interrupted while waiting for provider retry")

        await self._sleep(wait, reason or f"{self._provider}:{_cause_name(cause)}")
        self._slept_total += wait

        # 可中断睡眠本身被中断时上面就抛了；这里是「睡完了才发现已中断」的兜底。
        if self._is_interrupted():
            self._mark_terminal("interrupted")
            self._state = _STATE_INTERRUPTED
            await self._notify(_STATE_INTERRUPTED, cause, error=error, delay=0.0)
            raise asyncio.CancelledError("interrupted while waiting for provider retry")

        self._state = _STATE_RETRYING
        await self._notify(_STATE_RETRYING, cause, error=error, delay=wait)

    async def recovered(self) -> None:
        """这次调用成了：清空失败计数，必要时补一条 ``RECOVERING`` 通知。

        一次都没失败过就不通知——首轮就成功的调用不该在 UI 上刷「已恢复」。
        已经进入终态（取消 / 中断 / 用尽）后也不通知：那不是「恢复」。
        """
        if self._terminal:
            return
        had_failures = self._attempts > 0
        if had_failures:
            self._state = _STATE_RECOVERING
            await self._notify(_STATE_RECOVERING, self._last_cause, error=None, delay=0.0)
        self._attempts = 0
        self._last_cause = None
        self._last_error = None

    async def cancelled(self) -> None:
        """任务被取消：只通知，**永不抛**。

        调用点是 ``except asyncio.CancelledError: await coordinator.cancelled(); raise``
        ——这里抛任何东西都会盖掉原本的取消。
        """
        self._mark_terminal("canceled")
        self._state = _STATE_CANCELED
        await self._notify(_STATE_CANCELED, self._last_cause, error=self._last_error, delay=0.0)

    async def stream_interrupted(self, error: BaseException | str | None = None) -> None:
        """流在中途断掉（对端断开、流结束但没产出）：只通知，**永不抛**。

        与 :meth:`cancelled` 的差别：``cancelled`` 对应任务被取消（用户按 ESC），
        这里是流自己断了。两者都会把协调器置为终态——之后 :meth:`retry` 立刻抛错，
        因为「已经中断过」不是「可以再试一次」的状态。
        """
        if error is not None and isinstance(error, BaseException):
            self._last_error = error
        self._mark_terminal("interrupted")
        self._state = _STATE_INTERRUPTED
        await self._notify(_STATE_INTERRUPTED, self._last_cause, error=self._last_error, delay=0.0)

    # --- 内部：退避 / 睡眠 / 通知 ----------------------------------------

    def _mark_terminal(self, reason: str) -> None:
        self._terminal = True
        self._terminal_reason = reason

    def _delay_for(self, attempt: int) -> float:
        """第 ``attempt`` 次失败该等多久。

        优先用 ``policy.retry_delay``（调用方可能配了自己的退避口径），拿不到就
        指数退避 ``base_delay * 2 ** (attempt-1)``，最后用 ``max_delay`` 封顶。
        不抖随机：退避时长会被 UI 显示成「将在 Ns 后重试」，抖动只会让显示和
        实际对不上。
        """
        getter = getattr(self._policy, "retry_delay", None) if self._policy is not None else None
        if callable(getter):
            try:
                value = getter(max(0, attempt - 1))
            except Exception as exc:  # noqa: BLE001 - 退避算错不该拦住重试
                self._log("debug", "policy.retry_delay failed: %s", exc)
                value = None
            if value is not None:
                return _clamp(_coerce_float(value, default=self._base_delay, minimum=0.0),
                              0.0, self._max_delay)
        index = max(0, attempt - 1)
        raw = self._base_delay * (2 ** index) if self._base_delay else 0.0
        return _clamp(raw, 0.0, self._max_delay)

    async def _sleep(self, delay: float, reason: str) -> None:
        """走可中断睡眠；``sleeper`` 不可用时退回 ``asyncio.sleep``。

        ``InterruptibleSleep`` 声明成 ``(float, str) -> Awaitable[None]``，但不同
        实现有只收一个参数的版本，所以按形状依次试绑（用 ``signature.bind`` 而不是
        「先调再接 TypeError」——那样会把睡眼里自己抛的 TypeError 误判成签名不符）。

        ``CancelledError`` 一律放行：睡眠被打断就是被打断了，得让调用方知道。
        """
        if delay <= 0:
            return
        sleeper = self._sleeper
        if sleeper is None:
            await asyncio.sleep(delay)
            return
        ok, result = _bind_first(
            sleeper,
            (
                ((delay, reason), {}),
                ((delay,), {}),
                ((), {"delay": delay, "reason": reason}),
                ((), {"delay": delay}),
                ((), {"seconds": delay}),
            ),
        )
        if ok:
            if _is_awaitable(result):
                await result
            return
        self._log("debug", "sleeper rejected every known shape, falling back to asyncio.sleep")
        await asyncio.sleep(delay)

    def _is_interrupted(self) -> bool:
        """问 observer 是否已中断；探测不到或它自己抛错都当没中断。"""
        checker = getattr(self._observer, "is_interrupted", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception as exc:  # noqa: BLE001
            self._log("debug", "observer.is_interrupted failed: %s", exc)
            return False

    async def _notify(
        self,
        state_name: str,
        cause: object | None,
        *,
        error: BaseException | str | None = None,
        delay: float = 0.0,
        note: str = "",
    ) -> None:
        """把状态推给 observer：**任何失败都只记日志**。

        重试协调是旁路，observer 崩了（TUI 已退出、自定义 observer 没实现这个方法）
        不该反过来把整轮调用带崩。
        """
        text = _error_text(error)
        self._log(
            "warning" if state_name in (_STATE_EXHAUSTING, _STATE_CANCELED) else "info",
            "provider retry state=%s cause=%s attempt=%d/%d delay=%.2fs agent=%s model=%s%s",
            state_name,
            _cause_name(cause),
            self._attempts,
            self._max_attempts,
            delay,
            self._agent_name,
            self._model_name,
            f" error={text}" if text else "",
        )
        observer = self._observer
        if observer is None:
            return
        status = _build_retry_status(state_name, cause)
        await _anotify(
            observer,
            _OBSERVER_METHODS,
            status,
            on_fail=lambda exc: self._log("debug", "observer retry notification failed: %s", exc),
        )

    def _log(self, level: str, message: str, *args: object) -> None:
        """记日志；自定义 logger 没有对应级别（或干脆写失败）时静默跳过。"""
        logger = self._logger
        if logger is None:
            return
        emit = getattr(logger, level, None)
        if not callable(emit):
            emit = getattr(logger, "info", None)
        if not callable(emit):
            return
        try:
            emit(message, *args)
        except Exception:  # noqa: BLE001 - 日志不该影响主流程
            pass


# ----------------------------------------------------------------------
# 模块级小工具
#
# 放在类之后是刻意的：本文件顶部原本只有一个空壳类，新增代码一律往后追加。
# 这些名字只在方法体里运行期用到，定义顺序无所谓。
# ----------------------------------------------------------------------

import asyncio
import importlib
import inspect
import logging


_LOGGER = logging.getLogger(__name__)

_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BASE_DELAY = 0.5
_DEFAULT_MAX_DELAY = 8.0

_STATE_WAITING = "WAITING"
_STATE_RETRYING = "RETRYING"
_STATE_RECOVERING = "RECOVERING"
_STATE_EXHAUSTING = "EXHAUSTING"
_STATE_INTERRUPTED = "INTERRUPTED"
_STATE_CANCELED = "CANCELED"

# 替身对象的 ``state`` 取值：与 ``ProviderRetryState`` 的 **值** 对齐（注意
# ``RECOVERING`` 的值是 ``"recovered"``、``EXHAUSTING`` 是 ``"exhausted"``，
# 跟成员名并不一致）。真身不可用时观察方多半按值比较，取错就对不上了。
_STATE_VALUES = {
    _STATE_WAITING: "waiting",
    _STATE_RETRYING: "retrying",
    _STATE_RECOVERING: "recovered",
    _STATE_EXHAUSTING: "exhausted",
    _STATE_INTERRUPTED: "interrupted",
    _STATE_CANCELED: "canceled",
}

# ``RuntimeObserver`` 里叫这个名字；后两个是历史/别名写法，多试几个不吃亏。
_OBSERVER_METHODS = ("on_provider_retry_status", "on_retry_status", "on_provider_retry")

# ``runtime.model.provider_retry_status`` 的加载结果只试一次：它要么一直能用，
# 要么一直崩（pydantic schema 生成失败），没必要每次重试都再崩一遍。
_STATUS_TYPES: dict[str, object] = {"loaded": False, "status": None, "state": None}


def _text(value: object) -> str:
    """把任意值转成去空白的字符串；``None`` 转成空串。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _coerce_int(value: object, *, default: int, minimum: int = 0) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, number)


def _coerce_float(value: object, *, default: float, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(minimum, number)


def _clamp(value: float, low: float, high: float) -> float:
    if high < low:
        low, high = high, low
    return max(low, min(high, value))


def _is_awaitable(value: object) -> bool:
    return hasattr(value, "__await__")


def _cause_name(cause: object) -> str:
    """把重试原因规整成可读字符串：枚举取 ``name``，取不到取 ``value``。"""
    if cause is None:
        return "unknown"
    name = getattr(cause, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    value = getattr(cause, "value", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    text = _text(cause)
    return text or "unknown"


def _error_text(error: object) -> str:
    if error is None:
        return ""
    return _text(error)


def _as_exception(error: object, message: str) -> BaseException:
    """把 :meth:`retry` 的 ``error`` 变成可抛对象。

    异常原样返回（保住真实错误与 traceback）；字符串包成 ``RuntimeError``；
    什么都没有就用 ``message``。
    """
    if isinstance(error, BaseException):
        return error
    text = _error_text(error)
    if text:
        return RuntimeError(text)
    return RuntimeError(message)


def _bind_first(func: object, shapes: tuple[tuple[tuple[object, ...], dict[str, object]], ...]):
    """按候选形状依次试绑并调用，返回 ``(是否成功, 返回值)``。

    用 ``signature.bind`` 预检而不是「先调再接 ``TypeError``」：后者会把函数内部
    自己抛的 ``TypeError`` 误判成签名不符，然后拿另一套参数再调一次。
    ``signature`` 取不到（内建/扩展对象）时才退化成「直接调」。
    """
    if not callable(func):
        return False, None
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        signature = None
    for args, kwargs in shapes:
        if signature is not None:
            try:
                signature.bind(*args, **kwargs)
            except TypeError:
                continue
        try:
            return True, func(*args, **kwargs)
        except TypeError:
            continue
    return False, None


async def _anotify(target: object, names: tuple[str, ...], *args: object, on_fail=None) -> bool:
    """依次尝试 ``names`` 里的 observer 方法，成功一个就返回。

    同步返回值的当普通调用处理；返回协程/awaitable 的 await 掉。方法不存在、
    签名不符、抛错——一律跳过（``on_fail`` 只用来记日志），绝不让 observer
    反过来影响主流程。
    """
    for name in names:
        method = getattr(target, name, None)
        if not callable(method):
            continue
        ok, result = _bind_first(method, (((args, {}),)))
        if not ok:
            continue
        try:
            if _is_awaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001
            if on_fail is not None:
                try:
                    on_fail(exc)
                except Exception:  # noqa: BLE001
                    pass
            continue
        return True
    return False


def _retry_status_types() -> tuple[object | None, object | None]:
    """取 ``ProviderRetryStatus`` / ``ProviderRetryState``，只试一次。

    模块现在因 ``runtime.model.message`` 的 pydantic schema 生成失败而导入即崩，
    所以这里必须懒加载并缓存失败结果——否则每次重试都陪它崩一遍。
    """
    if _STATUS_TYPES["loaded"]:
        return _STATUS_TYPES["status"], _STATUS_TYPES["state"]
    status = None
    state = None
    try:
        module = importlib.import_module("runtime.model.provider_retry_status")
    except Exception:  # noqa: BLE001 - 上游未修好前这里是常态
        module = None
    if module is not None:
        status = getattr(module, "ProviderRetryStatus", None)
        state = getattr(module, "ProviderRetryState", None)
        if not isinstance(status, type):
            status = None
        if not isinstance(state, type):
            state = None
    _STATUS_TYPES["loaded"] = True
    _STATUS_TYPES["status"] = status
    _STATUS_TYPES["state"] = state
    return status, state


def _state_member(state_cls: object | None, name: str | None) -> object | None:
    """按名字取状态枚举成员；取不到再按值（忽略大小写）找一遍。"""
    if state_cls is None or not name:
        return None
    member = getattr(state_cls, name, None)
    if member is not None:
        return member
    lowered = name.lower()
    for candidate in getattr(state_cls, "__members__", {}).values():
        value = getattr(candidate, "value", None)
        if isinstance(value, str) and value.lower() == lowered:
            return candidate
    return None


def _build_retry_status(state_name: str, cause: object | None) -> object:
    """构造 ``ProviderRetryStatus``；构造不出来就退回 :class:`_FallbackRetryStatus`。

    形状从「信息最全」往「最少」退化：现在那个 model 是 ``extra="forbid"`` 的
    两字段版本，多给一个键就会被拒；等它以后长出 ``attempt`` / ``delay`` 这些字段，
    第一个形状自然就会命中，本文件不用改。

    另外它的 ``cause`` 字段目前标注成 ``ProviderRetryState``，而真实传进来的
    ``cause`` 是 ``retry_policy.ProviderRetryCause``（另一个枚举），strict 模式下
    会被拒——所以第二步退化成「拿 state 当 cause」，再不行才用替身。
    """
    status_cls, state_cls = _retry_status_types()
    state_member = _state_member(state_cls, state_name)
    if status_cls is None:
        return _FallbackRetryStatus(_STATE_VALUES.get(state_name, state_name.lower()), cause)

    cause_name = _cause_name(cause)
    cause_member = _state_member(state_cls, cause_name)
    state_value = state_member if state_member is not None else state_name
    cause_value = cause if cause is not None else (cause_member or state_value)
    # 同名但不同枚举时用不了，退化成 state 成员
    fallback_cause = cause_member or state_value

    for kwargs in (
        {"state": state_value, "cause": cause_value},
        {"state": state_value, "cause": fallback_cause},
        {"state": state_value},
    ):
        try:
            return status_cls(**kwargs)
        except Exception:  # noqa: BLE001 - 校验失败就换下一个形状
            continue
    return _FallbackRetryStatus(state_value, cause_value)


class _FallbackRetryStatus:
    """``ProviderRetryStatus`` 的替身：字段取不到真身时保证 observer 仍有东西可收。

    只保证 ``state`` / ``cause`` 两个属性存在（协议就只看这两个），外加一个
    ``model_dump()`` 方便打印与测试。
    """

    __slots__ = ("state", "cause")

    def __init__(self, state: object, cause: object | None = None) -> None:
        self.state = state
        self.cause = cause if cause is not None else state

    def model_dump(self) -> dict[str, object]:
        return {"state": self.state, "cause": self.cause}

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"_FallbackRetryStatus(state={self.state!r}, cause={self.cause!r})"
