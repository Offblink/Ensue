"""方案 JSON 往返与设置读写。"""

from __future__ import annotations

import json

import pytest

from sealmark import specs
from sealmark.seal import SealSpec


def test_roundtrip(tmp_path):
    spec = SealSpec(
        kind="oval",
        arc_text="某某医院",
        center_text="诊断专用章",
        bottom_prefix="(22)",
        bottom_text="1101011001124",
        star=False,
        size_mm=45,
        aspect=0.66,
        color="#1F4E9C",
        aging=0.7,
        seed=7,
    )
    path = specs.save_spec(spec, tmp_path / "方案.json")
    back = specs.load_spec(path)
    assert back == spec


def test_load_spec_accepts_bare_dict(tmp_path):
    path = tmp_path / "bare.json"
    path.write_text(json.dumps({"kind": "rect", "arc_text": "张三印"}, ensure_ascii=False), encoding="utf-8")
    spec = specs.load_spec(path)
    assert spec.kind == "rect" and spec.arc_text == "张三印"
    assert spec.size_mm == SealSpec().size_mm, "缺的键用默认值补齐"


def test_from_dict_ignores_junk_and_wrong_types():
    spec = specs.spec_from_dict({"kind": "round", "size_mm": "45", "不认识的键": 1, "star": 0, "aging": 2})
    assert spec.size_mm == 45.0
    assert spec.star is False
    assert spec.aging == 2.0


def test_from_dict_survives_garbage():
    assert specs.spec_from_dict(None) == SealSpec()
    assert specs.spec_from_dict({}) == SealSpec()


def test_load_spec_rejects_wrong_shape(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError):
        specs.load_spec(path)


def test_save_settings_merges(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(specs, "APP_DIR", tmp_path)
    monkeypatch.setattr(specs, "SETTINGS_PATH", tmp_path / "settings.json")
    specs.save_settings(seal={"kind": "round"})
    specs.save_settings(out_dir="D:/out")
    data = specs.load_settings()
    assert data == {"seal": {"kind": "round"}, "out_dir": "D:/out"}
    capsys.readouterr()
