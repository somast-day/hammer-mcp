from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hammer_mcp.environment import Environment
from hammer_mcp.keyvalues import MapError
from hammer_mcp.maps import Document
from hammer_mcp.storage import Store


VMAP_MATS = '''<!-- dmx encoding keyvalues2 4 format vmap 40 -->
"$prefix_element$" {
 "id" "elementid" "prefix"
 "map_asset_references" "string_array" [ "materials/a/old.vmat", "materials/z/other.vmat" ]
}
"CMapRootElement" {
 "id" "elementid" "root"
 "world" "CMapWorld" {
  "id" "elementid" "world" "nodeID" "int" "1"
  "children" "element_array" []
 }
}
"CMapMesh" {
 "id" "elementid" "mesh-a"
 "meshData" "CDmePolygonMesh" {
  "id" "elementid" "meshdata-a"
  "materials" "string_array" [ "materials/a/old.vmat", "materials/a/keep.vmat" ]
 }
}
"CMapMesh" {
 "id" "elementid" "mesh-b"
 "meshData" "CDmePolygonMesh" {
  "id" "elementid" "meshdata-b"
  "materials" "string_array" [ "materials/a/old.vmat" ]
 }
}
"CMapStaticOverlay" {
 "id" "elementid" "overlay-c"
 "meshData" "CDmePolygonMesh" {
  "id" "elementid" "meshdata-c"
  "materials" "string_array" [ "materials/a/keep.vmat" ]
 }
}
'''


def _setup(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"state_dir": str(tmp_path / "state"), "project_roots": [str(project)]}))
    env = Environment(cfg, games=[])
    return env, project, Store(env.state)


def write_map(folder, text):
    path = folder / "map.vmap"
    path.write_text(text)
    return path


def test_replace_material_mesh_and_asset_list(tmp_path):
    env, folder, store = _setup(tmp_path)
    path = write_map(folder, VMAP_MATS)
    doc = Document(path, env)
    result = doc.replace_material("materials/a/old.vmat", "materials/b/new.vmat", 2)
    assert result["mesh_items"] == 2
    assert result["asset_list_items"] == 1
    assert result["asset_list_sorted_after"] is True
    assert doc.text.count('"materials/b/new.vmat"') == 3
    assert '"materials/a/old.vmat"' not in doc.text
    assert '"materials/a/keep.vmat"' in doc.text
    assert '"materials/z/other.vmat"' in doc.text
    saved = doc.save(store, doc.original_hash)
    assert saved["changed"]
    assert Document(path, env).text.count('"materials/b/new.vmat"') == 3


def test_replace_material_count_mismatch_changes_nothing(tmp_path):
    env, folder, _ = _setup(tmp_path)
    doc = Document(write_map(folder, VMAP_MATS), env)
    before = doc.text
    with pytest.raises(MapError, match="found 2"):
        doc.replace_material("materials/a/old.vmat", "materials/b/new.vmat", 5)
    assert doc.text == before


def test_replace_material_dry_run_writes_nothing(tmp_path):
    env, folder, store = _setup(tmp_path)
    path = write_map(folder, VMAP_MATS)
    doc = Document(path, env)
    result = doc.replace_material("materials/a/old.vmat", "materials/b/new.vmat", 2)
    preview = doc.save(store, doc.original_hash, dry_run=True)
    assert preview["written"] is False
    assert result["mesh_items"] == 2
    assert path.read_text() == VMAP_MATS


def test_replace_material_overlay_and_asset_flag(tmp_path):
    env, folder, _ = _setup(tmp_path)
    doc = Document(write_map(folder, VMAP_MATS), env)
    result = doc.replace_material("materials/a/keep.vmat", "materials/b/keep2.vmat", 2,
                                  include_asset_list=False)
    assert result["mesh_items"] == 2
    assert result["asset_list_items"] == 0
    assert result["asset_list_sorted_after"] is None
    assert '"materials/a/keep.vmat"' not in doc.text
