from domains.agent.domain.entities import agent_compression_state

SYNC_AGENT_WAIT_TIMEOUT_SECONDS=300.0
COMPRESSION_GATE_WAIT_TIMEOUT_SECONDS=300.0
COMPRESSION_GATE_POLL_INTERVAL_SECONDS=0.05

class AgentAlreadyRunning(RuntimeError):
    def __init__(self, agent_id: str) -> None:
        super().__init__(f"Agent '{agent_id}' is already running")
        self.agent_id = agent_id

class AgentCompressionBusyTimeout(RuntimeError):


    def __init__(self,
        agent_id:str,
        compression_state:agent_compression_state.AgentCompressionState |None = None,
    )->None:
        run_id = compression_state.compression_run_id if compression_state is not None else None
        detail = f"compression_run_id={run_id}" if run_id else ""
        super().__init__(
            f"Agent'{agent_id}' is still compressing after"
            f"{COMPRESSION_GATE_WAIT_TIMEOUT_SECONDS:.Of}s: {detail}"
            "normal invocation was not started"
        )
        self.agent_id = agent_id
        self.compression_state = compression_state