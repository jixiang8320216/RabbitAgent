"""domains.persistence.infrastructure.file 的类型定义（迁移期自动拆出，避免包导入期求值）。"""

import typing
from fsspec.utils import atomic_write
import domains.persistence.infrastructure.file.file_lock_impl as file_lock_module
import domains.persistence.infrastructure.file.atomic_write as atomic_write


FileLockTimeoutError = file_lock_module.FileLockTimeoutError


FileLockUnsafePathError = file_lock_module.FileLockUnsafePathError


file_lock =file_lock_module.file_lock


atomic_write_text= atomic_write.atomic_write_text


atomic_write_json = atomic_write.atomic_write_json
