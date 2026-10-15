"""方案/设置的 JSON 持久化（印章参数存盘、上次用的路径记住）。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, fields
from pathlib import Path

from .seal import SealSpec

APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Ensue"
SETTINGS_PATH = APP_DIR / "settings.json"


def spec_to_dict(spec: SealSpec) -> dict:
    return asdict(spec)


def spec_from_dict(data: dict) -> SealSpec:
    """宽松还原：未知键忽略、缺键用默认值、类型不对就退回默认。"""
    valid = {f.name: f.type for f in fields(SealSpec)}
    kwargs = {}
    for key, value in (data or {}).items():
        if key not in valid:
            continue
        default = getattr(SealSpec(), key)
        if isinstance(default, bool):
            kwargs[key] = bool(value)
        elif isinstance(default, int) and not isinstance(default, bool):
            kwargs[key] = int(value)
        elif isinstance(default, float):
            kwargs[key] = float(value)
        elif isinstance(default, str):
            kwargs[key] = str(value)
    return SealSpec(**kwargs)


def save_spec(spec: SealSpec, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"app": "Ensue", "version": 1, "seal": spec_to_dict(spec)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_spec(path: str | Path) -> SealSpec:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "seal" in data:
        data = data["seal"]
    if not isinstance(data, dict):
        raise ValueError("方案文件格式不对")
    return spec_from_dict(data)


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(**patch) -> None:
    data = load_settings()
    data.update(patch)
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # 记不住不是致命问题


__all__ = [
    "APP_DIR",
    "SETTINGS_PATH",
    "load_settings",
    "load_spec",
    "save_settings",
    "save_spec",
    "spec_from_dict",
    "spec_to_dict",
]
