"""Tiny TTL cache for expensive collectors.

Wraps functions so repeated calls within `ttl` seconds return the cached
result. Safe for read-only pure-ish collectors that are called multiple
times per menubar tick (get_windows, load_recent_sessions, etc.).
"""
from __future__ import annotations
import time
import threading
import functools
from typing import Any, Callable


_lock = threading.Lock()
_store: dict[tuple, tuple[float, Any]] = {}


def ttl_cache(ttl: float):
    """Decorator: cache result of fn(*args, **kwargs) for `ttl` seconds.

    Cache key = (fn qualname, args, frozenset(kwargs.items())).
    Thread-safe. Exceptions are NOT cached."""
    def deco(fn: Callable):
        qn = getattr(fn, "__qualname__", fn.__name__)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = (qn, args, tuple(sorted(kwargs.items())))
            now = time.time()
            with _lock:
                hit = _store.get(key)
                if hit and (now - hit[0]) < ttl:
                    return hit[1]
            result = fn(*args, **kwargs)
            with _lock:
                _store[key] = (now, result)
            return result

        def clear():
            with _lock:
                keys = [k for k in _store if k[0] == qn]
                for k in keys:
                    _store.pop(k, None)

        wrapper.cache_clear = clear  # type: ignore
        return wrapper
    return deco


def clear_all() -> None:
    with _lock:
        _store.clear()
