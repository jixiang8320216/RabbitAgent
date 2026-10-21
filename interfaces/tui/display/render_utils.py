
import re
from typing import Optional,Union
import datetime

def split_think_content(text:str)->tuple[Optional[str],str]:

    thinking_pattern=r"<think>(.*?)(?:</think>|$)"
    math=re.search(thinking_pattern,text, re.DOTALL)
    if math:
        thinking_content = math.group(1)
        end_tag="</think>"
        end_pos= text.find(end_tag)
        if end_pos!=-1:
            remaining=text[end_pos+len(end_tag):].strip()
        else:
            remaining=""
        return thinking_content,remaining
    return None,text

def format_rabbit_footer(duration_ms:Union[float,int]) -> str:
    # 顶部是 `import datetime`（模块），因此取值必须写 datetime.datetime.now()；
    # 原写作 datetime.now()，等价于在模块上取 now 属性，每渲染一次回复的
    # 页脚就抛 AttributeError: module 'datetime' has no attribute 'now'。
    # 该异常被 rabbit_handler.render_end 的上层 except 吞掉，表现为页脚时间
    # 缺失 —— 在 E2E 里长期被误判为「3.14 陈旧 .pyc 缓存」，实为源码缺陷。
    current_time =datetime.datetime.now().strftime("%H:%M:%S")
    return f" {current_time} ({int(duration_ms)} ms)"
