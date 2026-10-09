from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hammer_mcp.environment import Environment
from hammer_mcp.keyvalues import MapError
from hammer_mcp.maps import Document
from hammer_mcp.storage import Store

# typed KV2 fixture: small, no product maps/engines
VMAP = '''<!-- dmx encoding keyvalues2 4 format vmap 40 -->
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
  "classname" "string" "ambient_generic" "message" "string" "sounds/bgm/old.vsnd" "health" "string" "10" "targetname" "string" "bgm"
 }
 "origin" "vector3" "1 2 3" "angles" "qangle" "0 0 0" "scales" "vector3" "1 1 1"
 "connectionsData" "element_array" [
  "DmeConnectionData"
  {
   "id" "elementid" "conn-1"
   "outputName" "string" "OnPressed"
   "targetType" "int" "7"
   "targetName" "string" "bgm"
   "inputName" "string" "PlaySound"
   "overrideParam" "string" ""
   "delay" "float" "0"
   "timesToFire" "int" "-1"
  }
 ]
 "opaque_mesh" "CDmePolygonMesh" {
  "id" "elementid" "mesh-id" "positions" "vector3_array" ["1 2 3", "4 5 6"]
 }
}
'''

# VMF fixture: scalar removal + classname change on Source 1
VMF = '''versioninfo { "editorversion" "400" }
world { "id" "1" "classname" "worldspawn" }
entity {
 "id" "2" "classname" "ambient_generic" "targetname" "bgm" "message" "old" "health" "10"
 "origin" "0 0 0"
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


def test_vmap_remove_scalar_and_change_classname(setup):
    env, folder, store = setup
    doc = Document(write(folder, "map.vmap", VMAP), env)
    result = doc.set_entity("entity-id",
                            {"classname": "snd_event_point", "soundName": "ze_obj_blackout.bgm.x"},
                            remove_properties=["message", "health"])
    props = result["properties"]
    assert props["classname"] == "snd_event_point"
    assert props["soundName"] == "ze_obj_blackout.bgm.x"
    assert "message" not in props and "health" not in props
    assert props["targetname"] == "bgm"
    # unrelated mesh/connection/ID preserved
    assert '"mesh-id"' in doc.text
    assert result["connections"][0]["id"] == "conn-1"
    assert result["id"] == "entity-id"
    # save roundtrip keeps result
    saved = doc.save(store, doc.original_hash)
    assert saved["changed"]
    reread = Document(write(folder, "map2.vmap", doc.text), env)
    assert reread.record(reread.find_entity("entity-id"))["properties"] == props


def test_vmf_remove_scalar_and_change_classname(setup):
    env, folder, store = setup
    doc = Document(write(folder, "map.vmf", VMF), env)
    result = doc.set_entity("bgm", {"classname": "snd_event_point"},
                            remove_properties=["message"])
    assert result["properties"]["classname"] == "snd_event_point"
    assert "message" not in result["properties"]
    assert result["properties"]["health"] == "10"
    doc.save(store, doc.original_hash)


def test_late_missing_key_changes_nothing(setup):
    env, folder, _ = setup
    path = write(folder, "map.vmap", VMAP)
    doc = Document(path, env)
    before = doc.text
    with pytest.raises(MapError):
        doc.set_entity("entity-id", {"soundName": "x"},
                       remove_properties=["health", "missing_key"])
    assert doc.text == before


@pytest.mark.parametrize("remove", [
    ["HEALTH", "health"],
    ["id"],
    ["classname"],
    ["message", "MESSAGE"],
])
def test_duplicate_casefold_and_protected_rejected(setup, remove):
    env, folder, _ = setup
    doc = Document(write(folder, "map.vmap", VMAP), env)
    before = doc.text
    with pytest.raises(MapError):
        doc.set_entity("entity-id", {}, remove_properties=remove)
    assert doc.text == before


def test_array_node_and_nonlist_nonstr_rejected(setup):
    env, folder, _ = setup
    # structured attribute: message value replaced by Node via direct edit is complex;
    # use existing structured props node child instead: opaque_mesh is outside props,
    # so craft a props-level array fixture minimally
    text = VMAP.replace('"health" "string" "10"',
                        '"health" "string" "10" "tags" "string_array" [ "a" ]')
    doc = Document(write(folder, "map.vmap", text), env)
    before = doc.text
    with pytest.raises(MapError):
        doc.set_entity("entity-id", {}, remove_properties=["tags"])
    with pytest.raises(MapError):
        doc.set_entity("entity-id", {}, remove_properties="message")  # type: ignore[arg-type]
    with pytest.raises(MapError):
        doc.set_entity("entity-id", {}, remove_properties=["message", 7])  # type: ignore[list-item]
    with pytest.raises(MapError):
        doc.set_entity("entity-id", {"message": "x"}, remove_properties=["message"])
    assert doc.text == before


def test_dryrun_and_stale_hash_unchanged(setup):
    env, folder, store = setup
    path = write(folder, "map.vmap", VMAP)
    doc = Document(path, env)
    preview = doc.save(store, doc.original_hash, dry_run=True)
    assert preview["written"] is False
    doc.set_entity("entity-id", {"soundName": "x"}, remove_properties=["message"])
    with pytest.raises(MapError, match="hash mismatch"):
        doc.save(store, "0" * 64)
    assert path.read_text() == VMAP


def test_server_passes_remove_properties(tmp_path, monkeypatch):
    # server-level passthrough check via existing test style: call wrapper with a fake hammer
    import server as srv
    project = tmp_path / "project"
    project.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"state_dir": str(tmp_path / "state"), "project_roots": [str(project)]}))
    from hammer_mcp.service import Hammer
    monkeypatch.setattr(srv, "hammer", Hammer(env := __import__("hammer_mcp.environment", fromlist=["Environment"]).Environment(cfg, games=[])))
    path = project / "map.vmap"
    path.write_text(VMAP)
    info = srv.hammer.document(str(path)).summary()
    result = srv.hammer_set_entity(path=str(path), entity="entity-id",
                                   expected_hash=info["sha256"],
                                   properties={"classname": "snd_event_point"},
                                   remove_properties=["message", "health"])
    assert result["entity"]["properties"]["classname"] == "snd_event_point"
    assert "message" not in result["entity"]["properties"]
