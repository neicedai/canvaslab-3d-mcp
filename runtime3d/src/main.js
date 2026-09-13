import * as T from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {createObject} from './primitives.js';
import {loadComponentTemplates,instantiateComponent,componentInstanceInfo} from './assets.js';
import {enhanceRiverWater} from './river-water.js';
import {enhanceShowcaseMaterials,createShowcaseEnvironment} from './showcase-materials.js';
import {createShowcaseComposite} from './showcase-composite.js';
import {resolveAppearance,referenceLightState} from './reference-appearance.js';
import {createSurfaceProjection} from './source-projection.js';

async function start(){
  const [plan,manifest,analysis]=await Promise.all(['scene.json','build-manifest.json','reference-annotations.json'].map(async n=>{const r=await fetch(`./${n}`);if(!r.ok)throw new Error(`Missing ${n}`);return r.json();}));
  document.title=`${plan.title} · CanvasLab 3D`;document.querySelector('#title').textContent=plan.title;
  const river=plan.presentation==='jiangnan',showcase=plan.render_quality==='showcase';document.body.dataset.presentation=plan.presentation||'studio';
  const appearance=resolveAppearance(plan,analysis),{reference,stylized}=appearance;document.body.dataset.appearance=appearance.mode;
  const canvas=document.querySelector('canvas');
  const renderer=new T.WebGLRenderer({canvas,antialias:true,alpha:false,preserveDrawingBuffer:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,showcase?2:1.5));renderer.shadowMap.enabled=true;renderer.shadowMap.type=T.PCFShadowMap;
  renderer.outputColorSpace=T.SRGBColorSpace;renderer.toneMapping=T.ACESFilmicToneMapping;
  const scene=new T.Scene();scene.background=new T.Color(plan.background);
  const hemi=new T.HemisphereLight(0xf5edda,0x6b766b,2.3);scene.add(hemi);
  const sun=new T.DirectionalLight(0xffebc5,3.1);sun.position.set(-8,14,8);sun.castShadow=true;
  sun.shadow.mapSize.set(2048,2048);Object.assign(sun.shadow.camera,{left:-18,right:18,top:18,bottom:-18,near:1,far:60});sun.shadow.bias=-.0003;scene.add(sun);
  if(stylized){
    const environment=createShowcaseEnvironment(renderer);scene.environment=environment.texture;scene.environmentIntensity=.40;
    hemi.color.set('#e4eced');hemi.groundColor.set('#806c49');hemi.intensity=.90;
    sun.intensity=3.3;sun.color.set('#fff0d3');sun.shadow.mapSize.set(Math.min(4096,renderer.capabilities.maxTextureSize),Math.min(4096,renderer.capabilities.maxTextureSize));
    sun.shadow.radius=8.0;sun.shadow.normalBias=.012;sun.shadow.bias=-.00012;
    renderer.toneMappingExposure=1.02;
  }
  if(reference){
    const cfg=appearance.lighting;
    hemi.color.set(cfg.sky_color);hemi.groundColor.set(cfg.ground_color);hemi.intensity=cfg.hemisphere_intensity;
    sun.color.set(cfg.sun_color);sun.intensity=cfg.sun_intensity;
    sun.position.fromArray(cfg.sun_position);sun.target.position.fromArray(cfg.sun_target);scene.add(sun.target);
    renderer.toneMapping=cfg.tone_mapping==='none'?T.NoToneMapping:T.ACESFilmicToneMapping;
    renderer.toneMappingExposure=cfg.exposure;
    // Sampling may improve; authored colors, light direction and geometry may not change.
    const shadowSize=Math.min(showcase?4096:2048,renderer.capabilities.maxTextureSize);
    sun.shadow.mapSize.set(shadowSize,shadowSize);
  }
  const materials=new Map(plan.materials.map(x=>[x.id,new T.MeshStandardMaterial({color:x.color,roughness:x.roughness,metalness:x.metalness,side:T.DoubleSide})]));
  const components=await loadComponentTemplates(plan.objects,manifest);
  const nodes=new Map(plan.objects.map(x=>[x.id,x.kind==='asset'?instantiateComponent(components.get(x.asset_id),x):createObject(x,materials,plan.seed)]));
  for(const spec of plan.objects)(spec.parent_id?nodes.get(spec.parent_id):scene).add(nodes.get(spec.id));
  let reflectionAvailable=true;
  if(river&&!reference)for(const spec of plan.objects)if(spec.kind==='water'){
    enhanceRiverWater(nodes.get(spec.id),plan.seed,{showcase,reflection:showcase&&reflectionAvailable});reflectionAvailable=false;
  }
  const craft=stylized?enhanceShowcaseMaterials(nodes):null;
  let helperTriangles=0;
  if(stylized){
    const bounds=new T.Box3().setFromObject(scene),center=bounds.getCenter(new T.Vector3()),span=bounds.getSize(new T.Vector3()).length()*.56;
    sun.target.position.copy(center);scene.add(sun.target);sun.position.copy(center).add(new T.Vector3(-10,18,10));
    Object.assign(sun.shadow.camera,{left:-span,right:span,top:span,bottom:-span,near:.2,far:80});sun.shadow.camera.updateProjectionMatrix();
    const size=bounds.getSize(new T.Vector3());
    const ground=new T.Mesh(new T.PlaneGeometry(Math.max(22,size.x*2.2),Math.max(22,size.z*2.2)),new T.ShadowMaterial({opacity:.15,color:'#76654e',depthWrite:false}));
    ground.name='showcase-shadow-catcher';ground.rotation.x=-Math.PI/2;ground.position.set(center.x,bounds.min.y-.016,center.z);
    ground.receiveShadow=true;ground.castShadow=false;ground.userData.helperGeometry=true;scene.add(ground);helperTriangles=2;
  }
  const ortho=new T.OrthographicCamera(-10,10,10,-10,.1,500),perspective=new T.PerspectiveCamera(plan.camera.fov,1,.1,500);
  let camera=plan.camera.projection==='orthographic'?ortho:perspective;
  const composite=stylized?createShowcaseComposite(renderer):null;let renderDirty=true;
  if(showcase){renderer.info.autoReset=false;renderer.shadowMap.autoUpdate=false;renderer.shadowMap.needsUpdate=true;}
  function renderScene(){renderDirty=false;if(showcase)renderer.info.reset();if(composite)composite.render(scene,camera);else renderer.render(scene,camera);}
  const target=new T.Vector3().fromArray(plan.camera.target),homeCamera=new T.Vector3().fromArray(plan.camera.position);
  const controls=new OrbitControls(camera,canvas);controls.enableDamping=true;controls.enablePan=false;controls.minPolarAngle=.15;controls.maxPolarAngle=Math.PI/2-.06;controls.minZoom=.6;controls.maxZoom=3;controls.minDistance=3;controls.maxDistance=100;
  controls.addEventListener('change',()=>{renderDirty=true;});
  const homeTheta=Math.atan2(homeCamera.x-target.x,homeCamera.z-target.z),angle=T.MathUtils.degToRad(plan.view_angle_degrees);
  controls.minAzimuthAngle=homeTheta-angle;controls.maxAzimuthAngle=homeTheta+angle;
  let surfaceProjection=null;
  let captureMode=false,playing=false,elapsed=0,dusk=false,selected='',drag=null,last=0;
  const offsets=new Map(plan.objects.filter(x=>x.movement).map(x=>[x.id,new T.Vector3()]));
  const select=document.querySelector('#object');
  for(const x of plan.objects.filter(x=>x.movement)){const option=document.createElement('option');option.value=x.id;option.textContent=x.label;select.append(option);}
  function fit(){const w=canvas.clientWidth,h=canvas.clientHeight;renderer.setSize(w,h,false);const aspect=w/h;if(camera.isOrthographicCamera){const half=plan.camera.vertical_span/2*Math.max(1,appearance.framingAspect/aspect);camera.left=-half*aspect;camera.right=half*aspect;camera.top=half;camera.bottom=-half;}else{camera.aspect=aspect;camera.fov=T.MathUtils.radToDeg(2*Math.atan(Math.tan(T.MathUtils.degToRad(plan.camera.fov)/2)*Math.max(1,appearance.framingAspect/aspect)));}camera.updateProjectionMatrix();renderDirty=true;}
  function resetCamera(){const damping=controls.enableDamping;controls.enableDamping=false;controls.update();camera.position.copy(homeCamera);camera.zoom=1;camera.lookAt(target);controls.target.copy(target);camera.updateProjectionMatrix();controls.update();controls.enableDamping=damping;}
  function light(on){dusk=on;surfaceProjection?.setDusk(on);scene.background.set(on?'#364b55':plan.background);
    if(reference){const cfg=referenceLightState(appearance.lighting,on);sun.intensity=cfg.sun_intensity;hemi.intensity=cfg.hemisphere_intensity;sun.color.set(cfg.sun_color);}
    else{sun.intensity=showcase?(on?.48:3.3):(on?.75:3.1);hemi.intensity=showcase?(on?.48:.90):(on?1:2.3);sun.color.set(on?'#a8c4ef':showcase?'#fff0d3':'#ffebc5');}
    if(stylized){scene.environmentIntensity=on?.18:.40;craft.setDusk(on);for(const node of nodes.values())node.userData.setWaterDusk?.(on);scene.userData.canvaslabWaterRevision=(scene.userData.canvaslabWaterRevision||0)+1;}
    renderDirty=true;document.body.dataset.dusk=String(on);document.querySelector('#dusk').setAttribute('aria-pressed',String(on));document.querySelector('#day').setAttribute('aria-pressed',String(!on));}
  function motion(on){playing=on;document.querySelector('#motion').setAttribute('aria-pressed',String(on));document.querySelector('#motion span').textContent=river?(on?'停止漫游':'漫游'):(on?'暂停动画':'播放动画');}
  function projection(kind){camera=kind==='orthographic'?ortho:perspective;controls.object=camera;fit();resetCamera();document.querySelector('#iso').setAttribute('aria-pressed',String(camera===ortho));document.querySelector('#perspective').setAttribute('aria-pressed',String(camera===perspective));}
  function sample(t){
    for(const spec of plan.objects){const node=nodes.get(spec.id);if(spec.movement){const off=offsets.get(spec.id);node.position.fromArray(spec.position).add(off);node.position.x+=Math.sin(t*spec.movement.speed)*spec.movement.amplitude;const [a,b,c,d]=spec.movement.bounds;node.position.x=T.MathUtils.clamp(node.position.x,a,c);node.position.z=T.MathUtils.clamp(node.position.z,b,d);}
      if(spec.kind==='water'){node.userData.updateWater?.(t);node.children.forEach(child=>{if(child.userData.ripple!==undefined)child.scale.x=.8+.2*Math.sin(t+child.userData.ripple);});}
    }
    renderDirty=true;if(showcase)renderer.shadowMap.needsUpdate=true;
  }
  function finish(cancel=false){if(!drag)return;if(cancel){drag.node.position.copy(drag.start);offsets.get(drag.id).copy(drag.offset);renderDirty=true;scene.userData.canvaslabWaterRevision=(scene.userData.canvaslabWaterRevision||0)+1;if(showcase)renderer.shadowMap.needsUpdate=true;}try{canvas.releasePointerCapture(drag.pointer);}catch{}drag=null;controls.enabled=true;canvas.style.cursor='grab';}
  function reset(){finish(true);motion(false);elapsed=0;for(const off of offsets.values())off.set(0,0,0);sample(0);light(false);selected='';select.value='';projection(plan.camera.projection);document.querySelector('#status').textContent='视角、物件、灯光和动画均已复位';}
  function move(id,x,z){const spec=nodes.get(id).userData.spec,node=nodes.get(id),[a,b,c,d]=spec.movement.bounds;node.position.x=T.MathUtils.clamp(x,a,c);node.position.z=T.MathUtils.clamp(z,b,d);offsets.get(id).copy(node.position).sub(new T.Vector3().fromArray(spec.position));offsets.get(id).x-=Math.sin(elapsed*spec.movement.speed)*spec.movement.amplitude;scene.userData.canvaslabWaterRevision=(scene.userData.canvaslabWaterRevision||0)+1;renderDirty=true;if(showcase)renderer.shadowMap.needsUpdate=true;}
  const ray=new T.Raycaster(),pointer=new T.Vector2(),plane=new T.Plane(new T.Vector3(0,1,0),0),point=new T.Vector3();
  function rayAt(event){const r=canvas.getBoundingClientRect();pointer.set((event.clientX-r.left)/r.width*2-1,-(event.clientY-r.top)/r.height*2+1);ray.setFromCamera(pointer,camera);}
  function pick(event){rayAt(event);const hits=ray.intersectObjects([...nodes.values()],true);for(const hit of hits){const id=hit.object.userData.objectId;if(nodes.get(id)?.userData.spec.movement)return id; // nonmovable occluders block picking through them
      if(id)return null;}return null;}
  canvas.addEventListener('pointerdown',e=>{if(e.button!==0||captureMode)return;const id=pick(e);if(!id)return;e.stopImmediatePropagation();motion(false);selected=id;select.value=id;const node=nodes.get(id);plane.constant=-node.position.y;ray.ray.intersectPlane(plane,point);drag={id,node,start:node.position.clone(),offset:offsets.get(id).clone(),delta:node.position.clone().sub(point),pointer:e.pointerId};controls.enabled=false;canvas.setPointerCapture(e.pointerId);canvas.focus();},true);
  canvas.addEventListener('pointermove',e=>{if(!drag||e.pointerId!==drag.pointer)return;rayAt(e);if(ray.ray.intersectPlane(plane,point)){point.add(drag.delta);move(drag.id,point.x,point.z);}e.stopImmediatePropagation();},true);
  canvas.addEventListener('pointerup',()=>finish());canvas.addEventListener('pointercancel',()=>finish(true));canvas.addEventListener('lostpointercapture',()=>finish(true));addEventListener('blur',()=>finish(true));
  select.addEventListener('change',()=>{selected=select.value;if(selected){motion(false);canvas.focus();document.querySelector('#status').textContent='方向键移动物件；Esc 取消当前拖动';}});
  canvas.addEventListener('keydown',e=>{if(e.key==='Escape'){finish(true);return;}if(!selected||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();motion(false);const n=nodes.get(selected);move(selected,n.position.x+(e.key==='ArrowLeft'?-.12:e.key==='ArrowRight'?.12:0),n.position.z+(e.key==='ArrowUp'?-.12:e.key==='ArrowDown'?.12:0));});
  document.querySelector('#reset').addEventListener('click',reset);document.querySelector('#dusk').addEventListener('click',()=>light(!dusk));document.querySelector('#motion').addEventListener('click',()=>motion(!playing));
  document.querySelector('#day').addEventListener('click',()=>light(false));
  document.querySelector('#iso').addEventListener('click',()=>{motion(false);projection('orthographic');});
  document.querySelector('#perspective').addEventListener('click',()=>{motion(false);projection('perspective');});
  addEventListener('resize',fit);canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();showError('图形上下文已丢失，请刷新页面。');});
  fit();resetCamera();motion(false);await document.fonts.ready;
  surfaceProjection=await createSurfaceProjection(renderer,scene,nodes,plan,analysis,manifest);
  await renderer.compileAsync(scene,camera);renderScene();
  function objectInfo(){scene.updateMatrixWorld(true);camera.updateMatrixWorld(true);const w=canvas.width/renderer.getPixelRatio(),h=canvas.height/renderer.getPixelRatio(),out={};
    for(const [id,node] of nodes){const box=new T.Box3().setFromObject(node),points=[];for(const x of [box.min.x,box.max.x])for(const y of [box.min.y,box.max.y])for(const z of [box.min.z,box.max.z])points.push(new T.Vector3(x,y,z).project(camera));
      const visible=points.some(p=>p.z>=-1&&p.z<=1);const xs=points.map(p=>(p.x+1)*w/2),ys=points.map(p=>(1-p.y)*h/2);
      out[id]={position:node.position.toArray(),quaternion:node.quaternion.toArray(),component:componentInstanceInfo(node),world_box:[box.min.toArray(),box.max.toArray()],screen_box:visible?[Math.min(...xs),Math.min(...ys),Math.max(...xs)-Math.min(...xs),Math.max(...ys)-Math.min(...ys)]:null};}return out;}
  function snapshot(){renderScene();return {build_id:manifest.build_id,objects:objectInfo(),camera:{position:camera.position.toArray(),target:controls.target.toArray(),projection:camera.projectionMatrix.toArray()},playing,time:elapsed,dusk,selected,render_quality:plan.render_quality||'standard',appearance_mode:appearance.mode,source_projection:surfaceProjection?.snapshot()??null,lighting_variant:reference?(dusk?'inferred-dusk':'reference'):'legacy',material_families:craft?.families,local_lights:craft?.lightCount||0,helper_triangles:helperTriangles,draw_calls:renderer.info.render.calls,triangles:renderer.info.render.triangles,webgl_version:renderer.getContext().getParameter(renderer.getContext().VERSION)};}
  function prepare({yaw=0,time=0,night=false,zoom=1}={}){if(!Number.isFinite(yaw)||Math.abs(yaw)>plan.view_angle_degrees)throw new Error('View outside contract');if(!Number.isFinite(zoom)||zoom<1||zoom>2.2)throw new Error('Capture zoom must be between 1 and 2.2');captureMode=true;document.body.dataset.capture='true';finish(true);reset();captureMode=true;renderer.setPixelRatio(1);fit();controls.enableDamping=false;controls.enabled=false;elapsed=time;sample(time);light(night);const offset=homeCamera.clone().sub(target).applyAxisAngle(new T.Vector3(0,1,0),T.MathUtils.degToRad(yaw));camera.position.copy(target).add(offset);camera.zoom=zoom;camera.updateProjectionMatrix();camera.lookAt(target);camera.updateMatrixWorld(true);renderScene();return snapshot();}
  function diagnostic(mode){if(!['id','depth'].includes(mode))throw new Error('Unknown diagnostic mode');const saved=[],helperVisibility=[],background=scene.background,tm=renderer.toneMapping,space=renderer.outputColorSpace,shadow=renderer.shadowMap.enabled,oldTarget=renderer.getRenderTarget();
    // Main canvas MSAA blends neighboring IDs into other valid object IDs.
    // Render labels offscreen with zero samples, then read exact unfiltered bytes.
    const targetPass=new T.WebGLRenderTarget(canvas.width,canvas.height,{samples:0,depthBuffer:true,format:T.RGBAFormat,type:T.UnsignedByteType});
    try{
    scene.userData.canvaslabDiagnostic=true;scene.background=new T.Color(0);renderer.toneMapping=T.NoToneMapping;renderer.outputColorSpace=T.LinearSRGBColorSpace;renderer.shadowMap.enabled=false;
    scene.traverse(mesh=>{if(!mesh.isMesh)return;if(mesh.userData.helperGeometry){helperVisibility.push([mesh,mesh.visible]);mesh.visible=false;return;}const index=plan.objects.findIndex(n=>n.id===mesh.userData.objectId)+1;saved.push([mesh,mesh.material]);mesh.material=mode==='depth'?new T.MeshDepthMaterial({depthPacking:T.BasicDepthPacking,side:T.DoubleSide}):new T.MeshBasicMaterial({color:new T.Color().setRGB(((index>>16)&255)/255,((index>>8)&255)/255,(index&255)/255),side:T.DoubleSide});});
    renderer.setRenderTarget(targetPass);renderer.render(scene,camera);
    const pixels=new Uint8Array(canvas.width*canvas.height*4);renderer.readRenderTargetPixels(targetPass,0,0,canvas.width,canvas.height,pixels);
    const exportCanvas=document.createElement('canvas');exportCanvas.width=canvas.width;exportCanvas.height=canvas.height;
    const ctx=exportCanvas.getContext('2d'),data=ctx.createImageData(canvas.width,canvas.height),stride=canvas.width*4;
    for(let y=0;y<canvas.height;y++)data.data.set(pixels.subarray((canvas.height-1-y)*stride,(canvas.height-y)*stride),y*stride);
    ctx.putImageData(data,0,0);return exportCanvas.toDataURL('image/png');
    }finally{for(const [mesh,mat] of saved){mesh.material.dispose();mesh.material=mat;}for(const [mesh,visible] of helperVisibility)mesh.visible=visible;scene.background=background;renderer.toneMapping=tm;renderer.outputColorSpace=space;renderer.shadowMap.enabled=shadow;renderer.shadowMap.needsUpdate=true;renderer.setRenderTarget(oldTarget);targetPass.dispose();scene.userData.canvaslabDiagnostic=false;renderer.resetState();renderScene();}}
  window.canvaslab3d={ready:true,buildId:manifest.build_id,idEncoding:'rgb-index-v2-no-msaa',referenceSize:analysis.scene_box.slice(2),snapshot,prepare,diagnostic,
    interactive(){captureMode=false;document.body.dataset.capture='false';controls.enabled=true;controls.enableDamping=true;renderer.setPixelRatio(Math.min(devicePixelRatio,showcase?2:1.5));reset();},
    objectIds:plan.objects.map(x=>x.id),movableIds:plan.objects.filter(x=>x.movement).map(x=>x.id)};
  document.querySelector('#status').textContent=`${plan.objects.length} 个真实三维对象 · 可交互开发预览${surfaceProjection?' · 原图外观投影（暮色为推断）':''}`;
  function animate(now){requestAnimationFrame(animate);const dt=Math.min((now-last)/1000,.05);last=now;if(captureMode||document.hidden)return;if(playing){elapsed+=dt;sample(elapsed);if(river){const offset=homeCamera.clone().sub(target).applyAxisAngle(new T.Vector3(0,1,0),Math.sin(elapsed*.16)*.35);camera.position.copy(target).add(offset);camera.lookAt(target);}}controls.update();if(!showcase||playing||renderDirty)renderScene();}requestAnimationFrame(animate);
}
function showError(message){const el=document.querySelector('#error');el.hidden=false;el.textContent=`无法加载三维场景：${message}`;document.querySelector('#status').textContent='渲染失败，未通过检查';}
start().catch(error=>{console.error(error);showError(error.message);});
