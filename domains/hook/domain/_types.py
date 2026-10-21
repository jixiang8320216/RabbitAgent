"""domains.hook.domain 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import typing
import domains.hook.domain.lifecycle_hook_configuration as lifecycle_hook_configuration
import domains.hook.domain.hook_definitions as models
import domains.hook.domain.outputs as outputs
import domains.hook.domain.resource_models as resource_models


HookEvent  = lifecycle_hook_configuration.HookEvent


HookScope = models.HookScope


HookSourceKind = models.HookSourceKind


HookRunStatus = outputs.HookRunStatus


HookSnapshot = resource_models.HookSnapshot


HookPolicy = resource_models.HookPolicy


HookAgentCompomnentSource = resource_models.HookAgentCompomnentSource


HookRuntimeDirectories = resource_models.HookRuntimeDirectories
