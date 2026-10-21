"""Agent 执行器：把 async agent 任务放到独立线程的事件循环里跑。

**为什么需要它**：TUI（Textual）自身占用主线程的事件循环，而 agent 的
LLM 调用是长耗时的协程。如果直接在主循环里 await，界面会在等待期间失去响应。
本模块起一个后台线程 + 独立 event loop，主循环通过
:meth:`AgentExecutorThread.submit_async` 跨线程提交任务并等待结果。

**归属**：本模块原为独立包 ``interfaces/rabbit_cli``，但它只有一个消费方
—— ``interfaces.cli.tui_application``。作为 CLI 应用层的技术设施，
归入 ``interfaces.cli`` 更贴合调用关系，也消除了「同一层里两个都叫 CLI
的包」的结构歧义。

**代理环境注入**：线程启动时会读运行时配置，把代理设置注入到进程环境变量
（``_inject_proxy_env_from_runtime_config``）—— 子线程内的 HTTP 客户端
需要继承这些环境变量才能走代理。
"""

import asyncio
import base64
import os
import concurrent
import threading
from contextvars import copy_context
from typing import Optional, Awaitable, cast, TypeVar
import logging
from domains.permission.infrastructure.security.credential_encryption import  get_encryption_service
from shared.network.proxy_environment import HTTP_PROXY_KEYS,HTTPS_PROXY_KEYS,ProxyEnvBuilder

from domains.agent.domain.aggregates.execution_context import get_effective_runtime_config


logger = logging.getLogger(__name__)
T=TypeVar("T")

def _inject_proxy_env_from_runtime_config()->None:
    if any(os.environ.get(key) for key in HTTP_PROXY_KEYS+ HTTPS_PROXY_KEYS):
        return

    try:
        config= get_effective_runtime_config()
        if not config or not config.get("useProxy",False):
            return
        proxy_password = config.get("proxy_password") or config.get("proxy_password")
        if isinstance (proxy_password,str) and proxy_password.startswith("ENC:"):
           try:
               proxy_password=get_encryption_service().decrypt(proxy_password)
           except Exception as e:
               proxy_password=""

        overlay,_cleanup_keys= ProxyEnvBuilder.build_overlay(
            use_proxy=True,
            http_proxy=config.get("httpProxy") or config.get("http_proxy"),
            https_proxy=config.get("httpsProxy") or config.get("https_proxy"),
            proxy_username = config.get("proxyUsername") or config.get("proxyUsername"),
            proxy_password = proxy_password,
            no_proxy = config.get("noProxy") or config.get("noProxy"),
            current_env=os.environ,
        )
        os.environ.update(overlay)
    except Exception as e:
        logger.error(e)

class AgentExecutorThread:
    """
    Agent 执行器：负责提交并执行 async agent 任务。

    当前为简化骨架：``is_running`` 用 bool 标志，``submit_async`` 直接在
    调用方事件循环里 ``await coro``（不跨线程），足以让 TUI/CLI 启动流程
    跑通。后续如需真正的后台线程执行，可在此扩展为 ``threading.Thread``
    + 独立 ``asyncio`` loop 的跨线程提交器。
    """

    def __init__(self) -> None:
        self._loop:Optional[asyncio.AbstractEventLoop] = None
        self._thread:Optional[threading.Thread] = None
        self._started = threading.Event()
        self._stopped = False
        self._running: bool = False

    def start(self) -> None:
        """启动执行器（标记为运行中；重复调用幂等）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stopped =False
        self._started.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="AgentExecutorThread")
        self._thread.start()
        self._started.wait(timeout=5.0)
        if self._loop is None:
            raise RuntimeError("2222Agent executor is not running")
        logger.info(f"Agent started successfully ...")


    def _run_loop(self)->None:
        try:
            _inject_proxy_env_from_runtime_config()

            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            def exception_handler(loop, context):
                exception = context.get("exception")
                message = context.get("message","")
                error_text=""
                if exception:
                    error_text=str(exception)
                if message:
                    error_text=""+str(message)

                suppressed_patterns=[
                    "cancel scope",
                    "defferent task",
                    "nont-thread-safe operation",
                    "other than the current one",
                    "async_generator_athrow",
                    "task was destroyed",
                ]

                if any(pattern in error_text.lower() for pattern in suppressed_patterns):
                    return
                loop.default_exception_handler(context=context)
            self._loop.set_exception_handler(exception_handler)

            self._started.set()

            self._loop.run_forever()
        except Exception as e:
            logger.error(e)
        finally:
            if self._loop is not None:
                try:
                    pending=asyncio.all_tasks(self._loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    self._loop.close()
                except Exception as e:
                    logger.error(e)
                finally:
                    self._loop = None


    def stop(self) -> None:
        """停止执行器（标记为已停止；重复调用幂等）。"""
        if self._stopped:
            return
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.info(f"Agent executor thread stop ...")
        self._thread = None

    async def submit_async(self, coro:Awaitable[T]) -> T:
        """
        提交一个 coroutine 并返回其结果。

        简化实现：直接在调用方事件循环里 ``await coro``。
        若 coro 是普通值（非 awaitable），原样返回。
        """
        if self._loop is None or self._stopped:
            raise RuntimeError("11111Agent executor is not running")
        future=concurrent.futures.Future()
        actual_result ={"value":None,"has_result":False}
        ctx=copy_context()

        async def wrapper():

            _ctx_ref=ctx
            try:
                result=await coro
                actual_result["value"]=result
                actual_result["has_result"]=True
                future.set_result(actual_result)
            except RuntimeError as e:
                error_msg=str(e)
                if "cancel scope" in error_msg.lower() or "defferent task" in error_msg.lower():
                    logger.warning(error_msg)
                    if actual_result["has_result"]:
                        future.set_result(actual_result["value"])
                    else:
                        future.set_result(None)
                else:
                    future.set_exception(e)
            except Exception as e:
                future.set_exception(e)
        def schedule_wrapper():
            asyncio.ensure_future(wrapper())
        self._loop.call_soon_threadsafe(ctx.run, schedule_wrapper)

        loop = asyncio.get_running_loop()
        try:
            # ``future`` 里放的是 ``actual_result``（带 value/has_result 的包装），
            # 但本方法的契约是「返回被提交协程的结果本身」。
            # 正常路径必须像异常路径那样取出 ``value`` ——
            # 否则调用方拿到的会是 ``{'value': ..., 'has_result': True}`` 这种
            # 内部包装字典，而不是真正的返回值。
            outcome = await loop.run_in_executor(None, future.result)
            if isinstance(outcome, dict) and "value" in outcome and "has_result" in outcome:
                return cast(T, outcome["value"])
            return cast(T, outcome)
        except RuntimeError as e:
            error_msg=str(e).lower()
            if "cancel scope" in error_msg or "defferent task" in error_msg:
                if actual_result["has_result"]:
                    return actual_result["value"]
                return cast(T,None)
            raise


    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        return self._loop

    @property
    def is_running(self) -> bool:
        return self._loop is not None and not self._stopped and self._thread is not None and self._thread.is_alive()
