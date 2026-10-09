from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hammer_mcp.environment import Environment
from hammer_mcp.keyvalues import MapError
from hammer_mcp.maps import Document
from hammer_mcp.storage import Store


VMAP_MODELS = '''<!-- dmx encoding keyvalues2 4 format vmap 40 -->
"CMapRootElement" {
 "id" "elementid" "root"
 "world" "CMapWorld" {
  "id" "elementid" "world" "nodeID" "int" "1"
  "children" "element_array" [ "element" "entity-a", "element" "entity-b", "element" "entity-c" ]
 }
}
"CMapEntity" {
 "id" "elementid" "entity-a" "nodeID" "int" "2"
 "referenceID" "uint64" "0x0"
 "children" "element_array" []
 "entity_properties" "EditGameClassProps" {
  "id" "elementid" "props-a"
  "classname" "string" "prop_static" "model" "string" "models/old_a.vmdl"
 }
 "origin" "vector3" "0 0 0" "angles" "qangle" "0 0 0" "scales" "vector3" "1 1 1"
 "connectionsData" "element_array" []
}
"CMapEntity" {
 "id" "elementid" "entity-b" "nodeID" "int" "3"
 "referenceID" "uint64" "0x0"
 "children" "element_array" []
 "entity_properties" "EditGameClassProps" {
  "id" "elementid" "props-b"
  "classname" "string" "prop_static" "model" "string" "models/old_b.vmdl"
 }
 "origin" "vector3" "64 0 0" "angles" "qangle" "0 0 0" "scales" "vector3" "1 1 1"
 "connectionsData" "element_array" []
}
"CMapEntity" {
 "id" "elementid" "entity-c" "nodeID" "int" "4"
 "referenceID" "uint64" "0x0"
 "children" "element_array" []
 "entity_properties" "EditGameClassProps" {
  "id" "elementid" "props-c"
  "classname" "string" "func_button" "model" "string" "models/old_a.vmdl"
 }
 "origin" "vector3" "128 0 0" "angles" "qangle" "0 0 0" "scales" "vector3" "1 1 1"
 "connectionsData" "element_array" []
}
'''


@pytest.fixture
def setup(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"state_dir": str(tmp_path / "state"), "project_roots": [str(project)]}))
    env = Environment(cfg, games=[])
    return env, project, Store(env.state)


def write(folder, name, text):
    path = folder / name
    path.write_text(text)
    return path


def test_batch_replace_reports_entity_ids(setup):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmap", VMAP_MODELS), env)
    result = doc.replace_property([
        {"key": "model", "from_value": "models/old_a.vmdl", "to_value": "models/new_a.vmdl", "expected_count": 2},
        {"key": "model", "from_value": "models/old_b.vmdl", "to_value": "models/new_b.vmdl", "expected_count": 1},
    ])
    assert [r["count"] for r in result["replacements"]] == [2, 1]
    assert sorted(result["replacements"][0]["entity_ids"]) == ["entity-a", "entity-c"]
    assert doc.text.count('"models/new_a.vmdl"') == 2
    assert doc.text.count('"models/new_b.vmdl"') == 1
    assert "models/old_a.vmdl" not in doc.text and "models/old_b.vmdl" not in doc.text


def test_count_mismatch_changes_nothing(setup):
    env, folder, _ = setup
    path = write(folder, "map.vmap", VMAP_MODELS)
    doc = Document(path, env)
    before = doc.text
    with pytest.raises(MapError, match="found 2"):
        doc.replace_property([
            {"key": "model", "from_value": "models/old_a.vmdl", "to_value": "models/new_a.vmdl", "expected_count": 3},
        ])
    assert doc.text == before


def test_classname_filter_limits_scope(setup):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmap", VMAP_MODELS), env)
    result = doc.replace_property([
        {"key": "model", "from_value": "models/old_a.vmdl", "to_value": "models/new_a.vmdl", "expected_count": 1},
    ], classname="prop_static")
    assert result["replacements"][0]["entity_ids"] == ["entity-a"]
    assert doc.record(doc.find_entity("entity-c"))["properties"]["model"] == "models/old_a.vmdl"


def test_protected_keys_and_same_value_rejected(setup):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmap", VMAP_MODELS), env)
    for key in ("classname", "origin", "id"):
        with pytest.raises(MapError, match="protected"):
            doc.replace_property([
                {"key": key, "from_value": "x", "to_value": "y", "expected_count": 0},
            ])
    with pytest.raises(MapError, match="must differ"):
        doc.replace_property([
            {"key": "model", "from_value": "models/old_a.vmdl", "to_value": "models/old_a.vmdl", "expected_count": 2},
        ])
