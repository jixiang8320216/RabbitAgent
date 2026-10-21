"""TUI 交互式问答端到端验证。

驱动方式
--------
Textual 官方 ``App.run_test(headless=True)`` —— 走的是同一套 widget / 事件 /
命令派发链路，不是 mock：

- 真实的 ``UserInputArea`` 组件
- 真实的 ``CommandRegistry`` 派发
- 真实的 provider 调用链

**关键**：``TUIObserver.on_application_start`` 里既启动 Textual 又建主循环，
但 ``run_test`` 已经接管了 ``App.run()``，再调 ``on_application_start`` 会二次
``run_async`` 冲突。因此这里**只补主循环**，不调 ``on_application_start``。

没有主循环，提交的输入没人消费 —— 现象就是「消息发出去了但永远没回复」。

用法::

    python e2e_tui_test.py                # 全量（含真实模型调用）
    python e2e_tui_test.py --no-llm       # 只验证 UI 与命令层
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger("e2e")


# ---------------------------------------------------------------------------
# 屏上文本提取
# ---------------------------------------------------------------------------

def widget_dump(app) -> str:
    """提取屏上可见文本。

    用渲染条带（``render_strips``）而不是遍历 widget —— 前者拿到的
    就是真正显示在屏幕上的内容（含布局结果），后者会拿到被遮挡、
    被滚动出视口的节点。
    """
    lines: list[str] = []
    try:
        for strip in app.screen._compositor.render_strips():
            lines.append("".join(seg.text for seg in strip))
    except Exception:  # noqa: BLE001
        try:
            for line in app.screen.lines:
                lines.append("".join(seg.text for seg in line))
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(lines)


def uniq(text: str) -> str:
    """去重相邻重复行。"""
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if out and out[-1] == line:
            continue
        out.append(line)
    return "\n".join(out)


def _session_params(tui) -> tuple[str | None, bool]:
    """从 app_config 里取 session_id 与 is_resume。"""
    try:
        from interfaces.cli.tui_application import _session_paramenters_from_overrides

        return _session_paramenters_from_overrides(tui.app_config.config_overrides)
    except Exception:  # noqa: BLE001
        return None, False


_USER_ECHO_MARK = "│ "   # 用户消息回显的行首标记


def assistant_reply(app) -> str:
    """取 assistant 响应（排除用户回显）。

    判定依据是**屏上可见文本**而不是 widget 的 class：
    响应的渲染方式（挂 ``Static`` / 追加到已有节点）会变，
    而「用户消息以 ``│ `` 开头、响应不带」这个形态是稳定的。
    """
    # 渲染条带 = 屏幕上真正显示的内容（含布局后的换行与缩进）。
    # 比遍历 widget 可靠：响应可能挂成 Static，也可能追加进已有节点。
    screen = widget_dump(app)
    if not screen.strip():
        return ""

    # 响应出现在「用户回显」之后；取最后一段非用户回显的文本
    lines = screen.splitlines()
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if "│" in stripped and stripped.startswith("│"):
            continue          # 用户消息回显
        if stripped.startswith(("Model:", "Context Window:", "输入消息开始对话")):
            continue          # 状态栏 / 提示行
        if stripped.startswith(("agents", "clear", "config", "config-", "configd",
                               "confige", "exit", "help", "memory", "mcp",
                               "session", "status", "theme", "tokens", "web",
                               "skills", "mode", "plan", "reset", "shell",
                               "btw", "hook", "lsp", "info", "reload")):
            continue          # 命令补全列表
        collected.append(stripped)

    if not collected:
        return ""

    # 最终响应后面一定跟着耗时时间戳（形如 ``17:43:19 (1.2 s)``）——
    # 这是「模型回答已渲染完成」的权威标志。
    # 以它为锚点往前收集，既能拿到响应正文，又能排除 banner / 任务提交行 /
    # loading 动画等中间态。
    lines = widget_dump(app).splitlines()

    anchor = -1
    for index, line in enumerate(lines):
        if re.search(r"\d{1,2}:\d{2}:\d{2}\s*\([^)]*\)", line):
            anchor = index
    if anchor < 0:
        return ""      # 还没有完成渲染 —— 不算收到响应

    # 从锚点往上收集，跳过用户回显与 loading 行
    body: list[str] = []
    for line in lines[:anchor]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("│") or "│" in stripped:
            continue                      # 用户消息回显
        if stripped.startswith(("Model:", "Context Window:", "输入消息开始对话",
                                "Rabbit Agent v")):
            continue                      # 状态栏 / banner
        if "is thinking" in stripped:
            continue                      # loading 动画
        if stripped.startswith((">通用模式Task:", "Rabbit >")):
            continue                      # 任务提交行 / 输入回显
        if any(ch in stripped for ch in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏▁▂▃▄▅▆▇"):
            continue
        if stripped.startswith(("agents", "clear", "config", "config-", "configd",
                               "confige", "exit", "help", "memory", "mcp",
                               "session", "status", "theme", "tokens", "web",
                               "skills", "mode", "plan", "reset", "shell",
                               "btw", "hook", "lsp", "info", "reload")):
            continue                      # 命令补全列表
        body.append(stripped)

    return "\n".join(body)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def run(include_llm: bool, question: str, timeout: float) -> int:
    results: list[tuple[str, bool, str]] = []

    def mark(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""))

    print("=" * 72)
    print("TUI 交互式问答端到端验证")
    print("=" * 72)
    print()

    # ---- 阶段 1：与 main.py 相同的运行时装配 ----
    print("[阶段 1] 装配运行时（main.py 同一路径）")
    from domains.context import build_runtime, build_runtime_context
    from domains.configuration.infrastructure.adapter import app_mode_resolver
    from domains.configuration.infrastructure.store.user_config_store import (
        load_or_create_user_config,
    )

    user_config = load_or_create_user_config()
    app_mode = app_mode_resolver.load_app_mode(user_config)
    build_runtime_context()
    mark("app_mode 解析", bool(app_mode), str(app_mode))

    runtime = await build_runtime(
        project_home=os.getcwd(),
        session_id=None,
        is_resume=False,
        current_mode_id=app_mode or "delegate",
    )
    app_config = runtime.app_config
    mark("build_runtime", True, type(app_config).__name__)

    # ---- 阶段 2：定位 Textual App ----
    print()
    print("[阶段 2] 启动 TUI（headless）")
    from interfaces.cli.tui_application import TUIApplication

    tui = TUIApplication(app_config, project_home=os.getcwd())
    observer = tui.observer
    app = observer.app
    mark("TUIApplication 装配", True, type(app).__name__)

    async with app.run_test(headless=True, size=(120, 42)) as pilot:

        # ---- 关键：手动拉起真实主循环 ----
        async def main_loop() -> None:
            try:
                while not tui.interrupted:
                    try:
                        user_input = await observer.get_user_input()
                    except asyncio.CancelledError:
                        continue
                    if user_input is None:
                        break
                    try:
                        if tui._is_command_input(user_input):
                            expansion = await tui._handle_command(user_input)
                            if expansion is not None:
                                await tui._enqueue_user_message(
                                    expansion.expanded_prompt,
                                    prompt_expansion=expansion,
                                )
                        else:
                            await tui._enqueue_user_message(user_input)
                    except Exception:  # noqa: BLE001
                        logger.exception("main loop handling failed")
            except asyncio.CancelledError:
                pass

        # ---- 关键：补齐 TUIApplication.run() 里的初始化序列 -------------
        #
        # on_application_start 之外，run() 还做了三件不可省的事：
        #   1. 绑定请求上下文
        #   2. 加载并创建 agent（Plan Agent + 子 Agent）
        #   3. 初始化会话
        # 缺任何一步，提交的消息都会以「No active session」告终。
        import uuid

        from domains.runtime.application.request_context import (
            bind_app_request_context,
        )

        tui._setup_signal_handlers()
        tui._start_agent_executor()

        session_id, is_resume = _session_params(tui)
        if not session_id:
            session_id = str(uuid.uuid4())
        bind_app_request_context(
            app_id=session_id,
            project_home=tui.project_home,
            app_instance=tui.rabbit_app,
        )
        mark("绑定请求上下文", True, session_id[:8])

        sub_agent_configs, plan_agent_config = tui.rabbit_app._load_agent_configs()
        mark(
            "加载 agent 配置",
            plan_agent_config is not None,
            f"sub={len(sub_agent_configs)} root={plan_agent_config is not None}",
        )
        if plan_agent_config is None:
            print("  ✗ 缺少 plan agent 配置，无法继续")
            return 2

        await tui._submit_agent_coro(
            tui.rabbit_app._create_agents(sub_agent_configs, plan_agent_config)
        )
        mark("创建 agent", True)

        real_session_id = await tui._submit_agent_coro(
            tui.rabbit_app.init_session(session_id=session_id, is_resume=is_resume)
        )
        if real_session_id:
            bind_app_request_context(
                app_id=real_session_id,
                project_home=tui.project_home,
                app_instance=tui.rabbit_app,
            )
        mark("初始化会话", bool(real_session_id), str(real_session_id)[:36])

        main_task = asyncio.create_task(main_loop())

        # banner（不调 on_application_start，避免二次 run_async）
        try:
            tui._sync_status_bar_context()
            mark("状态栏同步", True, f"model={getattr(app, 'model_name', None)}")
        except Exception:  # noqa: BLE001
            mark("状态栏同步", False, traceback.format_exc(limit=1).strip())

        async def render_banner() -> None:
            try:
                await observer._render_banner_after_ready("1.0.0")
            except Exception:  # noqa: BLE001
                logger.debug("banner render failed", exc_info=True)

        banner_task = asyncio.create_task(render_banner())

        # ---- 首屏 ----
        for _ in range(20):
            await pilot.pause(0.5)
            if re.search(r"Rabbit Agent|输入消息开始对话", widget_dump(app)):
                break
        screen = widget_dump(app)
        banner_ok = bool(re.search(r"Rabbit Agent|输入消息开始对话", screen))
        mark("首屏渲染", banner_ok)
        if not banner_ok:
            print("  屏上文本：", uniq(screen)[:600])

        # ---- 命令层 ----
        print()
        print("[阶段 3] 命令派发")
        try:
            cmds = tui.command_registry.list_commands()
            names = sorted(
                c if isinstance(c, str) else getattr(c, "name", str(c))
                for c in cmds
            )
        except Exception:  # noqa: BLE001
            names = []
        expected = {
            "help", "exit", "clear", "status", "agents", "skills",
            "config", "web", "model", "theme", "memory", "mcp",
            "session", "tokens",
        }
        missing = sorted(expected - {n.lower() for n in names})
        mark(
            "命令注册",
            len(names) > 0,
            f"{len(names)} 条, 缺失={missing or 'NONE'}",
        )

        if include_llm:
            # ---- 真实问答 ----
            print()
            print("[阶段 4] 自然语言问答（真实模型调用）")
            print(f"  输入: {question!r}")
            started = time.monotonic()

            # 走真实提交路径（与用户按 Enter 完全相同）
            app._process_submission(question)

            answered = False
            elapsed = 0.0
            while elapsed < timeout:
                await pilot.pause(1.0)
                elapsed = time.monotonic() - started
                reply = assistant_reply(app)
                if reply and len(reply.strip()) > 2:
                    answered = True
                    break
                if main_task.done():
                    break

            reply = assistant_reply(app)
            if answered:
                mark("收到模型响应", True, f"耗时 {elapsed:.1f}s")
                print(f"  响应: {reply[:400]!r}")
            else:
                mark("收到模型响应", False, f"{timeout}s 内无响应")
                print("  聊天区内容：")
                print(uniq(widget_dump(app))[:1200])
        else:
            print()
            print("[阶段 4] 跳过（--no-llm）")

        # ---- 清理 ----
        for task in (banner_task, main_task):
            task.cancel()
        try:
            await main_task
        except BaseException:  # noqa: BLE001
            pass

    # ---- 汇总 ----
    print()
    print("=" * 72)
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    print(f"  合计 {passed}/{len(results)} 通过")
    print("=" * 72)
    return 0 if passed == len(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true", help="只验证 UI 与命令层")
    ap.add_argument("--timeout", type=float, default=float(os.environ.get("E2E_TIMEOUT", "150")))
    ap.add_argument("question", nargs="?", default="请用一句话回答：1+1 等于几？")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, stream=sys.stdout)
    return asyncio.run(run(not args.no_llm, args.question, args.timeout))


if __name__ == "__main__":
    raise SystemExit(main())
