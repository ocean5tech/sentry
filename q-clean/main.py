#!/usr/bin/env python3
"""
q-clean: 按 retention 删旧文件, 释放磁盘.
默认 dry-run (打印会删什么), --apply 真删.
"""

import argparse
import fnmatch
import os
import shutil
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).parent


def parse_args():
    ap = argparse.ArgumentParser(prog="q-clean", description="按 retention 删旧文件释放磁盘")
    ap.add_argument("--apply", action="store_true", help="真删 (默认 dry-run)")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--quiet", action="store_true", help="少打印, 仅最终汇总")
    return ap.parse_args()


def log(msg, quiet=False):
    if not quiet:
        print(msg, file=sys.stderr)


def matches_never_delete(path: Path, never_patterns: list[str]) -> bool:
    s = str(path)
    for p in never_patterns:
        if fnmatch.fnmatch(s, p) or fnmatch.fnmatch(path.name, Path(p).name):
            return True
    return False


def find_expired(target: dict, never_patterns: list[str]) -> list[Path]:
    """返回该 target 下所有过期文件."""
    base = Path(target["path"]).expanduser()
    if not base.exists():
        return []
    pattern = target.get("pattern", "*")
    retention_days = target.get("retention_days")
    if retention_days is None:
        return []
    cutoff = time.time() - retention_days * 86400

    out = []
    for f in base.rglob(pattern):
        if not f.is_file():
            continue
        if matches_never_delete(f, never_patterns):
            continue
        try:
            if f.stat().st_mtime < cutoff:
                out.append(f)
        except OSError:
            pass
    return out


def disk_free_gb(path: str) -> float:
    try:
        st = shutil.disk_usage(path)
        return st.free / (1024 ** 3)
    except Exception:
        return -1


def main():
    args = parse_args()
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"ERROR: config not found: {cfg_path}", file=sys.stderr)
        sys.exit(1)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    never = cfg.get("never_delete", []) or []
    targets = cfg.get("targets", []) or []

    total_files = 0
    total_bytes = 0
    by_target: dict[str, dict] = {}

    for t in targets:
        files = find_expired(t, never)
        sz = sum(f.stat().st_size for f in files if f.exists())
        by_target[t["path"]] = {"count": len(files), "bytes": sz, "files": files}
        total_files += len(files)
        total_bytes += sz

    # 打印
    log("=" * 70, args.quiet)
    log(f"q-clean {'APPLY' if args.apply else 'DRY-RUN'}: 扫到 {total_files} 个过期文件, 共 {total_bytes / 1024 / 1024:.1f} MB", args.quiet)
    log("=" * 70, args.quiet)

    for path, info in by_target.items():
        if info["count"] == 0:
            continue
        log(f"\n{path}: {info['count']} files, {info['bytes'] / 1024 / 1024:.1f} MB", args.quiet)
        # 仅打印前 5 个示例
        for f in info["files"][:5]:
            age_days = (time.time() - f.stat().st_mtime) / 86400
            log(f"  - {f.name} ({age_days:.0f} 天前)", args.quiet)
        if info["count"] > 5:
            log(f"  ... 还有 {info['count'] - 5} 个", args.quiet)

    # 真删
    if args.apply:
        deleted_files = 0
        deleted_bytes = 0
        errors = 0
        for path, info in by_target.items():
            for f in info["files"]:
                try:
                    sz = f.stat().st_size
                    f.unlink()
                    deleted_files += 1
                    deleted_bytes += sz
                except Exception as e:
                    log(f"  ERR删 {f}: {e}", args.quiet)
                    errors += 1
        print(f"\n✅ 已删 {deleted_files}/{total_files} 文件, 释放 {deleted_bytes / 1024 / 1024:.1f} MB ({errors} 错)")
    else:
        print(f"\n[dry-run] 加 --apply 真删")

    # 磁盘检查
    free_gb = disk_free_gb("/home/wyatt")
    threshold = cfg.get("min_disk_free_gb", 5)
    if 0 <= free_gb < threshold:
        print(f"\n⚠️  磁盘 free 仅 {free_gb:.1f} GB < {threshold} GB 阈值", file=sys.stderr)
    elif free_gb >= 0:
        print(f"\n💾 磁盘 free: {free_gb:.1f} GB", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[q-clean] interrupted", file=sys.stderr); sys.exit(130)
