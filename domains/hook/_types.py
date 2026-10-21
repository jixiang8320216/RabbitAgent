"""domains.hook 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import typing
import domains.hook.domain as domain
import domains.hook.domain.resource_models as resource_models


HookEvent = domain.HookEvent


HookScope =domain.HookScope


HookSourceKind = domain.HookSourceKind


HookRunStatus = domain.HookRunStatus


HookSnapshot = resource_models.HookSnapshot


HookPolicy = resource_models.HookPolicy


HookAgentCompomnentSource = resource_models.HookAgentCompomnentSource


HookRuntimeDirectories = resource_models.HookRuntimeDirectories
