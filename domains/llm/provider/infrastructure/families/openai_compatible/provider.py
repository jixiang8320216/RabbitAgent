
import domains.llm.provider.infrastructure.protocols.openai_compatible.adapter as provider_base
import domains.llm.provider.infrastructure.protocols.openai_compatible.protocol_strategy as openai_protocol_strategy


NativeOpenAiChatModel= provider_base.NativeOpenAiChatModel
DeepSeekReasoningFormatter= provider_base.DeepSeekReasoningFormatter
KimitChatFormatter= provider_base.KimitChatFormatter
StringContentOpenAIFormatter= provider_base.StringContentOpenAIFormatter
TruncationgOpenaiChatFormatter= provider_base.TruncationgOpenaiChatFormatter

class OpenAIProvider(provider_base.OpenAICompatibleAdapterBase):

     def _resolve_protocol_strategy(self)->openai_protocol_strategy.OpenAIProtocolStrategy:
         return openai_protocol_strategy.resolve_openai_protocol_strategy(
             self.config.api_protocol,
             chat_completions_model_type = NativeOpenAiChatModel,
             deepseek_formatter= DeepSeekReasoningFormatter,
             string_content_formatter= StringContentOpenAIFormatter,
             kimi_formatter= KimitChatFormatter,
             default_formatter= TruncationgOpenaiChatFormatter,
         )

     def get_provider_name(self)->str:
        return "openai"