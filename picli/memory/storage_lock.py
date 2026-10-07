"""Cross-process coordination for database transactions and artifact maintenance."""
from contextlib import contextmanager
import fcntl


@contextmanager
def storage_lock(directory, *, exclusive=False):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'storage.lock').open('a') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lease, fcntl.LOCK_UN)
