"""通用下载 + 备份 + 回滚逻辑.
被 q-sync (日线) / q-sync-fin (财报) 共享.

流程:
  1. 备份文件夹清空 (backup/<name>/)
  2. 当天文件夹剪到备份 (data/<name>/ → backup/<name>/)
  3. 下载 zip 到 /tmp/, 解压到当天 (data/<name>/)
  4. 任意步骤失败 → 当天清空, 备份 cp 回当天 (回滚)

约定:
  - 路径都是绝对路径, config 写死
  - 备份永远是单一文件夹 (不带日期), 每次下载循环覆盖
  - zip 下载到 /tmp/, 解压完即删 (不占长期空间)
"""

import os
import shutil
import sys
import time
import subprocess
from pathlib import Path


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_download(name: str, url: str, target_dir: str, backup_dir: str,
                 from_zip: str | None = None, log_file: str | None = None):
    """下载 zip, 解压到 target, 失败回滚.
    name: 'tdx' / 'tdx_fin' (用于打印)
    target_dir: 当天文件夹
    backup_dir: 备份文件夹
    from_zip: 可选, 跳过下载用本地 zip 路径 (内网 proxy 拦时用)
    """
    target = Path(target_dir)
    backup = Path(backup_dir)

    # from_zip 模式: 跳过下载, 直接用本地 zip
    if from_zip:
        local_zip = Path(from_zip).expanduser()
        if not local_zip.exists():
            log(f"[{name}] ❌ --from-zip 路径不存在: {local_zip}")
            return 2
        tmp_zip = local_zip      # 用本地 zip
        skip_download = True
    else:
        tmp_zip = Path("/tmp") / f"{name}_{int(time.time())}.zip"
        skip_download = False

    target.parent.mkdir(parents=True, exist_ok=True)
    backup.parent.mkdir(parents=True, exist_ok=True)

    # ─────── Step 1: 清空备份 ───────
    if backup.exists():
        log(f"[{name}] 清空旧备份 {backup}")
        shutil.rmtree(backup)

    # ─────── Step 2: 当天剪到备份 ───────
    if target.exists():
        log(f"[{name}] {target} → {backup} (备份)")
        shutil.move(str(target), str(backup))
    else:
        log(f"[{name}] {target} 不存在, 跳过备份步骤")

    # ─────── Step 3: 下载 + 解压 ───────
    target.mkdir(parents=True, exist_ok=True)
    try:
        if skip_download:
            log(f"[{name}] 跳过下载, 用本地 zip: {tmp_zip}")
        else:
            log(f"[{name}] 下载 {url}")
            subprocess.run(
                ["wget", "-q", "--show-progress", "--timeout=300", "--tries=2",
                 "-O", str(tmp_zip), url],
                check=True,
                stderr=subprocess.STDOUT,
            )
        size_mb = tmp_zip.stat().st_size / 1024 / 1024
        log(f"[{name}] zip {size_mb:.1f} MB → {tmp_zip}")

        log(f"[{name}] 解压到 {target}")
        subprocess.run(
            ["unzip", "-q", "-o", str(tmp_zip), "-d", str(target)],
            check=True,
        )

        n_files = sum(1 for _ in target.rglob("*") if _.is_file())
        log(f"[{name}] ✅ 解压完成, {n_files} 个文件")

    except Exception as e:
        log(f"[{name}] ❌ 下载/解压失败: {type(e).__name__}: {e}")
        log(f"[{name}] 回滚: 清空 {target}, 备份 cp 回")
        if target.exists():
            shutil.rmtree(target)
        if backup.exists():
            shutil.copytree(backup, target)
            log(f"[{name}] 回滚完成, 当天 = 上次备份")
        else:
            log(f"[{name}] ⚠️ 备份也不存在, 当天文件夹空")
        # 清理 tmp (仅自己下的 zip)
        if not skip_download and tmp_zip.exists():
            tmp_zip.unlink()
        return 1

    # ─────── Step 4: 清理 tmp zip (仅删自己下载的, from-zip 模式不删用户 zip) ───────
    if not skip_download and tmp_zip.exists():
        tmp_zip.unlink()
        log(f"[{name}] 清理 tmp zip")

    return 0
