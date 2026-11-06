"""Small process-wide cache for whole-repository computations.

Entries are keyed by the repository's last scan time, so a rescan (which also
happens after every approved change) invalidates them automatically.
"""

import threading
from collections import OrderedDict
from collections.abc import Callable, Hashable

MAX_ENTRIES = 32

_entries: OrderedDict[Hashable, object] = OrderedDict()
_lock = threading.Lock()


def cached[T](key: Hashable, compute: Callable[[], T]) -> T:
    with _lock:
        if key in _entries:
            _entries.move_to_end(key)
            return _entries[key]  # type: ignore[return-value]
    value = compute()  # computed outside the lock; a rare duplicate computation is harmless
    with _lock:
        _entries[key] = value
        while len(_entries) > MAX_ENTRIES:
            _entries.popitem(last=False)
    return value


def clear() -> None:
    with _lock:
        _entries.clear()
