"""New original-reference study, through the independent 3D MCP only."""
import argparse
import asyncio
import json
from pathlib import Path
import uuid
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

BOXES={
 "water":[52,538,1302,447],"quay":[215,390,1275,405],"inn":[575,53,601,498],
 "gate":[290,262,240,250],"tea":[1068,378,329,229],"tree-back":[504,34,273,294],
 "tree-pink":[374,184,243,240],"tree-right":[1101,205,245,244],
 "pier":[425,473,248,155],"boat-main":[515,600,412,215],"boat-small":[946,638,264,165],
 "garden-left":[236,391,177,178],"garden-front":[498,399,300,124],"garden-right":[1216,411,223,232],
}

def analysis(source):
    sx,sy=source['width']/1536,source['height']/1024
    return dict(source_sha256=source['sha256'],scene_box=[0.,0.,float(source['width']),float(source['height'])],
        regions=[dict(id=k,label=k,box=[v[0]*sx,v[1]*sy,v[2]*sx,v[3]*sy],confidence=.75,critical=True,
            evidence="Measured visible region on the newly supplied 1536x1024 Qingyadu reference, not the old phone screenshot.") for k,v in BOXES.items()],
        assumptions=["Single viewpoint; hidden surfaces and depth remain inferred.","Original includes interface chrome; it is not geometry."],change_reason="New user-selected reference, independent job and native image bytes.")

def plan(ids):
    nodes=[]
    def asset(identity,template,p,d,region=None,**kw):
        nodes.append(dict(id=identity,label=identity,kind="asset",asset_id=ids[template],position=p,dimensions=d,
            material_id="wood",region_ids=[region or identity],inferred_surfaces="Blender solid component; depth and hidden joinery inferred from the supplied view.",**kw))
    nodes.append(dict(id="water",label="水面",kind="water",position=[0,-.3,.72],dimensions=[15.1,.32,11.0],material_id="water",accent_material_id="foam",region_ids=["water"],inferred_surfaces="Water volume and small waves are procedural, not recovered simulation."))
    asset("quay","stone_quay",[0,.25,-.62],[14.35,1.35,7.8])
    asset("inn","riverside_inn",[.20,1.10,-1.65],[6.70,6.35,4.90])
    asset("gate","open_gate",[-5.15,1.34,-.20],[2.75,3.20,1.90])
    asset("tea","tea_stall",[6.00,1.35,.20],[3.70,3.10,2.50])
    asset("tree-back","broadleaf_tree",[-3.40,1.20,-3.40],[3.75,6.20,3.70])
    asset("tree-pink","blossom_tree",[-4.70,1.25,-1.80],[3.65,3.50,2.65])
    asset("tree-right","broadleaf_tree",[5.35,1.45,-2.90],[3.40,4.20,3.10])
    asset("pier","timber_pier",[-2.60,.08,2.90],[2.65,1.35,1.90])
    asset("boat-main","woven_boat",[1.00,.18,4.15],[6.20,1.95,2.00],movement=dict(bounds=[-.3,3.9,1.8,4.9],amplitude=.16,speed=.18))
    asset("boat-small","rowing_boat",[6.20,.18,3.25],[3.60,1.00,1.80],movement=dict(bounds=[5.6,2.9,6.6,3.8],amplitude=.07,speed=.14))
    for ident,p,d,reg in [
        ("garden-left",[-6.35,1.05,.2],[1.55,1.50,1.55],"garden-left"),
        ("garden-front",[-2.25,1.12,1.02],[1.70,1.05,.90],"garden-front"),
        ("garden-right",[7.20,1.30,1.60],[1.40,1.30,1.80],"garden-right"),
        ("garden-inn-right",[3.00,1.14,1.42],[1.05,1.05,.85],"garden-front"),
        ("garden-gate",[-3.95,1.12,.15],[1.00,1.35,1.05],"garden-left")]:asset(ident,"potted_garden",p,d,reg)
    for i,p in enumerate([[-2.05,1.0,2.83],[-2.75,1.0,3.2],[6.55,1.25,2.05]]):
        asset("crate-"+str(i),"cargo_crate",p,[.43,.45,.41],"pier" if i<2 else "garden-right")
    # Text is declared, editable scene content on real volumetric boards.
    for ident,txt,p,d,reg,bg,fg in [
        ("inn-sign","青崖渡",[.80,4.00,1.10],[1.55,.40,.05],"inn","#373325","#d7ae65"),
        ("inn-side-sign","客栈",[3.20,3.72,.72],[.44,.95,.055],"inn","#373325","#d7ae65"),
        ("gate-sign","渡口",[-5.13,3.68,.28],[1.00,.36,.06],"gate","#392f21","#d7b35c"),
        ("tea-canopy","茶",[6.00,4.02,1.32],[1.15,.46,.055],"tea","#a5ac8d","#494a34"),
        ("tea-banner","清茶一盏",[6.78,3.02,1.42],[.40,1.15,.055],"tea","#a5ac8d","#494a34")]:
        nodes.append(dict(id=ident,label=txt,kind="sign",text=txt,position=p,dimensions=d,material_id=bg[1:],accent_material_id=fg[1:],region_ids=[reg],inferred_surfaces="Readable declared reference sign on a solid board."))
    mats=[dict(id="wood",color="#795a39"),dict(id="water",color="#3c8b85",roughness=.23),dict(id="foam",color="#b4d6c1")]
    for c in ["#373325","#d7ae65","#392f21","#d7b35c","#a5ac8d","#494a34"]:mats.append(dict(id="m"+c[1:],color=c))
    for n in nodes:
        if n['kind']=='sign':n['material_id']='m'+n['material_id'];n['accent_material_id']='m'+n['accent_material_id']
    return dict(title="青崖渡",presentation="jiangnan",background="#f3ecdb",camera=dict(position=[11.5,10.7,18.5],target=[0,2.7,.45],vertical_span=13.3),
                materials=mats,objects=nodes,assumptions=["Detailed interactive reference study; single-image hidden surfaces are inferred.","Rendering and handwritten lettering differ from the original illustration; no completion certificate."])

async def run(args):
    token=Path('.canvaslab3d/access.token').read_text().strip()
    async with httpx2.AsyncClient(headers={"Authorization":"Bearer "+token},timeout=240) as http:
        async with Client(streamable_http_client("http://127.0.0.1:8031/mcp-3d",http_client=http),cache=None) as client:
            async def call(name,**kw):
                r=await client.call_tool(name,kw)
                if r.is_error:raise RuntimeError(str(r.content))
                return r.structured_content
            status=await call("scene_runtime_status")
            if status['restart_required']:raise RuntimeError("Restart current 3D service")
            ids={}
            for template in ["riverside_inn","stone_quay","tea_stall","broadleaf_tree","blossom_tree","woven_boat","rowing_boat","timber_pier","potted_garden","open_gate","cargo_crate"]:
                recipe=dict(template=template,detail=2)
                if template=="open_gate":recipe.update(detail=2,primary_color="#735538",secondary_color="#627665")
                a=await call("generate_scene_component",recipe=recipe,idempotency_key="river-study-"+uuid.uuid4().hex)
                ids[template]=a['asset_id']
                print(json.dumps(dict(template=template,triangles=a['geometry']['triangles'],reused=a['reused'])),flush=True)
            r=await http.post("http://127.0.0.1:8031/assets",content=args.image.read_bytes());r.raise_for_status();source=r.json()
            job=await call("submit_scene_reference",asset_id=source['asset_id'],requirements="Restore the new Qingyadu reference as a detailed true 3D webpage, keep 2D MCP untouched",idempotency_key=uuid.uuid4().hex)
            jid=job['job_id']
            await call("save_scene_analysis",job_id=jid,analysis=analysis(source),base_revision=0)
            v=await call("validate_scene_plan",job_id=jid,plan=plan(ids),analysis_revision=1,base_revision=0)
            b=await call("build_threejs_scene",job_id=jid,plan_id=v['plan_id'],idempotency_key="first")
            print(json.dumps(dict(job_id=jid,build_id=b['build_id'],directory=str(Path('.canvaslab3d/builds')/jid/b['build_id']))),flush=True)
            if args.capture:
                c=await call("capture_scene_views",job_id=jid,build_id=b['build_id'])
                print(json.dumps(dict(capture_id=c['capture_id'],tests=c['tests'])),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('image',type=Path);p.add_argument('--capture',action='store_true')
    asyncio.run(run(p.parse_args()))
