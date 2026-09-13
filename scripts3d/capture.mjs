// Managed local-development capture worker. Only serves verified build bytes;
// never accepts a page URL, executable code or user-defined test expressions.
import fs from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../runtime3d/package.json',import.meta.url));
const {chromium}=require('playwright-core');
const [buildArg,outArg]=process.argv.slice(2);
if(!buildArg||!outArg)throw new Error('Usage: node scripts3d/capture.mjs <verified-build-directory> <new-evidence-directory>');
const root=path.resolve(buildArg),out=path.resolve(outArg),sha=b=>crypto.createHash('sha256').update(b).digest('hex');
const canonical=value=>JSON.stringify(value,(_k,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.entries(v).sort(([a],[b])=>a<b?-1:a>b?1:0)):v);
const manifestBytes=await fs.readFile(path.join(root,'build-manifest.json'));
const manifest=JSON.parse(manifestBytes),{build_id,...unsigned}=manifest;
if(sha(Buffer.from(canonical(unsigned)))!==build_id||path.basename(root)!==build_id)throw new Error('Invalid build identity');
const assets=new Map([['/build-manifest.json',manifestBytes]]);
for(const [name,digest] of Object.entries(manifest.files)){
  if(!/^[A-Za-z0-9_-]+\.[A-Za-z0-9.-]+$/.test(name))throw new Error('Invalid asset path');
  const bytes=await fs.readFile(path.join(root,name));if(sha(bytes)!==digest)throw new Error(`Changed asset: ${name}`);assets.set('/'+name,bytes);
}
const annotation=JSON.parse(assets.get('/reference-annotations.json'));
const plan=JSON.parse(assets.get('/scene.json'));
const native=annotation.scene_box.slice(2).map(Math.round);
if(Math.max(...native)>4096||native[0]*native[1]>8_388_608)throw new Error('Native capture exceeds development worker capacity; original retained, no thumbnail retry');
await fs.mkdir(out,{recursive:true});
const server=http.createServer((req,res)=>{const name=req.url==='/'?'/index.html':req.url,bytes=assets.get(name);if(!bytes){res.writeHead(404);res.end();return;}res.setHeader('Cache-Control','no-store');res.setHeader('Content-Type',name.endsWith('.svg')?'image/svg+xml':name.endsWith('.glb')?'model/gltf-binary':name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':name.endsWith('.html')?'text/html':'application/json');res.end(bytes);});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
let browser;
try{
  browser=await chromium.launch({channel:'chrome',headless:true});
  const context=await browser.newContext({viewport:{width:native[0],height:native[1]},deviceScaleFactor:1,serviceWorkers:'block'});
  await context.route('**/*',route=>{const url=new URL(route.request().url());return url.origin===origin?route.continue():route.abort('blockedbyclient');});
  const page=await context.newPage(),errors=[],warnings=[],network=[],pending=[];
  const comparePixels=async(first,second)=>{
    if(sha(first)===sha(second))return {exact_equal:true,within_rounding:true,max_abs_channel_delta:0,changed_pixel_ratio:0};
    return page.evaluate(async(encoded)=>{
      const decoded=[];
      for(const data of encoded){
        const bytes=Uint8Array.from(atob(data),ch=>ch.charCodeAt(0));
        const bitmap=await createImageBitmap(new Blob([bytes],{type:'image/png'}));
        const canvas=new OffscreenCanvas(bitmap.width,bitmap.height),ctx=canvas.getContext('2d');
        ctx.drawImage(bitmap,0,0);decoded.push({width:bitmap.width,height:bitmap.height,pixels:ctx.getImageData(0,0,bitmap.width,bitmap.height).data});bitmap.close();
      }
      const [a,b]=decoded;
      if(a.width!==b.width||a.height!==b.height)return {exact_equal:false,within_rounding:false,dimension_mismatch:true};
      let maxDelta=0,changed=0;
      for(let i=0;i<a.pixels.length;i+=4){let difference=false;for(let c=0;c<4;c++){const delta=Math.abs(a.pixels[i+c]-b.pixels[i+c]);maxDelta=Math.max(maxDelta,delta);difference||=delta!==0;}if(difference)changed++;}
      const ratio=changed/(a.width*a.height);
      // This is ONLY a restoration regression check. A driver's one-code-value
      // rounding on <=1% of pixels is not an object/style/fidelity deviation.
      // Original/build/capture SHA256 provenance remains strictly byte-exact.
      return {exact_equal:changed===0,within_rounding:maxDelta<=1&&ratio<=.01,max_abs_channel_delta:maxDelta,changed_pixel_ratio:ratio};
    },[first.toString('base64'),second.toString('base64')]);
  };
  page.on('pageerror',e=>errors.push(e.message));page.on('console',msg=>{if(msg.type()==='error')errors.push(msg.text());if(msg.type()==='warning')warnings.push(msg.text());});
  page.on('response',response=>pending.push((async()=>{const name=new URL(response.url()).pathname||'/';const bytes=await response.body(),key=name==='/'?'/index.html':name;if(!assets.has(key)||sha(bytes)!==sha(assets.get(key)))throw new Error('Served asset differs from build');network.push({path:key,sha256:sha(bytes)});})()));
  await page.goto(origin,{waitUntil:'networkidle'});
  await page.waitForFunction(()=>window.canvaslab3d?.ready,{},{timeout:30000});
  // Same native viewport, with real DOM controls visible, for source/UI QA.
  // The diagnostic reference below still hides UI and remains unchanged.
  await page.screenshot({path:path.join(out,'reference-ui.png')});
  const reference=await page.evaluate(()=>window.canvaslab3d.prepare());
  if(reference.build_id!==build_id)throw new Error('Wrong browser build');
  await page.locator('canvas').screenshot({path:path.join(out,'reference.png')});
  for(const mode of ['id','depth']){
    const data=await page.evaluate(mode=>window.canvaslab3d.diagnostic(mode),mode);
    await fs.writeFile(path.join(out,`${mode}.png`),Buffer.from(data.split(',')[1],'base64'),{flag:'wx'});
  }
  const views=[], qualityViews=[];
  if(plan.render_quality==='showcase'){
    const closeup=await page.evaluate(()=>window.canvaslab3d.prepare({zoom:1.6}));
    await page.locator('canvas').screenshot({path:path.join(out,'closeup.png')});
    qualityViews.push({file:'closeup.png',zoom:1.6,state:closeup});
    const dusk=await page.evaluate(()=>window.canvaslab3d.prepare({night:true}));
    await page.locator('canvas').screenshot({path:path.join(out,'dusk.png')});
    qualityViews.push({file:'dusk.png',night:true,state:dusk});
    await page.evaluate(()=>window.canvaslab3d.prepare());
  }
  // Check visible restoration, not only material UUIDs. Record exact equality
  // plus narrowly bounded GPU rounding after ID/depth and quality views.
  const restoredReference=await page.locator('canvas').screenshot();
  await fs.writeFile(path.join(out,'restored-reference.png'),restoredReference,{flag:'wx'});
  const referenceRestored=await comparePixels(restoredReference,await fs.readFile(path.join(out,'reference.png')));
  for(const yaw of [-Math.min(60,plan.view_angle_degrees),Math.min(60,plan.view_angle_degrees)]){
    const state=await page.evaluate(yaw=>window.canvaslab3d.prepare({yaw}),yaw);
    const file=yaw<0?'left.png':'right.png';await page.locator('canvas').screenshot({path:path.join(out,file)});views.push({yaw,file,state});
  }
  await page.setViewportSize({width:1440,height:900});
  await page.evaluate(()=>window.canvaslab3d.interactive());
  await page.locator('canvas').screenshot({path:path.join(out,'desktop.png')});
  const tests=[];
  const state=()=>page.evaluate(()=>window.canvaslab3d.snapshot());
  // Canvas-only pixels exclude overlaid DOM controls changing selection/focus.
  // Reading toDataURL does not request a render and still detects stale frames.
  const canvasPixels=async()=>Buffer.from((await page.locator('canvas').evaluate(el=>el.toDataURL('image/png'))).split(',')[1],'base64');
  const river=plan.presentation==='jiangnan';
  const selectObject=async(id)=>{if(!river){await page.locator('#object').selectOption(id);return;}const s=await state();const [x,y,w,h]=s.objects[id].screen_box;await page.mouse.click(x+w/2,y+h/2);if((await state()).selected!==id)throw new Error('Visible component could not be selected: '+id);};
  const check=(name,passed,details)=>tests.push({name,passed:!!passed,details});
  check('fixed_reference_pixels_restore_after_diagnostics',referenceRestored.within_rounding,referenceRestored);
  check('page_identity',(await page.title())===`${plan.title} · CanvasLab 3D`,{url:page.url(),title:await page.title()});
  check('nonblank_geometry',reference.triangles>0&&reference.draw_calls>0,{triangles:reference.triangles,draw_calls:reference.draw_calls});
  check('error_overlay_absent',await page.locator('#error').isHidden(),{});
  if(plan.objects.some(o=>o.kind==='asset')){
    const imported=plan.objects.filter(o=>o.kind==='asset'),materials=new Set(),geometries=new Set();
    const identity=imported.map(spec=>{
      const actual=reference.objects[spec.id]?.component,filename=`component-${spec.asset_id}.glb`;
      const expected=[[-.5,0,-.5],[.5,1,.5]];
      const valid=actual?.asset_id===spec.asset_id&&actual.object_id===spec.id&&actual.semantic_ids_valid&&actual.mesh_count>0&&
        JSON.stringify(actual.dimensions)===JSON.stringify(spec.dimensions)&&
        actual.normalized_box?.every((corner,i)=>corner.every((value,j)=>Number.isFinite(value)&&Math.abs(value-expected[i][j])<=1e-4))&&
        manifest.files[filename]===spec.asset_id&&assets.has('/'+filename);
      let isolated=true;
      for(const material of actual?.material_ids??[]){if(materials.has(material))isolated=false;materials.add(material);}
      for(const geometry of actual?.geometry_ids??[]){if(geometries.has(geometry))isolated=false;geometries.add(geometry);}
      return {object_id:spec.id,asset_id:spec.asset_id,valid:!!valid,isolated};
    });
    check('imported_component_identity_and_bounds',identity.every(x=>x.valid),identity);
    check('imported_component_instance_isolation',identity.every(x=>x.isolated),identity);
  }
  const start=await state();
  await page.getByRole('button',{name:'暮色',exact:true}).click();
  const night=await state();check('dusk_changes_scene',night.dusk&&!start.dusk,{before:start.dusk,after:night.dusk});
  if(plan.reference_projection)check('source_projection_dusk_fallback',start.source_projection?.active===true&&night.source_projection?.active===false,{day:start.source_projection,night:night.source_projection});
  await page.getByRole('button',{name:river?'漫游':'播放动画',exact:true}).click();
  await page.waitForTimeout(250);const moving=await state();
  await page.getByRole('button',{name:river?'停止漫游':'暂停动画',exact:true}).click();
  const paused=await state();await page.waitForTimeout(150);const still=await state();
  check('animation_advances_and_pauses',moving.time>0&&paused.time===still.time,{time:moving.time,paused:paused.time,still:still.time});
  await page.getByRole('button',{name:'复位',exact:true}).click();
  const reset=await state();check('reset_all_state',!reset.playing&&!reset.dusk&&reset.time===0,{time:reset.time,dusk:reset.dusk});
  if(plan.reference_projection)check('source_projection_restored_without_geometry',reset.source_projection?.active===true&&reset.source_projection?.geometry_added===0,reset.source_projection);
  if(plan.objects.some(o=>o.kind==='asset')){
    const restored=plan.objects.filter(o=>o.kind==='asset').map(spec=>{
      const before=reference.objects[spec.id]?.component,after=reset.objects[spec.id]?.component;
      return {object_id:spec.id,restored:JSON.stringify(before?.material_ids)===JSON.stringify(after?.material_ids)&&JSON.stringify(before?.geometry_ids)===JSON.stringify(after?.geometry_ids)};
    });
    check('imported_component_diagnostics_restore_materials',restored.every(x=>x.restored),restored);
  }
  const movableComponents=plan.objects.filter(o=>o.kind==='asset'&&o.movement);
  if(movableComponents.length){
    const movement=[];
    for(const spec of movableComponents){
      await selectObject(spec.id);const before=await state();
      const direction=before.objects[spec.id].position[0]<spec.movement.bounds[2]?'ArrowRight':'ArrowLeft';
      await page.locator('canvas').press(direction);const moved=await state();
      await page.getByRole('button',{name:'复位',exact:true}).click();const restored=await state();
      movement.push({object_id:spec.id,moved:moved.objects[spec.id].position[0]!==before.objects[spec.id].position[0],reset:JSON.stringify(restored.objects[spec.id].position)===JSON.stringify(before.objects[spec.id].position)});
    }
    check('imported_component_movement_and_reset',movement.every(x=>x.moved&&x.reset),movement);
  }
  const ids=await page.evaluate(()=>window.canvaslab3d.movableIds);
  if(ids.length){
    await selectObject(ids[0]);const before=await state();
    await page.locator('canvas').press('ArrowRight');const after=await state();
    check('keyboard_object_movement',after.objects[ids[0]].position[0]!==before.objects[ids[0]].position[0],{id:ids[0],before:before.objects[ids[0]].position,after:after.objects[ids[0]].position});
    await page.getByRole('button',{name:'复位',exact:true}).click();
    const pickState=await state(),box=pickState.objects[ids[0]].screen_box;
    const [x,y,w,h]=box;await page.mouse.move(x+w/2,y+h/2);await page.mouse.down();await page.mouse.move(x+w/2+35,y+h/2+3,{steps:8});await page.mouse.up();
    const dragged=await state();const cameraDelta=Math.max(...dragged.camera.position.map((v,i)=>Math.abs(v-pickState.camera.position[i])));
    check('pointer_drag_without_orbit',JSON.stringify(dragged.objects[ids[0]].position)!==JSON.stringify(pickState.objects[ids[0]].position)&&cameraDelta<1e-8,{id:ids[0],before:pickState.objects[ids[0]].position,after:dragged.objects[ids[0]].position,camera_delta:cameraDelta});
    await page.getByRole('button',{name:'复位',exact:true}).click();
    const beforeCancel=await state(),cancelBox=beforeCancel.objects[ids[0]].screen_box;
    const cancelPixels=await canvasPixels();
    const [cx,cy,cw,ch]=cancelBox;
    await page.mouse.move(cx+cw/2,cy+ch/2);await page.mouse.down();
    await page.mouse.move(cx+cw/2+40,cy+ch/2+4,{steps:8});
    await page.locator('canvas').press('Escape');await page.mouse.up();
    // Screenshot before snapshot() can force a render: this catches a cached
    // frame that failed to redraw after cancellation restored the transform.
    const cancelledPixels=await canvasPixels(),cancelled=await state();
    await fs.writeFile(path.join(out,'cancel-before.png'),cancelPixels,{flag:'wx'});
    await fs.writeFile(path.join(out,'cancel-after.png'),cancelledPixels,{flag:'wx'});
    const cancelComparison=await comparePixels(cancelledPixels,cancelPixels);
    check('escape_drag_cancel_restores_visible_frame',
      JSON.stringify(cancelled.objects[ids[0]].position)===JSON.stringify(beforeCancel.objects[ids[0]].position)&&cancelComparison.within_rounding,
      {object_id:ids[0],...cancelComparison});
  }
  await page.getByRole('button',{name:'复位',exact:true}).click();
  const beforeOrbit=await state();await page.mouse.move(70,450);await page.mouse.down();await page.mouse.move(180,450,{steps:10});await page.mouse.up();await page.waitForTimeout(200);
  const afterOrbit=await state();check('background_orbit',JSON.stringify(beforeOrbit.camera.position)!==JSON.stringify(afterOrbit.camera.position),{before:beforeOrbit.camera.position,after:afterOrbit.camera.position});
  await page.getByRole('button',{name:'复位',exact:true}).click();
  await page.waitForTimeout(250);const settled=await state();
  const resetDrift=Math.max(...settled.camera.position.map((v,i)=>Math.abs(v-start.camera.position[i])));
  check('reset_clears_orbit_inertia',resetDrift<1e-8,{camera_delta:resetDrift,before:start.camera.position,after:settled.camera.position});
  if(river){
    await page.getByRole('button',{name:'透视',exact:true}).click();const perspective=await state();
    await page.getByRole('button',{name:'等轴',exact:true}).click();const orthographic=await state();
    check('projection_switch',JSON.stringify(perspective.camera.projection)!==JSON.stringify(orthographic.camera.projection)&&JSON.stringify(orthographic.camera.projection)===JSON.stringify(start.camera.projection),{});
    await page.getByRole('button',{name:'暮色',exact:true}).click();await page.getByRole('button',{name:'日景',exact:true}).click();check('day_mode_restores_lighting',!(await state()).dusk,{});
  }
  // Measure actual changing frames, not the display cadence of the cached idle
  // scene. Motion changes boats, water and (in Jiangnan mode) camera/reflections.
  await page.getByRole('button',{name:river?'漫游':'播放动画',exact:true}).click();
  const frameTimes=await page.evaluate(async()=>{const results=[];let previous=performance.now();for(let i=0;i<90;i++){await new Promise(requestAnimationFrame);const now=performance.now();if(i>=30)results.push(now-previous);previous=now;}return results;});
  await page.getByRole('button',{name:river?'停止漫游':'暂停动画',exact:true}).click();
  await page.getByRole('button',{name:'复位',exact:true}).click();
  const sorted=[...frameTimes].sort((a,b)=>a-b);
  const performanceSample={mode:'animated_scene',viewport:[1440,900],sample_count:sorted.length,median_ms:sorted[Math.floor(sorted.length/2)],p95_ms:sorted[Math.floor(sorted.length*.95)],certified:false,reason:'short active-animation headless development sample; not a calibrated real-device benchmark'};
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(100);
  await page.screenshot({path:path.join(out,'mobile.png')});
  const mobile=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,canvas:document.querySelector('canvas').getBoundingClientRect().toJSON()}));
  check('mobile_no_horizontal_overflow',mobile.scroll===mobile.width,mobile);
  await page.setViewportSize({width:844,height:390});await page.waitForTimeout(100);await page.screenshot({path:path.join(out,'landscape.png')});
  check('landscape_controls_visible',await page.locator('#reset').evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}),{width:844,height:390});
  await Promise.all(pending);
  if(plan.objects.some(o=>o.kind==='asset')){
    const loaded=[...new Set(plan.objects.filter(o=>o.kind==='asset').map(o=>o.asset_id))].map(asset_id=>({asset_id,path:`/component-${asset_id}.glb`,verified:network.some(n=>n.path===`/component-${asset_id}.glb`&&n.sha256===asset_id)}));
    check('imported_component_exact_network_bytes',loaded.every(x=>x.verified),loaded);
  }
  check('console_health',errors.length===0,{errors,warnings});
  const id_encoding=await page.evaluate(()=>window.canvaslab3d.idEncoding??null);
  const observation={kind:'canvaslab-3d-local-capture-v1',id_encoding,build_id,browser_version:browser.version(),worker_script_sha256:sha(await fs.readFile(fileURLToPath(import.meta.url))),native_size:native,reference,views,quality_views:qualityViews,tests,performance_sample:performanceSample,network,errors,warnings,production_attestation:false};
  await fs.writeFile(path.join(out,'observation.json'),JSON.stringify(observation,null,2),{flag:'wx'});
  console.log(JSON.stringify({build_id,tests:tests.map(t=>({name:t.name,passed:t.passed})),errors}));
}finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
