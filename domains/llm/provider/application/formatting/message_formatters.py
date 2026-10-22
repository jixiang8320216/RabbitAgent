"""OpenAI 系消息体里的**媒体内容（图片 / 音频）整形**工具。

这一层只做一件事：把各种来源的媒体（runtime 的 ``ImageBlock`` / ``AudioBlock``、
OpenAI 原生块、``data:`` URL、裸 base64、远程 URL、已经解码的 ``bytes``）
统一成 OpenAI chat-completions 认的形状::

    图片  {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
    音频  {"type": "input_audio", "input_audio": {"data": "<base64>", "format": "wav"}}

三个贯穿全模块的设计口径：

1. **媒体一律带上界**。base64 和远程读取都有 ``max_bytes``：一张几百 MB 的图
   直接塞进请求体，换来的不是更好的回答，而是超时 + 整轮重试。解码前先按
   ``len * 3 / 4`` 估算，超限就拒——别为了确认「它太大」先把它解出来。
2. **拿不到就返回 ``None``，不抛**。格式化是旁路：一张图坏了不应该让整轮推理
   起不来，宁可少一个内容块。
3. **入参一律鸭子探测**。这组函数现在还没有固定调用方，块可能有
   ``{"type":"image","source":{...}}`` / ``{"image_url":{"url":...}}`` /
   ``{"type":"input_audio","input_audio":{...}}`` 等多种写法，逐形状试拿比
   认死一种更能扛住后续改动。
"""

from __future__ import annotations
import requests
import abc
import base64
import copy
import json
import pathlib
import re
import tempfile
import typing
from urllib import parse

import pydantic
import logging as _logging
import typing as _typing

from transformers.processing_utils import MultiModalData

import domains.agent.domain.value_objects.message as message

JsonObject = dict[str, _typing.Any]

_LOGGER = _logging.getLogger(__name__)
_JSON_OBJECT_ADAPTER=pydantic.TypeAdapter(message.JsonObject)
# 单份媒体的默认上限（20MiB）：OpenAI 的图片上限就在这个量级，超了必然被拒。
_SUPPORTED_IMAGE_EXTENSIONS=frozenset({".png",",jpg",".jpeg",".gif",".webp"})

# ``_save_base64_data`` 的默认落盘目录（系统临时目录下的子目录）。
_MEDIA_DIR_NAME = "rabbitagent_media"
_REMOTE_MEDIA_TIMEOUT_SECONDS=30
_REMOTE_MEDIA_CHUNK_BYTES=64*1024
_DEFAULT_IMAGE_MEDIA_TYPE = "image/png"
_DEFAULT_AUDIO_FORMAT = "wav"
_MAX_REMOTE_MEDIA_BYTES=20*1024*1024
_MDDIA_TYPE_PATTERN=re.compile(r"[a-z0-9.+-]+/([a-z0-9.+-]{1,32])]",re.IGNORECASE)
# OpenAI 的 ``input_audio`` 只认 ``wav`` / ``mp3`` 两种，其余一律落到 wav。
_AUDIO_FORMAT_BY_MEDIA_TYPE = {
    "audio/wav": "wav",
    "audio/wave": "wav",
    "audio/x-wav": "wav",
    "audio/vnd.wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mpeg3": "mp3",
}

def _json_object(value: object) -> JsonObject | None:
    return _JSON_OBJECT_ADAPTER.validate_python(value)

def _save_base64_data(
    media_type: str ,
    base64_data: str,
) -> str :
    match= _MDDIA_TYPE_PATTERN.fullmatch(media_type)
    if match is None:
        raise ValueError("unsupported media type: {}".format(media_type))
    extension = f".{match.group(1).lower()}"
    decoded_data = _decode_bounded_base64(base64_data)
    with tempfile.NamedTemporaryFile(suffix=extension,delete=False) as tmp:
        tmp.write(decoded_data)
        return tmp.name

# 图片 / 音频块整形
# ----------------------------------------------------------------------


def _to_openapi_image_url(
    url: str,
) -> str :
    parsed_url = parse.urlparse(url)
    local_path = pathlib.Path(url).expanduser()
    if local_path.is_file() and local_path.suffix.lower() in _SUPPORTED_IMAGE_EXTENSIONS:
        base64_image = base64.b64encode(local_path.read_bytes()).decode("utf-8")
        extension = local_path.suffix.lower().removeprefix(".")
        mime_subtype = "jpeg" if extension in {"jpg","jpeg"} else extension
        return f"data:image/{mime_subtype};base64,{base64_image}"
    remote_suffix = pathlib.PurePosixPath(parsed_url.path).suffix.lower()
    if parsed_url.scheme in {"http", "https"}  and remote_suffix in _SUPPORTED_IMAGE_EXTENSIONS:
        return url
    supported =",".join(sorted(_SUPPORTED_IMAGE_EXTENSIONS))
    raise TypeError(f'"{url}" must be a local image or HTTPS URL ending with ont of :{supported}')

def _formate_openai_image_block(
    image_block: message.ImageBlock,
) ->message.JsonObject:
    source = image_block.source
    if isinstance(source, message.URLSource):
        url=_to_openapi_image_url(source.url)
    elif isinstance(source, message.Base64Source):
        data = source.data
        media_type = source.media_type
        url=f"data:{media_type};base64,{data}"
    else:
        typing.assert_never(source)
    return _json_object({"type": "image_url", "image_url": {"url":url}})

def _to_openai_audio_data(
    source: message.MediaSource,
) -> message.JsonObject:
    if isinstance(source, message.URLSource):
        parsed_url = parse.urlparse(source.url)
        local_path = pathlib.Path(parsed_url.path).expanduser()
        path= local_path if local_path.is_file() else pathlib.PurePosixPath(parsed_url.path)
        extension = path.suffix.lower()
        if extension not in _SUPPORTED_IMAGE_EXTENSIONS:
            raise TypeError("unsupported media type: {}".format(extension))
        if local_path.is_file():
            if local_path.stat().st_size > _MAX_REMOTE_MEDIA_BYTES:
                raise ValueError(f"Media source exceeds decoded bytes")
            raw_data = local_path.read_bytes()
        elif parsed_url.scheme in {"http", "https"} :
            raw_data = _read_bounded_remote_media(source.url)
        else:
            raise TypeError("unsupported media type: {}".format(source.media_type))
        if len(raw_data) > _MAX_REMOTE_MEDIA_BYTES:
            raise ValueError("aDUIO SOURCE EXCENDS")
        data = base64.b64encode(raw_data).decode("utf-8")
        return {"data":data,"format": extension.removeprefix(".")}
    if isinstance(source, message.Base64Source):
        if source.media_type not in {"audio/wav","audio/mp3"}:
            media_type = source.media_type
            raise TypeError("unsupported media type: {}".format(media_type))
        _decode_bounded_base64(source.data)
        return {"data":source.data,"format": source.media_type.removeprefix("audio/")}

def _decode_bounded_base64(data:str)->str:
    maximum_encoded_bytes= ((_MAX_REMOTE_MEDIA_BYTES+2)//3)*4
    padding_bytes=len(data)-len(data.rstrip("="))
    estimated_decoded_bytes=(len(data)//4)*3- padding_bytes
    if len(data) > maximum_encoded_bytes or estimated_decoded_bytes > _MAX_REMOTE_MEDIA_BYTES:
        raise ValueError(f"Media source exceeds decoded bytes")
    decoded = base64.b64decode(data,validate=True)
    if len(decoded) > _MAX_REMOTE_MEDIA_BYTES:
        raise ValueError(f"Media source exceeds decoded bytes")
    return decoded

def _read_bounded_remote_media(url:str)->bytes:
    payload = bytearray()
    with requests.get(url, timeout=_REMOTE_MEDIA_TIMEOUT_SECONDS,stream=True) as response:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=_REMOTE_MEDIA_CHUNK_BYTES):
            if not chunk:
                continue
                if len(payload) +len(chunk)> _MAX_REMOTE_MEDIA_BYTES:
                    raise ValueError(f"Media source exceeds decoded bytes")
                payload.extend(chunk)
    return bytes(payload)

class ProviderMessageFormatter(abc.ABC):

    async def format(
        self,
        msgs: list[message.Msg],
        **kwargs:object) -> list[message.Msg]:
        self.assert_list_of_msgs(msgs)
        return await self._format(copy.deepcopy(msgs))

    @abc.abstractmethod
    async def _format(self, msgs: list[message.Msg]) -> list[message.Msg]:...


    @staticmethod
    def assert_list_of_msgs(msgs: list[message.Msg]):
        if not isinstance(msgs, list):
            raise TypeError("msgs must be a list")
        for msg in msgs:
            if not isinstance(msg, message.Msg):
                raise TypeError("msg must be a Msg object")

    @staticmethod
    def convert_tool_result_to_string(
        output: message.ToolResultBlock
    )->tuple[str, list[MultiModalData]]:
        if isinstance(output, str):
            return output, []
        textual_output = []
        multimodal_data:list[MultiModalData] = []
        for block in output:
            if isinstance(block, message.TextBlock):
                textual_output.append(block.text)
                continue
            if isinstance(block,(message.ImageBlock,message.AudioBlock,message.VideoBlock)):
                source= block.source
                if isinstance(source, message.URLSource):
                    path_multimodal_file = source.url
                else:
                    path_multimodal_file = _save_base64_data(source.media_type, source.data)
                textual_output.append(f"The returned {block.type} can be found at: {path_multimodal_file}")
                multimodal_data.append((path_multimodal_file, block))
                continue
            if isinstance(block, (message.ToolUseBlock, message.ToolResultBlock)):
                raise ValueError(f"Unsupported tool block: {block}")
            raise ValueError(f"Unsupported tool block: {block}")

        if len(textual_output) == 1:
            return textual_output[0], multimodal_data
        return "\n".join("-"+item for item in textual_output), multimodal_data

class OpenAIMessageFormatter(ProviderMessageFormatter):
    def __init__(self, promote_tool_result_images:bool=False) -> None:
        self.promote_tool_result_images = promote_tool_result_images

    async def _format(self, msgs: list[message.Msg]) -> list[message.JsonObject]:
        self.assert_list_of_msgs(msgs)
        messages:list[message.JsonObject] = []
        i=0
        while i < len(msgs):
            msg = msgs[i]
            content_blocks: list[message.JsonObject] = []
            tool_calls :list[message.JsonObject]=[]

            for block in msg.get_content_blocks():
                if isinstance(block, message.TextBlock):
                    content_blocks.append(dict(block))
                elif isinstance(block,message.ToolUseBlock):
                    tool_calls.append(
                        {
                            "id":block.id,
                            "type":"function",
                            "function":{
                                "name":block.name,
                                "arguments":json.dumps(block.input,ensure_ascii=False)
                            },
                        }
                    )
                elif isinstance(block, message.ToolResultBlock):
                    textual_output, multimodal_data = self.convert_tool_result_to_string(block.output)
                    messages.append(
                        {
                            "role":"tool",
                            "tool_call_id": block.id,
                            "content":textual_output,
                            "name":block.name,
                        }
                    )
                    prompt_blocks :list[message.ContentBlock]=[]
                    for url, multimodal_block in multimodal_data:
                        if isinstance(multimodal_block, message.ImageBlock) and self.promote_tool_result_images:
                            prompt_blocks.append(
                                [
                                    message.TextBlock(type="text", text=f"\n- The image from '{url}':"),
                                    message.ImageBlock(type="image", source=message.URLSource(type="url", url=url)),
                                ]
                            )
                    if prompt_blocks:
                        prompt_blocks=[
                            message.TextBlock(type="text", text=("<system-info>The followint are the image contents from the tool"
                                                                 f"result of '{block.name}':")),
                            *prompt_blocks,
                            message.TextBlock(type="text", text="</system-info>"),
                        ]
                        msgs.insert(i+1,message.Msg(name="user", content=prompt_blocks, role="user"))
                elif isinstance(block, message.ImageBlock):
                    content_blocks.append(_formate_openai_image_block(block))

                elif isinstance(block, message.AudioBlock):
                    input_audio = _to_openai_audio_data(block.source)
                    content_blocks.append(
                        {
                            "type":"input_audio",
                            "input_audio":input_audio,
                        }
                    )
                # 缩进修复：原先以下 8 行被错放在 `for block in msg.get_content_blocks()`
            # 循环体内，导致每条消息只处理「第一个 block」就append 并 return。
            # 后果：发给 provider 的 messages 恒只剩 1 条 system 消息，
            # 后续 user / assistant / tool 消息全部丢失，模型看不到用户提问。
            # （探针 [CTX] n=3 但 [REQ] n_messages=1 即由此而来）
            msg_openai_data:dict[str,object]={
                "role":msg.role,
                "name":msg.name,
                "content":content_blocks or None,
            }

            if tool_calls:
                msg_openai_data["tool_calls"]=tool_calls

            msg_openai= _json_object(msg_openai_data)

            if msg_openai["content"] or msg_openai.get("tool_calls"):
                messages.append(msg_openai)
            i+=1
        return  messages





















#: 兼容别名：消息格式化器的抽象基类原名 ``MessageFormatter``，
#: 现正式名为 ``ProviderMessageFormatter``（与 provider 家族的其他
#: ``Provider*`` 契约命名对齐）。旧引用点继续可用。
MessageFormatter = ProviderMessageFormatter
