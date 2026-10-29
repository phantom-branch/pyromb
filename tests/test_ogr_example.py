"""Ensure the standalone example uses OGR without the PyShp dependency."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

RORB_SHA256 = "67ae955b515a0594298fa1b0e48005d4d12db520e0a7dc582f37e6e06d6ad815"


@pytest.fixture
def example(monkeypatch):
    """Load the standalone example without requiring it to be installed."""
    pytest.importorskip("osgeo.ogr")
    path = Path(__file__).resolve().parents[1] / "src" / "examples" / "app.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("pyromb_example_ogr", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ogr_example_matches_existing_rorb_output(example, tmp_path, monkeypatch):
    """Run the example end-to-end against the tracked shapefiles."""
    assert all(
        path.is_file()
        for path in (example.REACH_PATH, example.BASIN_PATH, example.CENTROID_PATH, example.CONFUL_PATH)
    )
    monkeypatch.setattr(example, "DIR", tmp_path / "examples")
    example.main()
    output = (tmp_path / "vector.catg").read_text()
    assert hashlib.sha256(output.encode()).hexdigest() == RORB_SHA256


def test_ogr_example_preserves_hole_detection(example):
    """The adapter must not drop interior rings from the original regression fixture."""
    from pyromb.math import geometry

    source = example.OGRVectorLayer(
        example.DATA_DIR / "bc_basins" / "original.gpkg"
    )
    assert len(source) == 15
    with pytest.raises(ValueError, match="multiple rings.*holes"):
        geometry.ogr_polygon(source.geometry(6))
