import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from server3d.jobs import Store, Conflict
from server3d.api import create_app
from server3d.builder import build_files, sha
from server3d.tests.test_glb_validation import fixture, pack
from server3d.scene_schema import ScenePlan, scene_budgets
from benchmarks3d.scenes import water_town


class ComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=Store(Path(self.temp.name)); self.lib=self.store.components
        self.payload=pack(*fixture())
        self.patches=[patch.object(self.lib,"engine_identity",return_value={"version":"Blender test","executable_sha256":"a"*64}),
                      patch("server3d.components.configured_blender",return_value=Path("test-blender.exe")),
                      patch("server3d.components.subprocess.run",side_effect=self.worker)]
        self.mocks=[p.start() for p in self.patches]
    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()
    def worker(self,args,**kwargs):
        self.assertFalse(kwargs["shell"])
        self.assertIn("--factory-startup",args); self.assertIn("--disable-autoexec",args)
        out=Path(args[-1]); out.mkdir()
        (out/"component.glb").write_bytes(self.payload)
        (out/"component.blend").write_bytes(b"TEST ONLY editable fixture")
        (out/"metadata.json").write_text(json.dumps({"natural_dimensions":[2.,1.,3.]}))
        return subprocess.CompletedProcess(args,0,"ok","")
    def create(self,template="cargo_crate",key="first"):
        return self.lib.generate({"template":template},key)
    def node(self,identity,**kw):
        return {"id":"box","kind":"asset","asset_id":identity,"movement":None,**kw}
    def test_idempotency_and_cross_request_cache(self):
        a=self.create(); b=self.create(); c=self.create(key="other-client")
        self.assertEqual(a["asset_id"],b["asset_id"]); self.assertTrue(b["reused"]);self.assertTrue(c["reused"])
        self.assertEqual(self.mocks[-1].call_count,1)
        self.assertEqual(a["natural_dimensions"],[2.,1.,3.])
    def test_conflicting_retry_and_unknown_recipe(self):
        self.create()
        with self.assertRaises(Conflict): self.create(template="railing")
        with self.assertRaises(ValueError): self.lib.generate({"template":"cargo_crate","python":"bad"},"other")
        self.assertEqual(self.mocks[-1].call_count,1)
    def test_atomic_publish_recovers_after_database_failure(self):
        original=self.store.put; fail=[True]
        def put(db,kind,identity,value):
            if kind=="component" and fail[0]:
                fail[0]=False; raise RuntimeError("simulated commit failure")
            return original(db,kind,identity,value)
        with patch.object(self.store,"put",side_effect=put):
            with self.assertRaises(RuntimeError): self.create()
        result=self.create()
        self.assertEqual(result["asset_id"],sha(self.payload))
        self.assertEqual(self.lib.get(result["asset_id"])["geometry"]["triangles"],12)
    def test_tampered_component_never_enters_build(self):
        a=self.create(); (self.lib.root/a["asset_id"]/"component.glb").write_bytes(b"changed")
        with self.assertRaises(ValueError): self.lib.get(a["asset_id"])
        with self.assertRaises(ValueError): self.create(key="retry")
    def test_worker_failure_releases_lease_and_does_not_publish(self):
        self.mocks[-1].side_effect=None;self.mocks[-1].return_value=subprocess.CompletedProcess([],1,"", "failed")
        with self.assertRaises(ValueError): self.create()
        self.assertEqual(self.lib.list(),[])
        with self.store.transaction() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM records WHERE kind='lease' AND id='blender'").fetchone())
    def test_worker_timeout_actionable(self):
        self.mocks[-1].side_effect=subprocess.TimeoutExpired("blender",180)
        with self.assertRaisesRegex(ValueError,"timed out"): self.create()
    def test_failed_work_quota_prevents_unlimited_growth(self):
        root=self.store.root/"component-work";root.mkdir()
        for i in range(200): (root/str(i)).mkdir()
        with self.assertRaisesRegex(ValueError,"work quota"): self.create()
        self.mocks[-1].assert_not_called()
    def test_unknown_asset_and_path_rejected(self):
        for identity in ["../../secret","a"*64]:
            with self.assertRaises(ValueError): self.lib.get(identity)
    def test_only_crate_can_move(self):
        a=self.create(template="open_gate")
        with self.store.transaction() as db:
            with self.assertRaises(ValueError): self.lib.build_assets({"objects":[self.node(a["asset_id"],movement={"bounds":[0,0,1,1]})]},db)
    def test_measured_instanced_budget_and_packaged_hashes(self):
        a=self.create()
        with self.store.transaction() as db:
            assets=self.lib.build_assets({"objects":[self.node(a["asset_id"]) for _ in range(128)]},db)
        with patch("server3d.builder.runtime_files",return_value={"index.html":b"fixture"}):
            _,files,manifest=build_files({}, {}, {}, "job","plan",assets)
        self.assertEqual(files[f"component-{a['asset_id']}.glb"],self.payload)
        self.assertEqual(manifest["component_ids"],[a["asset_id"]])
        self.assertEqual(manifest["files"][f"component-{a['asset_id']}.glb"],a["asset_id"])
        huge=copy.deepcopy(a);huge["geometry"]["triangles"]=100000
        with patch.object(self.lib,"get",return_value=huge), self.store.transaction() as db:
            with self.assertRaisesRegex(ValueError,"triangle budget"):
                self.lib.build_assets({"objects":[self.node(a["asset_id"]) for _ in range(3)]},db)
    def test_component_download_requires_auth(self):
        a=self.create();client=TestClient(create_app(self.store,"x"*40))
        self.assertEqual(client.get(a["downloads"]["glb"]).status_code,401)
        response=client.get(a["downloads"]["glb"],headers={"Authorization":"Bearer "+"x"*40})
        self.assertEqual(response.content,self.payload)

    def test_declared_sign_budget_does_not_consume_2500_triangles(self):
        a=self.create()
        big=copy.deepcopy(a);big["geometry"]["triangles"]=249_980
        with patch.object(self.lib,"get",return_value=big), self.store.transaction() as db:
            self.lib.build_assets({"objects":[self.node(a["asset_id"]),{"kind":"sign"}]},db)
            with self.assertRaisesRegex(ValueError,"triangle budget"):
                self.lib.build_assets({"objects":[self.node(a["asset_id"]),{"kind":"sign"},{"kind":"sign"}]},db)
    def test_asset_schema_identity_rules(self):
        p=water_town();node=p["objects"][0]
        node["kind"]="asset"
        with self.assertRaises(ValueError): ScenePlan.model_validate(p)
        node["asset_id"]="a"*64; ScenePlan.model_validate(p)
        node["kind"]="water"
        with self.assertRaises(ValueError): ScenePlan.model_validate(p)

    def test_showcase_component_generation_and_orphan_recovery_use_strict_profile(self):
        from server3d.glb_validation import validate_glb
        original = self.store.put
        fail = [True]
        def put(db, kind, identity, value):
            if kind == "component" and fail[0]:
                fail[0] = False
                raise RuntimeError("simulated commit failure")
            return original(db, kind, identity, value)
        with patch("server3d.components.validate_glb", wraps=validate_glb) as validator:
            with patch.object(self.store, "put", side_effect=put), self.assertRaises(RuntimeError):
                self.lib.generate({"template": "cargo_crate", "detail": 3}, "showcase")
            record = self.lib.generate({"template": "cargo_crate", "detail": 3}, "showcase")
            self.assertEqual(record["geometry"]["budget_profile"], "showcase")
            self.assertEqual(validator.call_count, 3)
            self.assertTrue(all(call.kwargs == {"profile": "showcase"} for call in validator.call_args_list))

    def test_showcase_scene_budget_counts_every_instance_and_sign(self):
        a = self.create()
        measured = copy.deepcopy(a)
        measured["geometry"]["triangles"] = 300_000
        with patch.object(self.lib, "get", return_value=measured), self.store.transaction() as db:
            nodes = [self.node(a["asset_id"]) for _ in range(5)]
            self.lib.build_assets({"render_quality": "showcase", "objects": nodes}, db)
            with self.assertRaisesRegex(ValueError, "triangle budget"):
                self.lib.build_assets({"render_quality": "showcase", "objects": nodes + [{"kind": "sign"}]}, db)
            with self.assertRaisesRegex(ValueError, "triangle budget"):
                self.lib.build_assets({"objects": nodes[:1]}, db)

    def test_profile_download_budget_counts_unique_assets(self):
        a = self.create()
        d, b = fixture()
        d["materials"][0]["pbrMetallicRoughness"]["roughnessFactor"] = .9
        self.payload = pack(d, b)
        second = self.lib.generate({"template": "railing"}, "second")
        records = {r["asset_id"]: copy.deepcopy(r) for r in (a, second)}
        for r in records.values():
            r["bytes"] = 40 * 1024 * 1024
        nodes = [self.node(identity) for identity in records]
        with patch.object(self.lib, "get", side_effect=lambda identity, db: records[identity]), self.store.transaction() as db:
            self.lib.build_assets({"objects": [nodes[0], nodes[0]]}, db)
            with self.assertRaisesRegex(ValueError, "download budget"):
                self.lib.build_assets({"objects": nodes}, db)
            self.lib.build_assets({"render_quality": "showcase", "objects": nodes}, db)
            for r in records.values():
                r["bytes"] = 65 * 1024 * 1024
            with self.assertRaisesRegex(ValueError, "download budget"):
                self.lib.build_assets({"render_quality": "showcase", "objects": nodes}, db)

    def test_scene_quality_is_explicit_bounded_and_preserves_standard_default(self):
        plan = water_town()
        self.assertEqual(ScenePlan.model_validate(plan).render_quality, "standard")
        self.assertEqual(scene_budgets(), (250_000, 64 * 1024 * 1024))
        for value in (True, None, 3, "ultra", "SHOWCASE"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ScenePlan.model_validate({**plan, "render_quality": value})
            with self.assertRaises(ValueError):
                scene_budgets(value)
        prototype = plan["objects"][2]
        plan["objects"] = [{**copy.deepcopy(prototype), "id": f"inn-{i}"} for i in range(32)]
        with self.assertRaisesRegex(ValueError, "estimated triangle budget"):
            ScenePlan.model_validate(plan)
        ScenePlan.model_validate({**plan, "render_quality": "showcase"})
