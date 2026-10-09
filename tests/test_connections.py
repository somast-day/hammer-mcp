from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hammer_mcp.environment import Environment
from hammer_mcp.keyvalues import MapError
from hammer_mcp.maps import Document
from hammer_mcp.storage import Store
import json


VMAP_TWO = '''<!-- dmx encoding keyvalues2 4 format vmap 40 -->
"CMapRootElement" {
 "id" "elementid" "root"
 "world" "CMapWorld" {
  "id" "elementid" "world" "nodeID" "int" "1"
  "children" "element_array" [ "element" "entity-id" ]
 }
}
"CMapEntity" {
 "id" "elementid" "entity-id" "nodeID" "int" "2"
 "referenceID" "uint64" "0x0"
 "children" "element_array" []
 "entity_properties" "EditGameClassProps" {
  "id" "elementid" "properties-id"
  "classname" "string" "func_button" "targetname" "string" "btn"
 }
 "origin" "vector3" "0 0 0" "angles" "qangle" "0 0 0" "scales" "vector3" "1 1 1"
 "connectionsData" "element_array" [
  "DmeConnectionData"
  {
   "id" "elementid" "conn-first"
   "outputName" "string" "OnPressed"
   "targetType" "int" "7"
   "targetName" "string" "first_target"
   "inputName" "string" "Trigger"
   "overrideParam" "string" ""
   "delay" "float" "0"
   "timesToFire" "int" "-1"
  },
  "DmeConnectionData"
  {
   "id" "elementid" "conn-second"
   "outputName" "string" "OnPressed"
   "targetType" "int" "7"
   "targetName" "string" "second_target"
   "inputName" "string" "Trigger"
   "overrideParam" "string" ""
   "delay" "float" "0"
   "timesToFire" "int" "-1"
  }
 ]
}
'''

VMAP_EMPTY = '''<!-- dmx encoding keyvalues2 4 format vmap 40 -->
"CMapRootElement" {
 "id" "elementid" "root"
 "world" "CMapWorld" {
  "id" "elementid" "world" "nodeID" "int" "1"
  "children" "element_array" [ "element" "entity-id" ]
 }
}
"CMapEntity" {
 "id" "elementid" "entity-id" "nodeID" "int" "2"
 "referenceID" "uint64" "0x0"
 "children" "element_array" []
 "entity_properties" "EditGameClassProps" {
  "id" "elementid" "properties-id"
  "classname" "string" "func_button" "targetname" "string" "btn"
 }
 "origin" "vector3" "0 0 0" "angles" "qangle" "0 0 0" "scales" "vector3" "1 1 1"
 "connectionsData" "element_array" []
}
'''

VMF_ONE = '''versioninfo { "editorversion" "400" }
world { "id" "1" "classname" "worldspawn" }
entity {
 "id" "2" "classname" "func_button" "targetname" "btn"
 "origin" "0 0 0"
 connections { "OnPressed" "door,Open,,0,-1" }
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


def test_insert_index_zero_puts_row_first(setup):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmap", VMAP_TWO), env)
    result = doc.add_connection("entity-id", "OnPressed", "zero_target", "Trigger", "", 0, -1, index=0)
    assert [c["target"] for c in result["connections"]] == ["zero_target", "first_target", "second_target"]
    assert "conn-first" in doc.text and "conn-second" in doc.text


def test_target_type_validation_rejects_negative_and_bool(setup):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmap", VMAP_EMPTY), env)
    with pytest.raises(MapError, match="nonnegative integer"):
        doc.add_connection("entity-id", "OnPressed", "named_target", "Trigger", "", 0, -1, target_type=-1)
    with pytest.raises(MapError, match="nonnegative integer"):
        doc.add_connection("entity-id", "OnPressed", "named_target", "Trigger", "", 0, -1, target_type=True)


def test_insert_index_out_of_range_is_rejected(setup):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmap", VMAP_TWO), env)
    with pytest.raises(MapError, match="out of range"):
        doc.add_connection("entity-id", "OnPressed", "late_target", "Trigger", "", 0, -1, index=3)
    with pytest.raises(MapError, match="out of range"):
        doc.add_connection("entity-id", "OnPressed", "late_target", "Trigger", "", 0, -1, index=-1)


def test_target_type_is_written_to_kv2(setup):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmap", VMAP_EMPTY), env)
    result = doc.add_connection("entity-id", "OnPressed", "named_target", "Trigger", "", 0, -1, target_type=4)
    assert result["connections"][0]["target_type"] == "4"
    assert '"targetType" "int" "4"' in doc.text


def test_duplicate_connection_is_rejected(setup):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmap", VMAP_TWO), env)
    with pytest.raises(MapError, match="Duplicate connection"):
        doc.add_connection("entity-id", "OnPressed", "first_target", "Trigger", "", 0.0, -1)
    with pytest.raises(MapError, match="Duplicate connection"):
        doc.add_connection("entity-id", "OnPressed", "first_target", "Trigger", "", 0.000001, -1)


def test_set_connection_changes_only_requested_field(setup):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmap", VMAP_TWO), env)
    before = doc.connections(doc.find_entity("entity-id"))
    result = doc.set_connection("entity-id", "conn-first", input_name="Unlock")
    assert result["connections"][0]["input"] == "Unlock"
    assert result["connections"][0]["target"] == before[0]["target"]
    assert result["connections"][0]["delay"] == before[0]["delay"]
    assert result["connections"][1] == before[1]
    assert '"inputName" "string" "Trigger"' in doc.text


def test_set_connection_unknown_id_is_rejected(setup):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmap", VMAP_TWO), env)
    with pytest.raises(MapError, match="matched 0"):
        doc.set_connection("entity-id", "missing-id", input_name="Unlock")


def test_source1_rejects_index_and_target_type(setup):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmf", VMF_ONE), env)
    with pytest.raises(MapError, match="Indexed insertion"):
        doc.add_connection("btn", "OnPressed", "door", "Open", "", 0, -1, index=0)
    with pytest.raises(MapError, match="target_type"):
        doc.add_connection("btn", "OnPressed", "door", "Open", "", 0, -1, target_type=4)
