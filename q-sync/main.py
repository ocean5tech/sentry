#!/usr/bin/env python3
"""
q-sync: 下载 TDX 全市场日线 zip, 解压到 data/tdx/.
单一备份循环 (data/backup/tdx/ ← 上次的当天).
失败自动回滚.
"""

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from _download import run_download

DEFAULT_CFG = ROOT / "config.yaml"


def main():
    ap = argparse.ArgumentParser(prog="q-sync", description="下载 TDX 日线 zip")
    ap.add_argument("--config", default=str(DEFAULT_CFG))
    ap.add_argument("--from-zip", default=None,
                    help="跳过下载, 用本地 zip 解压 (内网 proxy 拦时用. 例: /mnt/c/Users/.../Downloads/hsjday.zip)")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    rc = run_download(
        name=cfg["name"],
        url=cfg["url"],
        target_dir=cfg["target_dir"],
        backup_dir=cfg["backup_dir"],
        from_zip=args.from_zip,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
