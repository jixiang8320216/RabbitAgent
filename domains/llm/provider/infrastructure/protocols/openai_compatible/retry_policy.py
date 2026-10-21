
import domains.llm.provider.infrastructure.protocols.openai_compatible.chat_model as native_openai_chat_model
import domains.llm.provider.application.authentication.request_attestation as provider_request_attestation
from domains.llm.provider.infrastructure.foundation.chat_transport import failure_classification
import collections.abc as abc
import domains.llm.provider.infrastructure.protocols.openai_compatible.projections as native_transport
import domains.agent.domain.aggregates.runtime_observer as callbacks

class NativeOpenAIChatModel(native_openai_chat_model.NativeOpenAiChatModel):
    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        base_url: str | None = None,
        token_counter: object | None = None,
        agent_name: str = "",
        max_retries_on_empty: int = 5,
        http_proxy: str | None = None,
        https_proxy: str | None = None,
        chunk_idle_timeout: int = 180,
        stream: bool = True,
        ssl_verify: bool = False,
        default_headers: abc.Mapping[str, str] | None = None,
        auth_hook: native_transport.AuthHook| None = None,
        model_identity_resolver: native_transport.ModelIdentityResolver | None = None,
        request_attestor: provider_request_attestation.ProviderRequestAttestorProtocol | None = None,
        llm_provider: str | None = None,
        failure_policy: failure_classification.ProviderFailurePolicy | None = None,
        observer: callbacks.RuntimeObserver | None = None,
        trust_env: bool | None = None,
        image_request: bool = False,
        **model_kwargs: object,
    ) -> None:
        self.image_request = image_request
        super().__init__(
            model_name=model_name,
            api_key=api_key,
            base_url=base_url,
            token_counter=token_counter,
            agent_name=agent_name,
            max_retries_on_empty=max_retries_on_empty,
            http_proxy=http_proxy,
            https_proxy=https_proxy,
            chunk_idle_timeout=chunk_idle_timeout,
            stream=stream,
            ssl_verify=ssl_verify,
            default_headers=default_headers,
            auth_hook=auth_hook,
            model_identity_resolver=model_identity_resolver,
            request_attestor=request_attestor,
            llm_provider=llm_provider,
            failure_policy=failure_policy,
            observer=observer,
            trust_env=trust_env,
            # 注意：不要把 image_request 继续传给基类。基类没有这个形参，
            # 它会落进 **model_kwargs -> self.model_kwargs，并最终被拼进
            # openai SDK 的 AsyncCompletions.create() 调用，导致 SDK 报
            # "create() got an unexpected keyword argument 'image_request'"。
            # 该标志只需在本类保存（供多模态请求判定），不属于 SDK 请求参数。
            **model_kwargs,
        )