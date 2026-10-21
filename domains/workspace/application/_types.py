"""domains.workspace.application 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import domains.workspace.application.version_control as version_control


WorkspaceVersionService = version_control.WorkspaceVersionService
