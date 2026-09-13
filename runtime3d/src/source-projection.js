/** Project verified SOURCE-LIT pixels onto existing static mesh surfaces.
 * Frozen mesh/projector transforms keep markings attached during orbit/movement.
 * Original-view depth and source-owned masks exclude occluded/unobserved faces.
 * No geometry, foreground cards, inferred albedo or arbitrary URLs are created.
 */
import * as T from 'three';

const MAX_PIXELS=4194304, MAX_BYTES=24*1024*1024;
const SHA=/^[a-f0-9]{64}$/;

async function checkedFile(name,manifest,budget){
  if(!/^(reference-source\.png|reference-projection\.json|reference-mask-[a-z][a-z0-9_-]{0,63}\.png)$/.test(name))throw new Error('Invalid projection asset name');
  const expected=manifest.files?.[name];
  if(!SHA.test(expected??''))throw new Error('Projection asset missing from immutable manifest');
  const response=await fetch(`./${name}`,{redirect:'error',credentials:'same-origin'});
  if(!response.ok)throw new Error(`Missing projection asset ${name}`);
  const bytes=await response.arrayBuffer();budget.bytes+=bytes.byteLength;
  if(budget.bytes>MAX_BYTES)throw new Error('Projection byte budget exceeded');
  const digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
  if(digest!==expected)throw new Error(`Changed projection asset ${name}`);
  return bytes;
}

async function imageTexture(bytes,size,colorSpace){
  const view=new DataView(bytes);
  if(bytes.byteLength<33||view.getUint32(0)!==0x89504e47||view.getUint32(4)!==0x0d0a1a0a||
     view.getUint32(12)!==0x49484452||view.getUint32(16)!==size[0]||view.getUint32(20)!==size[1])throw new Error('Projection PNG dimensions or format changed');
  const bitmap=await createImageBitmap(new Blob([bytes],{type:'image/png'}),{imageOrientation:'none',premultiplyAlpha:'none',colorSpaceConversion:'none'});
  if(bitmap.width!==size[0]||bitmap.height!==size[1]){bitmap.close();throw new Error('Projection decoded dimensions changed');}
  const texture=new T.Texture(bitmap);texture.flipY=false;texture.colorSpace=colorSpace;
  texture.minFilter=T.LinearFilter;texture.magFilter=T.LinearFilter;texture.generateMipmaps=false;
  texture.wrapS=texture.wrapT=T.ClampToEdgeWrapping;texture.needsUpdate=true;
  return texture;
}

export async function createSurfaceProjection(renderer,scene,nodes,plan,analysis,manifest){
  if(!plan.reference_projection)return null;
  const ids=plan.reference_projection.object_ids;
  if(plan.appearance_mode!=='reference'||plan.camera.projection!=='orthographic'||
     plan.reference_lighting?.tone_mapping!=='none'||plan.reference_lighting?.exposure!==1||
     !Array.isArray(ids)||ids.length<1||ids.length>4||new Set(ids).size!==ids.length)throw new Error('Unsupported source projection contract');
  const budget={bytes:0},meta=JSON.parse(new TextDecoder().decode(await checkedFile('reference-projection.json',manifest,budget)));
  const {width:w,height:h}=meta,size=[w,h];
  if(meta.kind!=='source-appearance-projection-v1'||meta.source_sha256!==manifest.source.sha256||
     ![w,h].every(Number.isSafeInteger)||Math.min(w,h)<1||Math.max(w,h)>Math.min(4096,renderer.capabilities.maxTextureSize)||w*h>MAX_PIXELS||
     JSON.stringify(meta.scene_box)!==JSON.stringify(analysis.scene_box)||w!==analysis.scene_box[2]||h!==analysis.scene_box[3]||
     meta.source!=='reference-source.png'||meta.resampled!==false||!Array.isArray(meta.objects)||
     meta.objects.length!==ids.length||new Set(meta.objects.map(o=>o.object_id)).size!==ids.length)throw new Error('Projection provenance or native-size mismatch');
  const textures=[],savedMaterials=[],hidden=[];
  let target;
  try{
    const image=await imageTexture(await checkedFile(meta.source,manifest,budget),size,T.SRGBColorSpace);textures.push(image);
    const masks=new Map();
    for(const item of meta.objects){
      const spec=plan.objects.find(o=>o.id===item.object_id);
      if(!ids.includes(item.object_id)||!spec||spec.kind!=='asset'||spec.parent_id||spec.region_ids.length!==1||
         spec.region_ids[0]!==item.region_id||item.mask!==`reference-mask-${item.object_id}.png`)throw new Error('Projection object ownership mismatch');
      const texture=await imageTexture(await checkedFile(item.mask,manifest,budget),size,T.NoColorSpace);
      textures.push(texture);masks.set(item.object_id,texture);
    }
    const half=plan.camera.vertical_span/2;
    const sourceCamera=new T.OrthographicCamera(-half*w/h,half*w/h,half,-half,.1,500);
    sourceCamera.position.fromArray(plan.camera.position);sourceCamera.lookAt(new T.Vector3().fromArray(plan.camera.target));
    sourceCamera.updateMatrixWorld(true);scene.updateMatrixWorld(true);
    const sourceView=new T.Matrix4().multiplyMatrices(sourceCamera.projectionMatrix,sourceCamera.matrixWorldInverse);
    const towardCamera=sourceCamera.position.clone().sub(new T.Vector3().fromArray(plan.camera.target)).normalize();
    target=new T.WebGLRenderTarget(w,h,{samples:0,depthBuffer:true});
    target.depthTexture=new T.DepthTexture(w,h,T.UnsignedIntType);
    target.depthTexture.minFilter=target.depthTexture.magFilter=T.NearestFilter;
    const oldTarget=renderer.getRenderTarget(),oldOverride=scene.overrideMaterial,oldBackground=scene.background,
      oldShadow=renderer.shadowMap.enabled,oldTone=renderer.toneMapping;
    const depthMaterial=new T.MeshBasicMaterial({color:0,side:T.DoubleSide});
    try{
      scene.traverse(n=>{if(n.isMesh&&n.userData.helperGeometry){hidden.push([n,n.visible]);n.visible=false;}});
      scene.overrideMaterial=depthMaterial;scene.background=null;renderer.shadowMap.enabled=false;renderer.toneMapping=T.NoToneMapping;
      renderer.setRenderTarget(target);renderer.clear();renderer.render(scene,sourceCamera);
    }finally{
      scene.overrideMaterial=oldOverride;scene.background=oldBackground;renderer.shadowMap.enabled=oldShadow;renderer.toneMapping=oldTone;
      for(const [node,visible] of hidden)node.visible=visible;
      renderer.setRenderTarget(oldTarget);depthMaterial.dispose();renderer.shadowMap.needsUpdate=true;
    }
    const enabled={value:1};let meshCount=0;
    for(const id of ids){
      const root=nodes.get(id);if(!root)throw new Error('Missing projection root');
      root.traverse(mesh=>{
        if(!mesh.isMesh)return;
        if(!mesh.geometry.getAttribute('normal'))mesh.geometry.computeVertexNormals();
        const projector=new T.Matrix4().multiplyMatrices(sourceView,mesh.matrixWorld);
        const normalMatrix=new T.Matrix3().getNormalMatrix(mesh.matrixWorld);
        const patch=material=>{
          if(!material.isMeshStandardMaterial)throw new Error('Projection requires authored PBR mesh materials');
          const cloned=material.clone();
          cloned.onBeforeCompile=shader=>{
            Object.assign(shader.uniforms,{sourceProjector:{value:projector},sourceNormalMatrix:{value:normalMatrix},
              sourceTowardsCamera:{value:towardCamera},sourceImage:{value:image},sourceMask:{value:masks.get(id)},
              sourceDepth:{value:target.depthTexture},sourceProjectionEnabled:enabled});
            shader.vertexShader=`uniform mat4 sourceProjector;\nuniform mat3 sourceNormalMatrix;\nvarying vec4 sourceClip;\nvarying vec3 sourceNormal;\n${shader.vertexShader}`;
            shader.vertexShader=shader.vertexShader.replace('#include <project_vertex>',`#include <project_vertex>\nsourceClip=sourceProjector*vec4(position,1.0);\nsourceNormal=normalize(sourceNormalMatrix*normal);`);
            shader.fragmentShader=`uniform sampler2D sourceImage;\nuniform sampler2D sourceMask;\nuniform sampler2D sourceDepth;\nuniform vec3 sourceTowardsCamera;\nuniform float sourceProjectionEnabled;\nvarying vec4 sourceClip;\nvarying vec3 sourceNormal;\n${shader.fragmentShader}`;
            shader.fragmentShader=shader.fragmentShader.replace('#include <opaque_fragment>',`
vec3 sourceP=sourceClip.xyz/sourceClip.w*0.5+0.5;
vec2 sourceUV=vec2(sourceP.x,1.0-sourceP.y);
float sourceFront=step(0.08,dot(normalize(sourceNormal),sourceTowardsCamera));
float sourceBounds=step(0.0,sourceP.x)*step(sourceP.x,1.0)*step(0.0,sourceP.y)*step(sourceP.y,1.0)*step(0.0,sourceP.z)*step(sourceP.z,1.0);
float depthError=abs(texture2D(sourceDepth,sourceP.xy).r-sourceP.z);
float sourceVisible=1.0-step(max(0.000002,0.75*fwidth(sourceP.z)),depthError);
float sourceWeight=sourceProjectionEnabled*sourceFront*sourceBounds*sourceVisible*texture2D(sourceMask,sourceUV).r;
outgoingLight=mix(outgoingLight,texture2D(sourceImage,sourceUV).rgb,sourceWeight);
#include <opaque_fragment>`);
          };
          cloned.customProgramCacheKey=()=> 'source-appearance-projection-v1';
          return cloned;
        };
        savedMaterials.push([mesh,mesh.material]);
        mesh.material=Array.isArray(mesh.material)?mesh.material.map(patch):patch(mesh.material);meshCount++;
      });
    }
    if(!meshCount)throw new Error('No real projection geometry');
    return {setDusk(on){enabled.value=on?0:1;},snapshot(){return {mode:'source_appearance',active:enabled.value===1,
      source_sha256:meta.source_sha256,object_ids:[...ids],mesh_count:meshCount,native_size:size,
      geometry_added:0,albedo_recovered:false,depth_occlusion:true};}};
  }catch(error){
    for(const [mesh,material] of savedMaterials){for(const m of Array.isArray(mesh.material)?mesh.material:[mesh.material])m.dispose();mesh.material=material;}
    for(const texture of textures){texture.image?.close?.();texture.dispose();}
    target?.dispose();throw error;
  }
}
