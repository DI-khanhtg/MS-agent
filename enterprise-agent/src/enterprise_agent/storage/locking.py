"""OS locks for a shared SQLite host volume and multiprocess audit writers."""

import asyncio
import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import anyio


async def run_local(function, *args, _cancel_cleanup=None, **kwargs):
    """Finish local I/O before releasing an owner lock, even on task cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancellation = None
    # AnyIO repeatedly cancels checkpoints inside a cancelled scope. Shield joining
    # the worker to avoid spinning while still honoring direct asyncio cancellation.
    with anyio.CancelScope(shield=True):
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError as exc:
                cancellation = exc
                if task.cancelled():
                    raise
            except BaseException:
                if cancellation:
                    raise cancellation from None
                raise
    if cancellation:
        if _cancel_cleanup:
            _cancel_cleanup(result)
        raise cancellation
    return result


class FileLock:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        if self.file.seek(0, 2) == 0:
            self.file.write(b"\0")
            self.file.flush()

    def acquire(self, blocking: bool = True) -> bool:
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            return True
        except OSError:
            if blocking:
                raise
            return False

    def close(self) -> None:
        # Closing the descriptor releases the OS lock, including after process death.
        self.file.close()


@asynccontextmanager
async def async_file_lock(path: Path):
    lock = FileLock(path)
    try:
        while not lock.acquire(blocking=False):  # noqa: ASYNC110 - poll a cross-process OS lock
            await asyncio.sleep(0.025)
        yield
    finally:
        lock.close()


@contextmanager
def file_lock(path: Path):
    import time

    lock = FileLock(path)
    try:
        while not lock.acquire(blocking=False):
            time.sleep(0.025)
        yield
    finally:
        lock.close()
