import pathlib
import contextlib
import collections.abc as abc
import os
import time
import domains.persistence.domain.file_lock_policy as file_lock_policy

DEFAULT_FILE_LOCK_SECONDS = file_lock_policy.DEFAULT_FILE_LOCK_TIMEOUT_SECONDS


class FileLockTimeoutError(TimeoutError):
    pass

class FileLockUnsafePathError(RuntimeError):
    pass

@contextlib.contextmanager
def file_lock(
    lock_file: pathlib.Path,
        *,
    timeout_seconds: float = file_lock_policy.DEFAULT_FILE_LOCK_TIMEOUT_SECONDS,
    timeout: float | None = None,
)-> abc.Iterator[None]:
    """Acquire an inter-process lock represented by an atomically-created file."""
    if timeout is not None:
        timeout_seconds = timeout
    lock_path = pathlib.Path(lock_file)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    file_descriptor: int | None = None

    while file_descriptor is None:
        try:
            file_descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
            os.write(file_descriptor, f"{os.getpid()}\n".encode("ascii"))
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise FileLockTimeoutError(f"Timed out waiting for file lock: {lock_path}")
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        except OSError as exc:
            raise FileLockUnsafePathError(f"Unable to create file lock: {lock_path}") from exc

    try:
        yield
    finally:
        try:
            os.close(file_descriptor)
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass