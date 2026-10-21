
import domains.agent.domain.value_objects.tool_call as tool_call
import time
import domains.agent.domain.value_objects.message as message

class ToolCallStreamAccumulator:

    def __init__(self):
        self._pending_by_id:dict[str, tool_call.PendingToolCall] = {}
        self._tool_id_by_index: dict[int,str]={}
        self._started_at = time.monotonic()

    @property
    def active_tool_ids(self)->tuple[str,...]:
        return tuple(self._pending_by_id)

    def ingest_complete(self, block:message.ToolUseBlock)->tuple[tool_call.ToolCallStreamUpdate,...]:
        # ToolUseBlock.__init__ 只在 index 非None 时才把它写进 payload
        # （见 runtime/model/message.py:216），所以 OpenAI 非流式路径返回的
        # 工具调用块**没有** index 属性。原代码无条件读 block.index，每遇到
        # 一个无 index 的完整工具调用就抛
        #     AttributeError: "ToolUseBlock has no attribute 'index'"
        # 导致整条 turn 失败。这里统一用 getattr 兜底成 None。
        block_index = getattr(block, "index", None)
        pending= self._pending_by_id.get(block.id)
        if pending is None:
            pending= tool_call.PendingToolCall(
                tool_id=block.id,
                tool_name=block.name,
                index=block_index,
                stream_started_at=self._started_at,
            )
            self._pending_by_id[block.id] = pending
        updates: list[tool_call.ToolCallStreamUpdate] = []
        if pending.mark_identity():
            updates.append(self._identity_update(pending))
        pending.set_complete_input(block.input)
        fragment= pending.input_preview()
        pending.record_fragment(fragment,"snapshot")
        if block_index is not None:
            self._tool_id_by_index[block_index] = block.id
        if fragment:
            updates.append(
                tool_call.ToolCallStreamUpdate(
                    tool_id= pending.tool_id,
                    tool_name= pending.tool_name,
                    input_stream_phase="arguments",
                    input_preview= pending.input_preview(),
                    input_fragment= fragment,
                    input_fragment_index= pending.allocate_fragment_index(),
                    input_fragment_kind= "snapshot",
                )
            )
        return tuple(updates)

    def ingest_delta(self, block: message.ToolUseDeltaBlock)->tuple[tool_call.ToolCallStreamUpdate,...]|tool_call.ToolDeltaRoutingFailure:
        target_id:str|None=None
        if block.id is not None and block.id in self._pending_by_id:
            target_id = block.id
        elif block.id is not None and block.name is not None:
            pending= tool_call.PendingToolCall(
                tool_id=block.id,
                tool_name=block.name,
                index=block.index,
                stream_started_at=self._started_at,
            )
            self._pending_by_id[block.id] = pending
            target_id = block.id
            if block.index is not None:
                self._tool_id_by_index[block.index] = block.id
        elif block.index is not None:
                target_id = self._tool_id_by_index.get(block.index)
        elif block.id is None and len(self._pending_by_id) ==1:
                target_id = next(iter(self._pending_by_id))

        if target_id is None:
                active_count = len(self._pending_by_id)
                for pending in self._pending_by_id.values():
                    pending.mark_routing_failure(active_count, block.input_delta)
                return tool_call.ToolDeltaRoutingFailure(
                    delta_id= block.id,
                    delta_index= block.index,
                    input_preview= block.input_delta[:80],
                    active_tool_ids= self.active_tool_ids,
                )
        pending = self._pending_by_id[target_id]
        updates: list[tool_call.ToolCallStreamUpdate] = []
        if pending.mark_identity():
            updates.append(self._identity_update(pending))
        if not block.input_delta:
            return tuple(updates)
        if  block.input_delta_kind=="snapshot":
            pending.replace_text(block.input_delta)
        else:
            pending.append_text(block.input_delta)
        pending.record_fragment(block.input_delta, block.input_delta_kind)
        updates.append(tool_call.ToolCallStreamUpdate(
                tool_id= pending.tool_id,
                tool_name= pending.tool_name,
                input_stream_phase="arguments",
                input_preview=pending.input_preview(),
                input_fragment= block.input_delta,
                input_fragment_index= pending.allocate_fragment_index(),
                input_fragment_kind= block.input_delta_kind,
            ))
        return tuple(updates)

    @staticmethod
    def _identity_update(pending:tool_call.PendingToolCall)->tool_call.ToolCallStreamUpdate:
        return tool_call.ToolCallStreamUpdate(
            tool_id=pending.tool_id,
            tool_name=pending.tool_name,
            input_stream_phase="identity",
            input_preview="",
        )

    def resolve(self)->list[tool_call.ToolCallResolution]:
        return [pending.resolve() for pending in self._pending_by_id.values()]

#简化版，不做流式积累，只收集完整的TOOLUseBlock
class ToolArgsSummaryResolver:

    def __init__(self):
        self._blocks:list = []


    def ingest_complete(self, block):
        self._blocks.append(block)
        return [block]

    def ingest_delta(self, block):
        return []

    def resolve(self):
        return []

    def __len__(self):
        return len(self._blocks)


