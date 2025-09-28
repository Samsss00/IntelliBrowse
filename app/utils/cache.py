# app/utils/cache.py
from __future__ import annotations
import os
import json
import time
import hashlib
from typing import Any, Optional


def _safe_mkdir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass


def _now() -> float:
    return time.time()


class SimpleFileCache:
    """
    Tiny JSON-on-disk cache with TTL.
    Each cache entry is a JSON blob: {"ts": <epoch>, "value": <any json-serializable>}
    """
    def __init__(self, cache_dir: str, ttl_sec: int = 12 * 3600) -> None:
        self.cache_dir = cache_dir
        self.ttl_sec = max(60, int(ttl_sec))
        _safe_mkdir(self.cache_dir)

    def _path_for(self, key: str) -> str:
        h = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, f"{h}.json")

    def get(self, key: str) -> Optional[Any]:
        path = self._path_for(key)
        try:
            if not os.path.exists(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            ts = float(obj.get("ts") or 0)
            if _now() - ts > self.ttl_sec:
                return None
            return obj.get("value")
        except Exception:
            return None

    def set(self, key: str, value: Any) -> bool:
        path = self._path_for(key)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ts": _now(), "value": value}, f, ensure_ascii=False)
            return True
        except Exception:
            return False
