
import pathlib

import pydantic
import json
import logging
import tempfile
import os
import time

logger = logging.getLogger(__name__)
_REPLACE_RETRY_DELAYS=(0.01,0.02,0.05,0.1,0.2)

def _should_fsync()->bool:
    return os.getenv("RABBIT_TEST_MODE")!="1"

def _replace_with_retry(source:pathlib.Path,target :pathlib.Path)->None:
    for delay in (*_REPLACE_RETRY_DELAYS,None):
        try:
            # ``Path.rename`` 在 POSIX 上会静默覆盖目标，但 Windows 上目标已存在
            # 时抛 ``FileExistsError``（WinError 183），**不是** ``PermissionError``。
            # 原实现只捕获 PermissionError，所以并发写同一个 state.json 时重试
            # 逻辑完全不生效，直接把 WinError 183 抛给上层
            # （表现为 "persist_state failed: [WinError 183] 当文件已存在时..."）。
            # 改用 ``os.replace``：它跨平台都是原子覆盖，语义正是这里需要的。
            os.replace(source, target)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)

def atomic_write_text(path:pathlib.Path,text:str,*,encoding:str='utf-8')->None:
    target =pathlib.Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    should_fsync = _should_fsync()
    temporary_path : pathlib.Path |None= None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+",
            encoding="utf-8",
            newline="",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as fh:
            temporary_path =pathlib.Path(fh.name)
            fh.write(text)
            fh.flush()
            if should_fsync:
                os.fsync(fh.fileno())
        _replace_with_retry(temporary_path,target)
        temporary_path =None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError as exec:
                logger.warning(exc)


def atomic_write_json(path:pathlib.Path, data: pydantic.JsonValue,*, indent: int=2)->None:
    atomic_write_text(
        path,
        json.dumps(data,indent=indent,ensure_ascii=False),
        encoding="utf-8"
    )