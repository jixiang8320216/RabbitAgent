import collections
import threading

import domains.llm.provider.application.capability.provider_module as provider_module


class _AuthenticationRuntimeSlot:
    def __init__(
        self,
        factory: collections.abc.Callable[[],provider_module.ProviderAuthenticationRuntimePort],
         )->None:
        self._factory = factory
        self._runtime: provider_module.ProviderAuthenticationRuntimePort|None = None

# 职责： 进程级单例注册表，管理所有Provider 家族模块的注册、查询和生命周期。是Provider能力的唯一权威查询肉蔻（canonical lookup）和生命周期所有者（lifecycle owner）
class ProviderModuleRegistry:
    """类型化Provider 能力的权威查询柔和与生命周期所有者"""

    _module: dict[str,provider_module.ProviderModule]={}   #family_id -》模块
    _provider_index: dict[str,provider_module.ProviderModule]={}  #provider_id ->模块（反向索引）
    _registration_index: dict[str,provider_module.ProviderAdapterRegistration]={} #provider_id -> 适配器注册
    _authentication_runtime_slots: dict[str,_AuthenticationRuntimeSlot] ={}  #family_id -> 认证运行时
    _started_capabilities: list[provider_module.ProviderCapabilityLifecycle] =[] #已启动的能力（按顺序）
    _lock = threading.RLock() #线程安全锁，生命周期启动时允许同线程重入查询

    @classmethod
    def get_modules(cls)->tuple[provider_module.ProviderModule,...]:
        with cls._lock:
            return tuple(module for _, module in sorted(cls._module.items()))

    @classmethod
    def get_module(cls, provider_id: str) -> provider_module.ProviderModule | None:
        """Return the module owning a provider ID, if it is registered."""
        with cls._lock:
            return cls._provider_index.get(provider_id.strip().lower())

    # z注册一个Provider family 模块到全局注册表，建立family_id->module、provider_id->module、provider_id->registration
    # 多重索引，并初始化该family的认证运行时槽位。
    @classmethod
    def register(cls,module: provider_module.ProviderModule)->None:
        """register provider module withoutout allowing cross-family Provider ID ownership"""
        with cls._lock:
            #1 幂等检查： 同一个对象已注册-》 直接返回
            existing_module = cls._module.get(module._family_id)
            if existing_module is module:
                return
            #2. 冲突检查： family_id 已被其他对象占用—》 爆错
            if existing_module is not None:
                raise ValueError(f"Provider family {module._family_id} already registered")
            #3. 交叉冲突检查： 任何provider_id 已被其他家族注册-》报错
            for registration in module.registrations:
                if registration.provider_id in cls._registration_index:
                    raise ValueError(f"Provider {registration.provider_id} already registered")
            #4 注册模块
            cls._module[module._family_id] = module
            runtime_factory = module.capabilities.authentication.runtime_factory
            if runtime_factory is not None:
                cls._authentication_runtime_slots[module._family_id] = _AuthenticationRuntimeSlot(
                    runtime_factory
                )
            # 5. 注册所有的provider_id 的反向索引
            for registration in module.registrations:
                cls._registration_index[registration.provider_id] = registration
                cls._provider_index[registration.provider_id] = module


    @classmethod
    def start_capabilities(cls)->None:
        """start provider module registration capabilities"""
        with cls._lock:
            if cls._started_capabilities:
                return
            started: list[provider_module.ProviderCapabilityLifecycle] = []
            try:
                for module in cls.get_modules():
                    authentication_slot = cls._authentication_runtime_slots.get(module._family_id)
                    if authentication_slot is not None:
                        authentication_slot.start()
                        started.append(authentication_slot)
                    for capability in module.capabilities.lifecycle_capabilities():
                        capability.start()
                        started.append(capability)
            except Exception as startup_error:
                cleanup_error:Exception |None= None
                for capability in reversed(started):
                    try:
                        capability.shutdown()
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
                if cleanup_error is not None:
                    startup_error.add_note(
                        f"Provider capabilities rollback also failed: {cleanup_error}"
                    )
                raise
            cls._started_capabilities = started



















