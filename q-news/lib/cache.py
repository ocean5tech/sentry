"""1h 缓存层 (新闻刷新快, 比 q-fin 24h 短).
JSON 文件存 dict, key=namespace+args."""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable


class JsonCache:
    def __init__(self, cache_dir: Path | str, ttl_hours: float = 1, disabled: bool = False):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_hours * 3600
        self.disabled = disabled

    def _key(self, ns: str, *args) -> str:
        h = hashlib.sha256(f"{ns}:{json.dumps(args, default=str, ensure_ascii=False)}".encode()).hexdigest()[:16]
        return f"{ns}_{h}"

    def get_or_call(self, ns: str, fn: Callable[[], Any], *args) -> Any:
        if self.disabled:
            return fn()
        k = self._key(ns, *args)
        p = self.dir / f"{k}.json"
        if p.exists() and (time.time() - p.stat().st_mtime) < self.ttl:
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        out = fn()
        try:
            p.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception:
            pass
        return out
