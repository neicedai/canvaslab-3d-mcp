import copy
import unittest
from server3d.camera_fit import project_box, suggest_camera
from server3d.scene_schema import Camera


class CameraFitTests(unittest.TestCase):
    def setUp(self):
        self.camera=Camera(position=[10,10,15],target=[0,1,0],vertical_span=15).model_dump()
        self.worlds={"a":[[-5,0,-2],[-3,4,0]],"b":[[2,0,0],[5,2,3]],"c":[[-1,0,3],[0,1,5]],"d":[[1,0,-4],[3,6,-2]]}
        self.plan={"camera":self.camera,"objects":[{"id":i,"region_ids":[i],"dimensions":[1,1,1]} for i in self.worlds]}
        true=copy.deepcopy(self.camera); true["vertical_span"]=13; true["target"]=[.5,1.2,0]; true["position"]=[11,11,15]
        self.analysis={"scene_box":[20,30,1200,900],"regions":[]}
        self.observation={"native_size":[1200,900],"reference":{"objects":{}}}
        for identity,world in self.worlds.items():
            x,y,w,h=project_box(true,world,[1200,900])
            self.analysis["regions"].append({"id":identity,"confidence":.9,"box":[x+20,y+30,w,h]})
            self.observation["reference"]["objects"][identity]={"world_box":world,"screen_box":project_box(self.camera,world,[1200,900])}
    def test_fit_improves_and_never_changes_geometry(self):
        before=copy.deepcopy(self.plan)
        result=suggest_camera(self.plan,self.analysis,self.observation)
        self.assertTrue(result["improved"])
        self.assertLess(result["predicted_after_loss"],result["before_loss"]*.05)
        self.assertEqual(self.plan,before)
        self.assertEqual(result["proposed_plan"]["objects"],before["objects"])
        self.assertFalse(result["applied"])
        Camera.model_validate(result["proposed_plan"]["camera"])
    def test_perfect_fit_not_perturbed(self):
        for r in self.analysis["regions"]:
            x,y,w,h=self.observation["reference"]["objects"][r["id"]]["screen_box"]
            r["box"]=[x+20,y+30,w,h]
        result=suggest_camera(self.plan,self.analysis,self.observation)
        self.assertFalse(result["improved"])
        self.assertEqual(result["proposed_plan"],self.plan)
    def test_wrong_camera_capture_rejected(self):
        self.observation["reference"]["objects"]["a"]["screen_box"][0]+=10
        with self.assertRaises(ValueError): suggest_camera(self.plan,self.analysis,self.observation)
    def test_insufficient_regions_rejected(self):
        for r in self.analysis["regions"]: r["confidence"]=.1
        with self.assertRaises(ValueError): suggest_camera(self.plan,self.analysis,self.observation)
    def test_perspective_explicitly_unsupported(self):
        self.plan["camera"]["projection"]="perspective"
        with self.assertRaises(ValueError): suggest_camera(self.plan,self.analysis,self.observation)
    def test_clustered_targets_rejected(self):
        for r in self.analysis["regions"]: r["box"]=[500,500,50,50]
        with self.assertRaises(ValueError): suggest_camera(self.plan,self.analysis,self.observation)
