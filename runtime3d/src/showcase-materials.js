import * as T from 'three';

// Only fixed, locally authored treatments are used. Component recipes cannot
// provide shader code, texture URLs or material programs.
const tiles=new Set(['66776a','758074','566959','839080','536558','4c5145','424e46','526155','38463e','617061','455648','3a4139']);
const panes=new Set(['977237','bf964f','6a512b']);
const lanterns=new Set(['d9482f','e16c3e']);
const plaster=new Set(['cbbd93','b4a480']);
const cloth=new Set(['a3ac89']);
// Warm interior paper/glass stays visibly lit beneath deep eaves even at noon.
// This applies only to authored pane palette surfaces, never timber lattice.
const PANE_DAY_EMISSION=.45,PANE_NIGHT_EMISSION=.95;

export function classifyCraftMaterial(material){
  if(!material?.isMeshStandardMaterial||material.map)return null;
  const hex=material.color.getHexString();
  if(lanterns.has(hex))return 'lantern';
  if(panes.has(hex))return 'pane';
  if(tiles.has(hex)||/tile/.test(material.name))return 'tile';
  if(plaster.has(hex))return 'plaster';
  if(cloth.has(hex))return 'cloth';
  if(/iron/.test(material.name))return 'metal';
  if(/stone/.test(material.name))return 'stone';
  const hsl=material.color.getHSL({});
  if(hsl.h>.16&&hsl.h<.43)return 'leaf';
  if(hsl.h>.86||hsl.h<.045&&hsl.l>.4)return 'petal';
  if(hsl.s<.24&&hsl.l>.24)return 'stone';
  if(hsl.h<.175)return 'wood';
  return 'stone';
}

const NOISE=`
varying vec3 vCraftPoint;
float craftHash(vec3 p){p=fract(p*.1031);p+=dot(p,p.yzx+33.33);return fract((p.x+p.y)*p.z);}
float craftNoise(vec3 p){vec3 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);
return mix(mix(mix(craftHash(i),craftHash(i+vec3(1,0,0)),f.x),mix(craftHash(i+vec3(0,1,0)),craftHash(i+vec3(1,1,0)),f.x),f.y),mix(mix(craftHash(i+vec3(0,0,1)),craftHash(i+vec3(1,0,1)),f.x),mix(craftHash(i+vec3(0,1,1)),craftHash(i+vec3(1,1,1)),f.x),f.y),f.z);}
`;

const SURFACES={
  wood:`float broad=craftNoise(p*vec3(7.0,1.3,9.0));
    float fine=craftNoise(p*vec3(94.0,3.7,68.0));
    float grain=.5+.5*sin(p.x*155.0+p.z*72.0+broad*11.0);
    float detail=1.0-smoothstep(.6,2.6,length(fwidth(p))*85.0);
    float age=craftNoise(p*vec3(3.2,.48,4.1));
    craftVariation=.68+.27*broad+.14*age+.10*fine-.16*pow(grain,8.0)*detail;
    craftHeight=(fine*.0013+grain*.0010)*detail;`,
  stone:`float broad=craftNoise(p*5.5),fine=craftNoise(p*105.0);
    float mineral=craftNoise(p*24.0);
    craftVariation=.77+.29*broad+.13*mineral+.07*fine;
    craftHeight=(broad-.5)*.012+(mineral-.5)*.003+(fine-.5)*.0016;`,
  plaster:`float broad=craftNoise(p*3.5),fine=craftNoise(p*82.0);
    float stain=craftNoise(p*vec3(8.0,.8,8.0));
    craftVariation=.80+.14*broad+.14*stain+.04*fine;
    craftHeight=(broad-.5)*.004+(fine-.5)*.0015;`,
  tile:`float broad=craftNoise(p*9.0),fine=craftNoise(p*112.0);
    float patina=craftNoise(p*vec3(23.0,3.0,8.0));
    craftVariation=.72+.23*broad+.18*patina+.075*fine;
    craftHeight=(broad-.5)*.0036+(fine-.5)*.0012;`,
  cloth:`float broad=craftNoise(p*6.0);
    float weave=sin(p.x*205.0)*sin(p.z*205.0);
    float detail=1.0-smoothstep(.5,2.4,length(fwidth(p))*140.0);
    craftVariation=.85+.18*broad+.045*weave*detail;
    craftHeight=weave*.0007*detail;`,
  leaf:`float broad=craftNoise(p*18.0);
    craftVariation=.85+.25*broad;
    craftHeight=0.0;`,
  petal:`craftVariation=.95+.065*craftNoise(p*28.0);craftHeight=0.0;`,
  lantern:`float rib=.5+.5*sin(atan(p.z,p.x)*22.0);
    craftVariation=.96+.04*rib;craftHeight=0.0;`,
  pane:`craftVariation=.92+.09*craftNoise(p*14.0);craftHeight=0.0;`,
  metal:`craftVariation=.9+.12*craftNoise(p*55.0);craftHeight=0.0;`
};

export function applyCraftSurface(material,family=classifyCraftMaterial(material)){
  if(!family||!SURFACES[family])return null;
  if(family==='wood')material.color.multiplyScalar(.84);
  if(family==='plaster')material.color.lerp(new T.Color('#f0e3c4'),.22);
  material.roughness=({wood:.76,stone:.93,plaster:.96,tile:.64,cloth:.98,leaf:.72,petal:.8,lantern:.69,pane:.46,metal:.51})[family];
  material.metalness=family==='metal'?.5:family==='tile'?.025:0;
  material.envMapIntensity=family==='tile'?.65:.42;
  if(family==='lantern'||family==='pane'){
    material.emissive.set(family==='lantern'?'#ff571f':'#ffb95d');
    material.emissiveIntensity=family==='lantern'?.22:PANE_DAY_EMISSION;
  }else if(family==='leaf'){
    material.emissive.copy(material.color).multiplyScalar(.06);
  }
  material.onBeforeCompile=shader=>{
    shader.vertexShader='varying vec3 vCraftPoint;\n'+shader.vertexShader;
    // Keep grain attached to movable components. World translation/rotation must
    // not make it swim, while the authored physical dimensions set its scale.
    shader.vertexShader=shader.vertexShader.replace('#include <begin_vertex>',`#include <begin_vertex>
      vCraftPoint=position*vec3(length(modelMatrix[0].xyz),length(modelMatrix[1].xyz),length(modelMatrix[2].xyz));`);
    shader.fragmentShader=NOISE+shader.fragmentShader;
    shader.fragmentShader=shader.fragmentShader.replace('#include <color_fragment>',`#include <color_fragment>
      vec3 p=vCraftPoint;float craftVariation=1.0,craftHeight=0.0;
      ${SURFACES[family]}
      diffuseColor.rgb*=craftVariation;`);
    shader.fragmentShader=shader.fragmentShader.replace('#include <roughnessmap_fragment>',`#include <roughnessmap_fragment>
      roughnessFactor=clamp(roughnessFactor+(1.0-craftVariation)*.18,.12,1.0);`);
    shader.fragmentShader=shader.fragmentShader.replace('#include <normal_fragment_maps>',`#include <normal_fragment_maps>
      vec3 craftDx=dFdx(-vViewPosition),craftDy=dFdy(-vViewPosition);
      vec3 craftR1=cross(craftDy,normal),craftR2=cross(normal,craftDx);
      float craftDet=dot(craftDx,craftR1);
      vec3 craftGradient=sign(craftDet)*(dFdx(craftHeight)*craftR1+dFdy(craftHeight)*craftR2);
      normal=normalize(max(abs(craftDet),1.e-12)*normal-craftGradient);`);
  };
  material.customProgramCacheKey=()=>`canvaslab-crafted-${family}-v2`;
  material.needsUpdate=true;
  return family;
}

// Find connected red lantern solids within a batched palette mesh. Lighting
// follows real generated geometry instead of hard-coded Qingyadu coordinates.
function lanternCenters(mesh,indices){
  const geometry=mesh.geometry,pos=geometry.getAttribute('position'),index=geometry.index;
  if(!pos||!index)return [];
  const parents=new Map(),points=new Map();
  const key=i=>`${Math.round(pos.getX(i)*1e5)},${Math.round(pos.getY(i)*1e5)},${Math.round(pos.getZ(i)*1e5)}`;
  function find(k){let root=k;while(parents.get(root)!==root)root=parents.get(root);while(k!==root){const next=parents.get(k);parents.set(k,root);k=next;}return root;}
  function touch(i){const k=key(i);if(!parents.has(k)){parents.set(k,k);points.set(k,new T.Vector3().fromBufferAttribute(pos,i));}return k;}
  const groups=geometry.groups.length?geometry.groups:[{start:0,count:index.count,materialIndex:0}];
  for(const group of groups){if(!indices.has(group.materialIndex))continue;
    for(let n=group.start;n<group.start+group.count;n+=3){const ids=[touch(index.getX(n)),touch(index.getX(n+1)),touch(index.getX(n+2))];parents.set(find(ids[1]),find(ids[0]));parents.set(find(ids[2]),find(ids[0]));}}
  const boxes=new Map();for(const [k,p] of points){const root=find(k);if(!boxes.has(root))boxes.set(root,new T.Box3());boxes.get(root).expandByPoint(p);}
  return [...boxes.values()].filter(box=>box.getSize(new T.Vector3()).length()>.005).map(box=>mesh.localToWorld(box.getCenter(new T.Vector3())));
}

export function enhanceShowcaseMaterials(nodes){
  const emitters=[],lamps=[],families={};let remainingLights=7;
  for(const node of nodes.values()){
    if(node.userData.spec.kind!=='asset')continue;
    node.updateWorldMatrix(true,true);
    const candidates=[];
    node.traverse(mesh=>{
      if(!mesh.isMesh)return;
      const materials=Array.isArray(mesh.material)?mesh.material:[mesh.material],red=new Set();
      materials.forEach((material,i)=>{
        const family=applyCraftSurface(material);families[family]=(families[family]||0)+1;
        if(family==='lantern'||family==='pane')emitters.push({material,family});
        if(material.color.getHexString()==='d9482f')red.add(i);
      });
      if(red.size)candidates.push(...lanternCenters(mesh,red));
    });
    // Evenly distribute a small number across the lanterns of each component.
    const count=Math.min(remainingLights,3,candidates.length);
    for(let i=0;i<count;i++){
      const center=candidates[Math.floor(i*candidates.length/count)];
      const lamp=new T.PointLight('#ffad54',1.1,3.1,2);
      lamp.position.copy(node.worldToLocal(center));lamp.position.y-=.025;
      node.add(lamp);lamps.push(lamp);remainingLights--;
    }
  }
  return {families,lightCount:lamps.length,setDusk(on){
    for(const {material,family} of emitters)material.emissiveIntensity=family==='lantern'?(on?1.45:.22):(on?PANE_NIGHT_EMISSION:PANE_DAY_EMISSION);
    for(const lamp of lamps)lamp.intensity=on?5.4:1.1;
  }};
}

export function createShowcaseEnvironment(renderer){
  const skyScene=new T.Scene();
  const skyMaterial=new T.ShaderMaterial({side:T.BackSide,vertexShader:`varying vec3 direction;
    void main(){direction=position;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`,
    fragmentShader:`varying vec3 direction;
    void main(){vec3 d=normalize(direction);float up=smoothstep(-.15,.8,d.y);
      vec3 color=mix(vec3(.46,.40,.29),vec3(.80,.88,.96),up);
      float sun=pow(max(dot(d,normalize(vec3(-.45,.8,.35))),0.0),120.0);
      color+=vec3(1.0,.82,.53)*sun*3.0;gl_FragColor=vec4(color,1.0);}`});
  const geometry=new T.SphereGeometry(20,24,12);skyScene.add(new T.Mesh(geometry,skyMaterial));
  const pmrem=new T.PMREMGenerator(renderer),result=pmrem.fromScene(skyScene,.08,.1,40);
  pmrem.dispose();geometry.dispose();skyMaterial.dispose();return result;
}
