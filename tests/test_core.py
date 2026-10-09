from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hammer_mcp.environment import Environment
from hammer_mcp.keyvalues import MapError, Parser, splice
from hammer_mcp.maps import Document, create_room
from hammer_mcp.service import Hammer
from hammer_mcp.storage import Store, sha256
from hammer_mcp.compiler import diagnose_log


VMF = '''// A comment that must survive edits.
versioninfo { "editorversion" "400" }
world { "id" "1" "classname" "worldspawn" }
entity {
 "id" "2" "classname" "logic_relay" "targetname" "relay"
 "origin" "0 0 0"
 connections { "OnTrigger" "door,Open,,0,-1" "OnTrigger" "door,Close,,1,-1" }
}
entity { "id" "3" "classname" "func_door" "targetname" "door" }
'''

VMAP = '''<!-- dmx encoding keyvalues2 4 format vmap 40 -->
"$prefix_element$" { "id" "elementid" "prefix" "asset_preview_thumbnail" "binary" "ABCD" }
"CMapRootElement" {
 "id" "elementid" "root"
 "world" "CMapWorld" {
  "id" "elementid" "world" "nodeID" "int" "1"
  "children" "element_array" [ "element" "entity-id" ]
 }
}
"CMapEntity" {
 "id" "elementid" "entity-id" "nodeID" "int" "2"
 "referenceID" "uint64" "0xfedcba9876543210"
 "children" "element_array" []
 "entity_properties" "EditGameClassProps" {
  "id" "elementid" "properties-id"
  "classname" "string" "logic_relay" "targetname" "string" "relay"
 }
 "origin" "vector3" "0 0 0" "angles" "qangle" "0 0 0" "scales" "vector3" "1 1 1"
 "connectionsData" "element_array" []
 "opaque_mesh" "CDmePolygonMesh" {
  "id" "elementid" "mesh-id" "positions" "vector3_array" ["1 2 3", "4 5 6"]
  "unknown_future_type" "uint64" "0xffffffffffffffff"
 }
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


def test_vmf_splices_only_requested_value(setup):
    env, folder, store = setup
    path = folder / "map.vmf"
    path.write_bytes(VMF.encode())
    doc = Document(path, env)
    doc.set_entity("relay", {"targetname": "renamed"})
    assert doc.text == VMF.replace('"targetname" "relay"', '"targetname" "renamed"')
    result = doc.save(store, doc.original_hash)
    assert result["changed"]
    assert len(Document(path, env).connections(Document(path, env).find_entity("renamed"))) == 2
    restored = store.restore(path, result["backup_id"], result["sha256"])
    assert restored["sha256"] == sha256(VMF.encode())
    assert path.read_bytes() == VMF.encode()


def test_vmap_preserves_prefix_refs_and_unknown_mesh_data(setup):
    env, folder, store = setup
    path = folder / "map.vmap"
    path.write_bytes(VMAP.encode())
    doc = Document(path, env)
    assert len(doc.entity_nodes()) == 1
    doc.set_entity("relay", {"targetname": "한국어"}, origin=[1, 2, 3])
    assert '"0xfedcba9876543210"' in doc.text
    assert VMAP[VMAP.index(' "opaque_mesh"'):] == doc.text[doc.text.index(' "opaque_mesh"'):]
    assert '"asset_preview_thumbnail" "binary" "ABCD"' in doc.text
    doc.save(store, doc.original_hash)
    assert Document(path, env).record(Document(path, env).find_entity("entity-id"))["targetname"] == "한국어"


@pytest.mark.parametrize("source,extension", [(VMF, ".vmf"), (VMAP, ".vmap")])
def test_add_entity_and_connection(source, extension, setup):
    env, folder, store = setup
    path = folder / ("map" + extension)
    path.write_text(source)
    doc = Document(path, env)
    before = len(doc.entity_nodes())
    result = doc.add_entity("logic_relay", {"targetname": "new_relay"}, [32, 64, 128])
    doc.add_connection(result["id"], "OnTrigger", "relay", "Trigger", "", 0.5, 1)
    doc.save(store, doc.original_hash)
    read = Document(path, env)
    assert len(read.entity_nodes()) == before + 1
    entity = read.record(read.find_entity("new_relay"))
    assert entity["origin"] == "32 64 128"
    assert entity["connections"][0]["target"] == "relay"
    assert entity["connections"][0]["delay"] == "0.5"


def test_hash_conflict_does_not_write(setup):
    env, folder, store = setup
    path = folder / "map.vmf"
    path.write_text(VMF)
    doc = Document(path, env)
    doc.set_entity("relay", {"targetname": "changed"})
    path.write_text(VMF + "// External save\n")
    with pytest.raises(MapError, match="changed on disk"):
        doc.save(store, doc.original_hash)
    assert path.read_text().endswith("// External save\n")


def test_bad_hash_and_preview_do_not_write(setup):
    env, folder, store = setup
    path = folder / "map.vmf"
    path.write_text(VMF)
    doc = Document(path, env)
    doc.set_entity("relay", {"targetname": "changed"})
    with pytest.raises(MapError, match="hash mismatch"):
        doc.save(store, "0" * 64)
    preview = doc.save(store, doc.original_hash, dry_run=True)
    assert preview["written"] is False
    assert path.read_text() == VMF


def test_project_boundary_and_backup_binding(setup):
    env, folder, store = setup
    with pytest.raises(MapError, match="outside"):
        env.resolve_map(str(folder / "../outside.vmf"), write=True, exists=False)
    with pytest.raises(MapError, match="Expected"):
        env.resolve_map(str(folder / "secret.txt"), write=True, exists=False)
    a, b = folder / "a.vmf", folder / "b.vmf"
    a.write_bytes(b"one")
    b.write_bytes(b"one")
    result = store.commit(a, b"one", b"two", sha256(b"one"))
    with pytest.raises(MapError, match="another map"):
        store.restore(b, result["backup_id"], sha256(b"one"))
    assert b.read_bytes() == b"one"


def test_invalid_syntax_and_overlapping_edits():
    for text in ['world { "id" "1"', 'world { "id" "unterminated }']:
        with pytest.raises(MapError):
            Parser(text, False).parse()
    with pytest.raises(MapError, match="Overlapping"):
        splice("abcdef", [(1, 4, "x"), (3, 5, "y")])


def test_duplicate_entity_selector_is_rejected(setup):
    env, folder, _ = setup
    path = folder / "map.vmf"
    path.write_text(VMF.replace('"targetname" "door"', '"targetname" "relay"'))
    with pytest.raises(MapError, match="matched 2"):
        Document(path, env).set_entity("relay", {"spawnflags": "1"})


def test_diagnostics_and_reference_validation(setup):
    env, folder, _ = setup
    assert diagnose_log("0 errors\nErrors: 0\nWARNING: missing texture")["error_count"] == 0
    result = diagnose_log("**** leaked ****\nError opening test.prt")
    assert result["first_error"]["code"] == "leak"
    path = folder / "map.vmf"
    path.write_text(VMF.replace("door,Open", "missing,Open"))
    result = Hammer(env).validate(str(path), None)
    assert result["error_count"] == 0
    assert result["findings"][0]["target"] == "missing"


def test_generated_room_is_readable_by_independent_vmf_parser(setup):
    from srctools import VMF as NativeVMF
    from srctools import Keyvalues
    env, folder, _ = setup
    data = create_room(512, 512, 256)
    path = folder / "room.vmf"
    path.write_bytes(data)
    parsed = NativeVMF.parse(Keyvalues.parse(data.decode()))
    assert len(parsed.brushes) == 6
    assert len(Document(path, env).entity_nodes()) == 4
    hammer = Hammer(env)
    try:
        assert hammer.validate(str(path), None)["error_count"] == 0
    finally:
        hammer.jobs.shutdown()


def test_hidden_vmf_entities_can_be_read_and_edited(setup):
    from srctools import VMF as NativeVMF, Keyvalues
    env, folder, store = setup
    vmf = NativeVMF()
    vmf.create_ent("logic_relay", targetname="visible")
    hidden = vmf.create_ent("logic_relay", targetname="hidden")
    hidden.hidden = True
    path = folder / "hidden.vmf"
    path.write_text(vmf.export(), encoding="utf-8")
    doc = Document(path, env)
    records = {doc.record(n)["targetname"]: doc.record(n) for n in doc.entity_nodes()}
    assert records["visible"]["hidden"] is False
    assert records["hidden"]["hidden"] is True
    assert doc.summary()["hidden_entities"] == 1
    doc.set_entity(str(hidden.id), {"targetname": "edited"})
    doc.add_connection(str(hidden.id), "OnTrigger", "visible", "Trigger", "", 0, -1)
    doc.save(store, doc.original_hash)
    independent = NativeVMF.parse(Keyvalues.parse(path.read_text()))
    edited = next(e for e in independent.entities if e["targetname"] == "edited")
    assert edited.hidden
    assert len(edited.outputs) == 1


def test_vmf_duplicate_ids_are_checked_within_their_namespace(setup):
    env, folder, _ = setup
    path = folder / "ids.vmf"
    path.write_text('''world { "id" "1" "classname" "worldspawn"
        solid { "id" "1" side { "id" "1" } side { "id" "1" } }
    } entity { "id" "1" "classname" "logic_relay" }''')
    hammer = Hammer(env)
    try:
        findings = hammer.validate(str(path), None)["findings"]
        assert {(f["namespace"], f["id"], f["count"]) for f in findings} == {
            ("entity", "1", 2), ("side", "1", 2)}
    finally:
        hammer.jobs.shutdown()


def test_vmap_element_ids_still_share_one_namespace(setup):
    env, folder, _ = setup
    path = folder / "ids.vmap"
    path.write_text(VMAP.replace('"mesh-id"', '"world"'))
    hammer = Hammer(env)
    try:
        findings = hammer.validate(str(path), None)["findings"]
        assert any(f.get("namespace") == "dmx_element" and f["id"] == "world" for f in findings)
    finally:
        hammer.jobs.shutdown()


@pytest.mark.parametrize("name", ["fatal", "error", "leaked", "warning"])
def test_diagnostic_words_in_filenames_are_not_messages(name):
    result = diagnose_log(f"Loading C:/maps/{name}.vmf\nWriting C:/maps/{name}.bsp\n0 errors")
    assert result["error_count"] == result["warning_count"] == 0


def test_prefixed_real_diagnostics_are_preserved():
    result = diagnose_log("[ResourceCompiler] FATAL ERROR: invalid data\n12:34:56.789 [Map] Error: missing\nEntity light (0 0 0) leaked!")
    assert result["error_count"] == 3
