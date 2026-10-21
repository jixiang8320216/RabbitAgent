import asyncio
import logging


import domains.agent.domain.aggregates.cancellation as cancellation
import domains.agent.domain.aggregates.execution_context as execution_context
import domains.agent.domain.aggregates.runtime_bindings as runtime_bindings
import domains.agent.domain.aggregates.scope_context as scope_context

logger = logging.getLogger(__name__)

_INTERRUPT_POLL_INTERVAL_SECONDS=0.1

def is_session_interrupted() -> bool:
    try:
        execution_scope = scope_context.get_turn_scope_context()
        if execution_scope is not None:
            if execution_scope.cancelled:
                return True
            if  not isinstance(execution_scope, cancellation.TurnCancelScope):
                return False
        session_id = execution_context.get_session_id()
        if not session_id:
            return False
        checker = runtime_bindings.get_interrupt_checker()
        if checker is None:
            return False
        try:
            turn_scope = checker.get_turn_scope(session_id)
            if turn_scope is not None and turn_scope.cancelled:
                return True
        except Exception as exc:
            logger.error(exc)
        return checker.check_interrupt(session_id)
    except Exception as exc:
        logger.error(exc)
        return False


async def interrupt_sleep(seconds:float, agent_name: str="")->None:
    if seconds <0:
        raise ValueError("Interruptible sleep duration must be non-negative")
    if is_session_interrupted():
        await cancellation.propagate_cancelled_error(f"LLM sleep interrupted for agent {agent_name}")

    remaining=seconds
    while remaining > 0:
        interval = min(_INTERRUPT_POLL_INTERVAL_SECONDS, remaining)
        await asyncio.sleep(interval)
        remaining -= interval
        if is_session_interrupted():
            await cancellation.propagate_cancelled_error(f"LLM sleep interrupted for agent {agent_name}")

interruptible_sleep=interrupt_sleep
