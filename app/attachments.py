"""盘点附件：保存在本地目录（不上传真实档案文件，仅登记虚构编号的照片占位）。"""
import re
from pathlib import Path

from . import config

_SAFE = re.compile(r"[^A-Za-z0-9._\-一-龥]")


def save(task_id, box_id, filename, data: bytes):
    folder = config.ATTACH_DIR / f"task_{task_id}" / f"box_{box_id}"
    folder.mkdir(parents=True, exist_ok=True)
    safe = _SAFE.sub("_", filename) or "attachment.bin"
    path = folder / safe
    # 同名追加序号，不覆盖
    if path.exists():
        stem, suffix = path.stem, path.suffix
        i = 1
        while path.exists():
            path = folder / f"{stem}_{i}{suffix}"
            i += 1
    path.write_bytes(data)
    return path


def list_for_box(task_id, box_id):
    folder = config.ATTACH_DIR / f"task_{task_id}" / f"box_{box_id}"
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.iterdir()):
        if p.is_file():
            out.append({"name": p.name, "size": p.stat().st_size,
                        "rel": f"task_{task_id}/box_{box_id}/{p.name}"})
    return out
