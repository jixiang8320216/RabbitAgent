"""TUI 交互式问答端到端验证（ConPTY 真实终端驱动）。

背景
----
``main.py`` 顶层 ``except`` 只 ``print(f"\\n Fatal error: {e}")``，吞掉堆栈；
而 Textual 在 stdout 不是终端时会退化，键盘输入无法输入。因此这里用
**Windows ConPTY** 给 ``main.py`` 分配一个真实终端，再真发按键。

驱动脚本本身跑在装有 ``pywinpty`` 的托管环境；被驱动的目标进程用
``RabbitAgent - 1018`` 目录下的完整依赖 venv（textual 8.2.8 / Python 3.14.5），
但 ``cwd`` 与 ``sys.path`` 都指向**当前项目** ``D:/AI/AIProject/RabbitAgent``，
即验证的是当前工作副本的代码。

用法::

    C:/Users/HUAWEI/.workbuddy/binaries/python/envs/default/Scripts/python.exe \
        verify_tui_interactive.py
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import threading
import time

from winpty import PtyProcess  # type: ignore

PROJECT = pathlib.Path(r"D:\AI\AIProject\RabbitAgent")
TARGET_PY = pathlib.Path(
    r"D:\AI\AIProject\RabbitAgent - 1018\.venv\Scripts\python.exe"
)
ROWS, COLS = 44, 120

CLEAN_LOG = PROJECT / "_tui_e2e_clean.log"
REPORT = PROJECT / "_tui_e2e_report.txt"

ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>()][0-9A-Za-z]?|\x1b[78]"
)

APP_LOG = pathlib.Path.home() / ".rabbit" / "logs" / "rabbit.log"
LOG_BASELINE = 0  # 脚本启动时日志文件长度，用于只判定「本轮」新增


def check_app_log() -> tuple[bool, bool]:
    """交叉验证：从应用日志判定真实 LLM 调用与 agent 回合是否成功。

    TUI 屏幕输出受整屏重绘/去重影响，容易漏判；应用日志是权威事实来源。
    """
    try:
        text = APP_LOG.read_text(encoding="utf-8", errors="replace")[LOG_BASELINE:]
    except Exception:
        return False, False
    llm_ok = bool(re.search(r"chat/completions\s+\"HTTP/1\.1 200 OK\"", text))
    # 兼容历史拼写 "Aggent"
    turn_ok = bool(re.search(r"Agg?ent end successfully", text))
    return llm_ok, turn_ok

buf: list[str] = []
lock = threading.Lock()
closed = threading.Event()


def drain() -> str:
    with lock:
        text = "".join(buf)
        buf.clear()
    return text


def clean(text: str) -> str:
    text = ANSI_RE.sub("", text)
    return text.replace("\r", "")


def visible(text: str, keep: int = 80) -> str:
    """折叠整屏重绘：去重后只保留最后一次全屏渲染的非空行。"""
    lines = [ln.rstrip() for ln in text.split("\n")]
    out, seen = [], set()
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return "\n".join(out[-keep:])


def _alive(proc: PtyProcess) -> bool:
    """存活判断。

    ``PtyProcess.read()`` 在「暂时没有数据」时同样抛 ``EOFError``，
    不能据此判定进程已退出 —— 必须用 ``isalive()`` 区分。
    """
    try:
        return bool(proc.isalive())
    except Exception:
        return False


def reader(proc: PtyProcess) -> None:
    while not closed.is_set():
        try:
            chunk = proc.read()
        except EOFError:
            if _alive(proc):
                time.sleep(0.1)
                continue
            closed.set()
            break
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
    """等到出现 pattern 为止（启动阶段本来就是静默的，不能用静默判据）。"""
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
    """等到输出静默 quiet 秒为止，返回本次累计的干净文本。"""
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
    """逐字符写入，模拟真实打字。

    ConPTY 一次性写入整串时 Textual 的 Input 会丢字符（实测 ``1+1 等于几？``
    被截成 ``1+``），逐字符 + 间隔可稳定送达。
    """
    ok = True
    for ch in text:
        if not send(proc, ch):
            ok = False
            break
        time.sleep(delay)
    return ok


def main() -> int:
    global LOG_BASELINE
    try:
        # 用字符数而非字节数作基线：日志含中文，st_size 与字符索引不一致
        LOG_BASELINE = len(APP_LOG.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        LOG_BASELINE = 0

    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONPATH"] = str(PROJECT)

    proc = PtyProcess.spawn(
        [str(TARGET_PY), "main.py"],
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
        # 3-0) Esc 收起 /help 面板与补全列表。
        # 不用 /clear：它自身会渲染一个「Command Result」块（带时间戳），
        # 会被误判成 agent 答复锚点。
        send(proc, "\x1b")
        time.sleep(1.5)
        drain()

        # 用高辨识度的期望答案，避免屏幕噪声造成的误判/漏判
        question = "请只回复四个字：开门见山。不要任何解释。"
        expect = "开门见山"
        def norm(s: str) -> str:
            return re.sub(r"[\s，。：？！、,.:?!]", "", s)

        echoed = False
        answered = False
        out = ""
        # 逐字符输入：ConPTY 整串写入会丢字符
        if type_text(proc, question):
            # 输入阶段：收集回显。逐字符回显的中间态不能用于答复判定。
            echo_out = settle(quiet=2.0, timeout=20.0)
            echoed = norm(question) in norm(echo_out)
            # 提交前先 Esc：逐字符输入会让 Textual 实时弹出命令补全列表，
            # 此时回车会被补全列表消费（选中补全项）而不是提交消息。
            send(proc, "\x1b")
            time.sleep(1.5)
            # 提交后：丢弃输入回显缓冲，只收集答复阶段输出
            drain()
            if send(proc, "\r"):
                out = settle(quiet=25.0, timeout=360.0)
                answered = norm(expect) in norm(out)

        # 交叉验证：应用日志里的真实 LLM 调用结果
        llm_ok, turn_ok = check_app_log()
        step(f"STEP 3 - 发送自然语言提问：{question}（期望含「{expect}」）")
        sections.append(visible(echo_out, keep=60) + "\n---- 提交后输出 ----\n" + visible(out, keep=200))
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
            exited = not _alive(proc)
            results.append(f"STEP4 exit_sent=True process_alive={exited}")
            print(f"[driver] STEP4 process_alive={exited}")

    finally:
        tail = drain()
        if tail.strip():
            sections.append(
                f"\n{'=' * 72}\n### 退出后残留输出\n{'=' * 72}\n{visible(clean(tail))}"
            )
        sections.append(
            "\n" + "=" * 72 + "\n### 机器判定\n" + "=" * 72 + "\n" + "\n".join(results)
        )

        with CLEAN_LOG.open("w", encoding="utf-8-sig", errors="replace") as f:
            f.write("\n".join(sections))
        with REPORT.open("w", encoding="utf-8-sig", errors="replace") as f:
            f.write("\n".join(results))

        try:
            proc.terminate()
        except Exception:
            pass

    print(f"[driver] clean log -> {CLEAN_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
