"""24h 缓存层. JSON 文件存 dict, key=namespace+args."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable


class JsonCache:
    def __init__(self, cache_dir: Path, ttl_hours: int = 24, disabled: bool = False):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_hours * 3600
        self.disabled = disabled

    def _key(self, namespace: str, args: tuple) -> Path:
        h = hashlib.md5(repr(args).encode("utf-8")).hexdigest()[:16]
        sub = self.dir / namespace
        sub.mkdir(parents=True, exist_ok=True)
        return sub / f"{h}.json"

    def get_or_set(self, namespace: str, args: tuple, fn: Callable[[], Any]) -> Any:
        if self.disabled:
            return fn()
        path = self._key(namespace, args)
        if path.exists() and (time.time() - path.stat().st_mtime) < self.ttl:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        result = fn()
        try:
            path.write_text(json.dumps(result, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception:
            pass
        return result
