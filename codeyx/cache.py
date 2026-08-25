from __future__ import annotations

import os
import threading


class FileCache:
    def __init__(self) -> None:
        # path -> (content, mtime_ns, size); the stat pair lets get_fresh()
        # detect edits that never went through invalidate().
        self._store: dict[str, tuple[str, int, int]] = {}
        self._lock = threading.Lock()

    def get(self, path: str) -> str | None:
        with self._lock:
            entry = self._store.get(path)
        return entry[0] if entry is not None else None

    def get_fresh(self, path: str) -> str | None:
        """Content only if the file on disk still matches the cached state.

        External writes (Bash ``sed -i``, ``git checkout``, editors) bypass
        invalidate(), so a plain get() would serve stale content forever."""
        with self._lock:
            entry = self._store.get(path)
        if entry is None:
            return None
        content, mtime_ns, size = entry
        try:
            st = os.stat(path)
        except OSError:
            with self._lock:
                self._store.pop(path, None)
            return None
        if st.st_mtime_ns != mtime_ns or st.st_size != size:
            with self._lock:
                self._store.pop(path, None)
            return None
        return content

    def put(self, path: str, content: str) -> None:
        try:
            st = os.stat(path)
            meta = (st.st_mtime_ns, st.st_size)
        except OSError:
            # Unstatable at write time: cache with a sentinel that can never
            # match a future stat, i.e. effectively uncached.
            meta = (-1, -1)
        with self._lock:
            self._store[path] = (content, meta[0], meta[1])

    def invalidate(self, path: str) -> None:
        with self._lock:
            self._store.pop(path, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
