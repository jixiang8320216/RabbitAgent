""" Agent 基类"""

from abc import ABC, abstractmethod
from typing import Optional
from domains.llm import LLM
from domains.agent.domain.value_objects.agent_config import AgentConfig
from domains.agent.message import Message

class Agent(ABC):
    """Agent 基类"""

    def __init__(
            self,
            name: str,
            llm: LLM,
            system_promot: Optional[str]= None,
             config: Optional[AgentConfig] = None
           ):
        self.name = name
        self.llm = llm
        self.system_promot = system_promot
        self.config = config or AgentConfig()
        self._history: list[Message] =[]

    @abstractmethod
    def run(self, message: Message) -> str:
        """运行Aagent"""
        pass

    def add_message(self, message: Message):
        """添加消息到历史记录"""
        self._history.append(message)

    def clear_history(self):
        """清空历史消息"""
        self._history.clear()

    def  get_history(self)-> list[Message]:
        """获取历史记录"""
        return self._history

    def __str__(self) -> str:
        return f"Agent(name={self.name}, provider ={self.llm.provider})"

    def __repr__(self) -> str:
        return self.__str__()