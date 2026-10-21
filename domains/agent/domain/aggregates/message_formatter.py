"""请求格式化（MessageFormatter）。

``ReActAgent`` 的 ``formatter`` 参数声明为
``runtime.service.message_formatter.MessageFormatter``。主循环在
``_apply_formatter`` 里这样用它：

- 若 formatter **本身可调用**，直接当候选；否则找 ``format`` /
  ``format_messages`` / ``to_provider_payload`` / ``format_request`` 四个方法；
- 按 ``(messages, tools)``、``(messages)``、``messages=`` / ``msgs=`` 探测调用；
- 返回值是 **Mapping** 时取 ``messages`` / ``tools`` 两个键；是 **list/tuple**
  时直接当消息列表。

所以这个类**同时**满足两条路：实现 ``__call__``（可调用）**并且**提供那四个
方法名，任何一种探测都能命中。

它做的事就是 provider 侧适配：

1. 把系统提示合成一条 ``system`` 消息放到最前（缺失时才补）；
2. 把带 ``content blocks`` 的消息压成后端认得的纯文本/字典；
3. 剥掉后端不认识的字段（``id`` / ``name`` / ``metadata`` …），只留
   ``role`` / ``content``；
4. 工具声明原样透传。

格式化失败一律**返回原样**：格式化的目的是让请求更兼容，不该因为它把一轮
对话弄挂。
"""
import typing
import domains.agent.domain.value_objects.message as message


@typing.runtime_checkable
class MessageFormatter(typing.Protocol):


    async def format(
        self,
        msgs: list[message.Msg],
        **kwargs: object,
    ) -> list[message.JsonObject]:...

