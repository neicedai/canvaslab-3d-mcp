from __future__ import annotations

import argparse, base64, copy, json, os, re, shutil, subprocess, tempfile, uuid
from pathlib import Path
from PIL import Image, ImageDraw

from server3d.jobs import Store
from server3d.builder import REPO


def region(identity, label, box, polygon=None, *, critical=False, confidence=.65):
    data = {
        'id': identity, 'label': label, 'box': box, 'critical': critical,
        'confidence': confidence,
        'evidence': 'Manual measurement on the generated reference used for this reconstruction benchmark.',
        'visible_polygons': [polygon] if polygon else [], 'visible_holes': [],
    }
    return data


def build_analysis(source):
    return {
        'source_sha256': source['sha256'], 'scene_box': [0, 0, 512, 384],
        'regions': [
            region('inn', '主建筑', [0, 0, 336, 282],
                   [[0,0],[220,0],[258,28],[318,68],[334,126],[332,212],[306,269],[0,269]],
                   critical=True, confidence=.93),
            region('boat', '乌篷船', [244, 225, 142, 83],
                   [[255,244],[278,236],[349,235],[378,251],[371,286],[322,303],[261,291]],
                   critical=True, confidence=.91),
            region('bridge', '石拱桥', [326, 154, 186, 126],
                   [[334,218],[356,195],[409,173],[465,171],[511,188],[511,237],[482,239],[456,213],[414,205],[381,232],[351,248]],
                   critical=True, confidence=.90),
            region('quay', '石岸与台阶', [0, 198, 344, 132],
                   [[0,207],[301,209],[340,253],[321,311],[0,329]], confidence=.78),
            region('water', '河道水面', [128, 218, 384, 166],
                   [[128,264],[512,230],[512,384],[128,384]], confidence=.75),
            region('trees', '树木与柳枝', [0, 0, 512, 260], confidence=.60),
            region('background', '远景建筑与山体', [280, 72, 232, 190], confidence=.55),
        ],
        'assumptions': [
            'Single-image reconstruction: hidden faces and depth are inferred.',
            'The stone bridge uses the closest managed component in this first benchmark pass; arch topology remains approximate.',
            'Vegetation is approximate geometry; source projection is limited to selected measured assets.'
        ],
        'change_reason': 'First real reconstruction pass of the generated Jiangnan architectural landscape reference.'
    }


def material(identity, color, roughness=.8, metalness=0.0):
    return {'id':identity,'color':color,'roughness':roughness,'metalness':metalness}


def asset_node(identity, label, asset_id, region_id, position, dimensions, material_id, rotation=None):
    return {
        'id':identity,'label':label,'kind':'asset','asset_id':asset_id,'parent_id':None,
        'region_ids':[region_id], 'position':position,'dimensions':dimensions,
        'rotation':rotation or [0.0,0.0,0.0,1.0], 'material_id':material_id,
        'accent_material_id':None,'floors':1,'roof_enabled':True,'canopy':True,
        'movement':None,'inferred_surfaces':'Managed Blender component; hidden geometry inferred from one source view.'
    }


def primitive_node(identity, label, kind, regions, position, dimensions, material_id, accent=None, **extra):
    node = {
        'id':identity,'label':label,'kind':kind,'asset_id':None,'parent_id':None,
        'region_ids':regions,'position':position,'dimensions':dimensions,
        'rotation':[0.0,0.0,0.0,1.0], 'material_id':material_id,
        'accent_material_id':accent,'floors':extra.pop('floors',1),'roof_enabled':extra.pop('roof_enabled',True),
        'canopy':extra.pop('canopy',True),'movement':extra.pop('movement',None),
        'inferred_surfaces':'Procedural support geometry inferred from the single reference image.'
    }
    node.update(extra)
    return node


def build_plan(assets):
    return {
        'contract_version':'canvaslab-scene-v1','title':'生成江南水乡 · 3D还原测试',
        'presentation':'jiangnan','render_quality':'standard','appearance_mode':'reference',
        'reference_lighting': {
            'sky_color':'#ffffff','ground_color':'#8f9a83','hemisphere_intensity':1.15,
            'sun_color':'#fff4df','sun_intensity':1.8,'sun_position':[-8.0,14.0,8.0],
            'sun_target':[0.0,1.0,0.0],'exposure':1.0,'tone_mapping':'none'
        },
        'reference_projection': {'mode':'source_appearance','object_ids':['inn','boat','bridge']},
        'coordinate_system':'right-handed-y-up-relative','mode':'true_3d','seed':1977,
        'background':'#b9d6ef',
        'camera': {'projection':'orthographic','position':[10.4,12.5,18.0],
                   'target':[-0.1,1.55,0.8],'vertical_span':12.4,'fov':40.0},
        'view_angle_degrees':55.0,
        'materials': [
            material('plaster','#e8e2d1',.92), material('wood','#64462f',.78),
            material('stone','#aaa893',.95), material('water','#5f9897',.25),
            material('foam','#c8ded3',.75), material('leaf','#718e55',.88),
            material('roof','#4b4c44',.82), material('distant','#d7d5c5',.9),
        ],
        'objects': [
            primitive_node('water','河道','water',['water'],[0,-.32,1.5],[16,.32,11.5],'water','foam'),
            asset_node('quay','石岸',assets['stone_quay'],'quay',[-2.4,.0,-.75],[9.2,1.55,5.0],'stone',
                       [0.0,0.0523359562,0.0,0.9986295348]),
            asset_node('inn','主建筑',assets['riverside_inn'],'inn',[-2.3,.85,-2.1],[7.2,5.45,4.7],'wood',
                       [0.0,0.0784590957,0.0,0.9969173337]),
            asset_node('boat','乌篷船',assets['woven_boat'],'boat',[1.35,.02,3.85],[3.55,1.35,1.55],'wood',
                       [0.0,-0.069756474,0.0,0.99756405]),
            asset_node('bridge','石拱桥近似体',assets['open_gate'],'bridge',[4.85,.45,.25],[4.65,3.0,1.55],'stone'),
            asset_node('tree-left','左侧大树',assets['broadleaf_tree'],'trees',[-5.1,.7,-3.0],[3.6,5.5,3.4],'leaf'),
            asset_node('tree-right','桥边树',assets['broadleaf_tree'],'trees',[5.3,.65,-2.2],[3.2,4.8,3.0],'leaf'),
            primitive_node('background-house','远景民居','building',['background'],[5.0,.8,-3.4],[4.7,3.4,3.2],'plaster','roof',floors=2),
            primitive_node('left-rail','石栏与台阶','dock',['quay'],[-2.0,.35,.45],[7.0,.7,1.5],'stone'),
        ],
        'assumptions': [
            'This is a first-pass true-3D reconstruction of a generated single image, not a depth-ground-truth recovery.',
            'Source appearance projection is used only on the measured inn, boat and bridge proxy; hidden faces retain authored materials.',
            'Bridge arch geometry is approximate in this pass and should be replaced by a dedicated stone-arch component in a later iteration.'
        ]
    }


def decode_source(repo: Path, output: Path):
    raw=(repo/'benchmarks3d/generated-jiangnan-reference.jpg.b64').read_text(encoding='ascii')
    chunks=re.findall(r'[A-Za-z0-9+/=]{64,}', raw)
    if not chunks:
        raise RuntimeError('generated source fixture contains no base64 payload')
    encoded=''.join(chunks)
    payload=base64.b64decode(encoded,validate=True)
    target=output/'source-reference.jpg'; target.write_bytes(payload)
    with Image.open(target) as im:
        if im.size != (512,384): raise RuntimeError(f'unexpected source size {im.size}')
        im.verify()
    return payload,target


def montage(source_path, rendered_path, output_path):
    source=Image.open(source_path).convert('RGB')
    rendered=Image.open(rendered_path).convert('RGB').resize(source.size,Image.Resampling.LANCZOS)
    pad=28
    canvas=Image.new('RGB',(source.width*2,source.height+pad),(245,245,245))
    canvas.paste(source,(0,pad)); canvas.paste(rendered,(source.width,pad))
    draw=ImageDraw.Draw(canvas); draw.text((8,7),'SOURCE',fill=(0,0,0)); draw.text((source.width+8,7),'3D RECONSTRUCTION',fill=(0,0,0))
    canvas.save(output_path)


def run(output: Path):
    repo=Path(__file__).resolve().parents[1]
    output.mkdir(parents=True,exist_ok=False)
    payload,source_path=decode_source(repo,output)
    store=Store(output/'store')
    source=store.upload(payload)
    job=store.submit(source['asset_id'],'Reconstruct the generated Jiangnan architectural landscape as true 3D with source-faithful visible appearance.',uuid.uuid4().hex)
    analysis=build_analysis(source)
    store.save_analysis(job['job_id'],analysis,0)

    recipes={
        'riverside_inn': {'template':'riverside_inn','detail':2,'primary_color':'#69482f','secondary_color':'#4a4b44'},
        'stone_quay': {'template':'stone_quay','detail':1,'primary_color':'#aaa893','secondary_color':'#8e8c7d'},
        'woven_boat': {'template':'woven_boat','detail':2,'primary_color':'#6d4a31','secondary_color':'#9b845d'},
        'open_gate': {'template':'open_gate','detail':2,'primary_color':'#aaa893','secondary_color':'#77766c'},
        'broadleaf_tree': {'template':'broadleaf_tree','detail':1,'primary_color':'#6b5237','secondary_color':'#6f8f54'},
    }
    assets={}
    for name,recipe in recipes.items():
        record=store.components.generate(recipe,'generated-reference:'+name)
        assets[name]=record['asset_id']
        print(json.dumps({'component':name,'asset_id':record['asset_id'],'triangles':record['geometry']['triangles']}),flush=True)

    plan=build_plan(assets)
    validated=store.validate(job['job_id'],plan,1,0)
    build=store.build(job['job_id'],validated['plan_id'],'generated-reference-build')
    build_dir=store.build_path(job['job_id'],build['build_id'])
    capture_dir=output/'capture'; capture_dir.mkdir()
    env=os.environ.copy(); env.setdefault('CANVASLAB3D_NODE','node')
    result=subprocess.run([env['CANVASLAB3D_NODE'],str(repo/'scripts3d/capture.mjs'),str(build_dir),str(capture_dir)],
                          capture_output=True,text=True,timeout=600,env=env,encoding='utf-8',errors='replace')
    (output/'capture.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
    if result.returncode:
        raise RuntimeError('capture failed: '+result.stderr[-2000:])
    observation=json.loads((capture_dir/'observation.json').read_text(encoding='utf-8'))
    failed=[x for x in observation.get('tests',[]) if not x.get('passed')]
    shutil.copy2(build_dir/'project.zip',output/'generated-jiangnan-3d-web.zip')
    (output/'scene-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'analysis.json').write_text(json.dumps(analysis,ensure_ascii=False,indent=2),encoding='utf-8')
    montage(source_path,capture_dir/'reference.png',output/'source-vs-3d.png')
    summary={
        'job_id':job['job_id'],'build_id':build['build_id'],'source_sha256':source['sha256'],
        'source_size':[source['width'],source['height']],'assets':assets,
        'browser_version':observation.get('browser_version'),'checks_total':len(observation.get('tests',[])),
        'checks_passed':sum(1 for x in observation.get('tests',[]) if x.get('passed')),
        'failed_checks':[x.get('name') for x in failed], 'console_errors':observation.get('errors',[]),
        'limitations':['single-image hidden geometry inferred','bridge uses open_gate proxy in first pass','vegetation remains approximate'],
    }
    (output/'reconstruction-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('output',type=Path); run(p.parse_args().output.resolve())
