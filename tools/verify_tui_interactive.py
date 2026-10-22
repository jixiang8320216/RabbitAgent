"""TUI 交互式问答端到端验证（ConPTY 真实终端驱动）。

背景
----
``main.py`` 顶层 ``except`` 只 ``print(f"\\n Fatal error: {e}")``，吞掉堆栈；
而 Textual 在 stdout 不是终端时会退化，键盘输入无法注入。因此这里用
**Windows ConPTY** 给 ``main.py`` 分配一个真实终端，再真发按键，
让「跑不起来」的缺陷无处可藏。

驱动脚本本身跑在装有 ``pywinpty`` 的 Python；被驱动的目标进程用依赖完整的
venv（textual 8.x）。``cwd`` 与 ``PYTHONPATH`` 都指向项目根。

用法::

    python tools/verify_tui_interactive.py

产物（写到项目根）：
    - ``_tui_e2e_clean.log``  去 ANSI 后的干净屏幕流（按 STEP 分段）
    - ``_tui_e2e_report.txt`` 机器判定

踩过的坑（务必保留这些写法）
----------------------------
1. **必须逐字符发送**：ConPTY 一次性写入整串会丢字符（实测 ``1+1 等于几？``
   被截成 ``1+``）。
2. **不要用 ``/clear`` 清屏**：它自身会渲染一个带时间戳的 ``Command Result``
   块，会被误判成 agent 答复锚点。用 ``Esc`` 替代。
3. **输入阶段与提交阶段分段收集**：逐字符回显的中间态会污染答复判定
   （输入含 ``开门见山`` 时，回显中间态就让「答复已渲染」误报为 True）。
4. **判定要交叉验证**：TUI 全屏重绘 + 去重会让屏幕流漏判，需同时读应用日志
   与会话历史。
5. **日志基线用字符数而非 ``st_size``**：日志含中文，字节与字符索引不一致。
6. **问答文本要高辨识度**：用「请只回复四个字：开门见山」而不是 ``1+1``，
   避免屏幕噪声（命令列表、欢迎语）造成误判。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
import threading
import time

from winpty import PtyProcess  # type: ignore

PROJECT = pathlib.Path(__file__).resolve().parent.parent
ROWS, COLS = 44, 120

CLEAN_LOG = PROJECT / "_tui_e2e_clean.log"
REPORT = PROJECT / "_tui_e2e_report.txt"
APP_LOG = pathlib.Path.home() / ".rabbit" / "logs" / "rabbit.log"

QUESTION = "请只回复四个字：开门见山。不要任何解释。"
EXPECT = "开门见山"

ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>()][0-9A-Za-z]?|\x1b[78]"
)

buf: list[str] = []
lock = threading.Lock()
closed = threading.Event()
LOG_BASELINE = 0


def drain() -> str:
    with lock:
        text = "".join(buf)
        buf.clear()
    return text


def clean(text: str) -> str:
    return ANSI_RE.sub("", text).replace("\r", "")


def visible(text: str, keep: int = 80) -> str:
    """折叠整屏重绘：去重后只保留最后若干行。"""
    out, seen = [], set()
    for ln in text.split("\n"):
        s = ln.strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return "\n".join(out[-keep:])


def norm(s: str) -> str:
    return re.sub(r"[\s，。：？！、,.:?!]", "", s)


def _alive(proc: PtyProcess) -> bool:
    """``PtyProcess.read()`` 在「暂时没有数据」时也抛 EOFError，必须用 isalive 区分。"""
    try:
        return bool(proc.isalive())
    except Exception:
        return False


def reader(proc: PtyProcess) -> None:
    while not closed.is_set():
        try:
            chunk = proc.read()
        except Exception:
            if _alive(proc):
                time.sleep(0.1)
                continue
            closed.set()
            break
        if chunk:
            with lock:
                buf.append(chunk)
        else:
            time.sleep(0.05)


def wait_for_pattern(pattern: str, timeout: float) -> tuple[str, bool]:
    deadline = time.time() + timeout
    acc: list[str] = []
    while time.time() < deadline:
        piece = drain()
        if piece:
            acc.append(clean(piece))
            if re.search(pattern, "".join(acc)):
                return "".join(acc), True
        time.sleep(0.3)
    return "".join(acc), False


def settle(quiet: float, timeout: float) -> str:
    deadline = time.time() + timeout
    last_change = time.time()
    acc: list[str] = []
    while time.time() < deadline:
        piece = drain()
        if piece:
            acc.append(clean(piece))
            last_change = time.time()
        elif time.time() - last_change >= quiet:
            break
        time.sleep(0.3)
    return "".join(acc)


def send(proc: PtyProcess, text: str) -> bool:
    try:
        proc.write(text)
        return True
    except Exception as error:
        print(f"[driver] send failed: {error!r}")
        return False


def type_text(proc: PtyProcess, text: str, delay: float = 0.06) -> bool:
    """逐字符写入，模拟真实打字（整串写入会丢字符）。"""
    for ch in text:
        if not send(proc, ch):
            return False
        time.sleep(delay)
    return True


def check_app_log() -> tuple[bool, bool]:
    """交叉验证：从应用日志判定真实 LLM 调用与 agent 回合是否成功。"""
    try:
        text = APP_LOG.read_text(encoding="utf-8", errors="replace")[LOG_BASELINE:]
    except Exception:
        return False, False
    llm_ok = bool(re.search(r'chat/completions\s+"HTTP/1\.1 200 OK"', text))
    turn_ok = bool(re.search(r"Agg?ent end successfully", text))
    return llm_ok, turn_ok


def main() -> int:
    global LOG_BASELINE
    parser = argparse.ArgumentParser(description="TUI 交互式问答端到端验证")
    parser.add_argument("--target-python", default=None, help="被驱动进程的 python 解释器")
    args = parser.parse_args()

    target_py = pathlib.Path(args.target_python) if args.target_python else None
    if target_py is None:
        candidate = PROJECT.parent / "RabbitAgent - 1018" / ".venv" / "Scripts" / "python.exe"
        target_py = candidate if candidate.exists() else pathlib.Path(sys.executable)

    try:
        LOG_BASELINE = len(APP_LOG.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        LOG_BASELINE = 0

    env = dict(os.environ)
    env.update(
        TERM="xterm-256color",
        PYTHONIOENCODING="utf-8",
        PYTHONUTF8="1",
        PYTHONPATH=str(PROJECT),
    )

    proc = PtyProcess.spawn(
        [str(target_py), "main.py"],
        cwd=str(PROJECT),
        env=env,
        dimensions=(ROWS, COLS),
    )
    threading.Thread(target=reader, args=(proc,), daemon=True).start()

    sections: list[str] = []
    results: list[str] = []

    def step(title: str) -> None:
        sections.append(f"\n{'=' * 72}\n### {title}\n{'=' * 72}\n")

    try:
        # 1) 启动 + TUI 首屏
        out, banner_ok = wait_for_pattern(r"Rabbit Agent|输入消息开始对话", 240.0)
        if banner_ok:
            out += settle(quiet=4.0, timeout=20.0)
        step("STEP 1 - 启动与 TUI 首屏")
        sections.append(visible(out))
        results.append(f"STEP1 banner_rendered={banner_ok}")
        print(f"[driver] STEP1 banner_rendered={banner_ok}")

        # 2) 本地命令 /help
        if send(proc, "/help\r"):
            out = settle(quiet=5.0, timeout=60.0)
            help_ok = bool(re.search(r"Command Result|help|命令|exit", out, re.I))
            step("STEP 2 - 发送 /help（本地命令路径）")
            sections.append(visible(out))
            results.append(f"STEP2 help_dispatched={help_ok}")
            print(f"[driver] STEP2 help_dispatched={help_ok}")

        # 3) 自然语言问答（真实 LLM 调用）
        #    Esc 收起 /help 面板与补全列表（不用 /clear，它会渲染带时间戳的
        #    Command Result 块，被误判成答复锚点）
        send(proc, "\x1b")
        time.sleep(1.5)
        drain()

        echoed = answered = False
        echo_out = out = ""
        if type_text(proc, QUESTION):
            # 输入阶段只用于判定回显，不能用于答复判定
            echo_out = settle(quiet=2.0, timeout=20.0)
            echoed = norm(QUESTION) in norm(echo_out)
            # 提交前再 Esc 一次：逐字符输入会让 Textual 实时弹出补全列表
            send(proc, "\x1b")
            time.sleep(1.5)
            drain()
            if send(proc, "\r"):
                out = settle(quiet=25.0, timeout=360.0)
                answered = norm(EXPECT) in norm(out)

        llm_ok, turn_ok = check_app_log()
        step(f"STEP 3 - 发送自然语言提问：{QUESTION}（期望含「{EXPECT}」）")
        sections.append(
            visible(echo_out, keep=60) + "\n---- 提交后输出 ----\n" + visible(out, keep=200)
        )
        results.append(
            f"STEP3 question_echoed={echoed} answer_rendered={answered} "
            f"llm_http_200={llm_ok} agent_turn_end={turn_ok} chars={len(out)}"
        )
        print(
            f"[driver] STEP3 question_echoed={echoed} answer_rendered={answered} "
            f"llm_http_200={llm_ok} agent_turn_end={turn_ok} chars={len(out)}"
        )

        # 4) 退出
        if send(proc, "/exit\r"):
            out = settle(quiet=4.0, timeout=40.0)
            step("STEP 4 - 发送 /exit")
            sections.append(visible(out))
            # 以应用日志为准：settle 可能在进程完全退出前就结束
            exited = "TUIApplication shutdown complete" in (
                APP_LOG.read_text(encoding="utf-8", errors="replace")[LOG_BASELINE:]
            )
            results.append(f"STEP4 exit_sent=True exited_cleanly={exited}")
            print(f"[driver] STEP4 exited_cleanly={exited}")

    finally:
        tail = drain()
        if tail.strip():
            sections.append(
                f"\n{'=' * 72}\n### 退出后残留输出\n{'=' * 72}\n{visible(clean(tail))}"
            )
        sections.append(
            "\n" + "=" * 72 + "\n### 机器判定\n" + "=" * 72 + "\n" + "\n".join(results)
        )
        CLEAN_LOG.write_text("\n".join(sections), encoding="utf-8-sig", errors="replace")
        REPORT.write_text("\n".join(results), encoding="utf-8-sig", errors="replace")
        try:
            proc.terminate()
        except Exception:
            pass

    print(f"[driver] clean log -> {CLEAN_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
