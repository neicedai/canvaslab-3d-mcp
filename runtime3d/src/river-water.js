import * as T from 'three';
import {Reflector} from 'three/addons/objects/Reflector.js';

// Fixed rendering treatment for this bounded presentation. No caller shaders.
export function enhanceRiverWater(node, seed=73,{showcase=false,reflection=false}={}){
  const time={value:0};let n=seed;
  const random=()=>{n=(Math.imul(n,1664525)+1013904223)>>>0;return n/4294967296;};
  const surface=node.children[0],material=surface.material.clone();surface.material=material;
  material.roughness=showcase?.24:.32;material.metalness=showcase?.03:.1;
  if(showcase){
    material.color.set('#549485');material.transparent=true;material.opacity=.80;material.depthWrite=true;
    material.onBeforeCompile=shader=>{
      shader.uniforms.riverTime=time;shader.uniforms.waterSurfaceY={value:node.position.y+node.userData.spec.dimensions[1]};
      shader.vertexShader='varying vec3 vRiverPoint; varying vec3 vRiverNormal;\n'+shader.vertexShader;
      shader.vertexShader=shader.vertexShader.replace('#include <worldpos_vertex>','#include <worldpos_vertex>\nvRiverPoint=(modelMatrix*vec4(transformed,1.0)).xyz; vRiverNormal=normalize(mat3(modelMatrix)*normal);');
      shader.fragmentShader='uniform float riverTime;uniform float waterSurfaceY; varying vec3 vRiverPoint; varying vec3 vRiverNormal;\n'+shader.fragmentShader;
      shader.fragmentShader=shader.fragmentShader.replace('#include <color_fragment>',`#include <color_fragment>
        float depthBelow=max(0.0,waterSurfaceY-vRiverPoint.y);
        float waterBand=sin(vRiverPoint.x*4.7+vRiverPoint.z*4.1+sin(vRiverPoint.z*2.9-riverTime*.32)+depthBelow*8.0);
        float crossBand=sin(vRiverPoint.x*2.3-vRiverPoint.z*6.2+cos(vRiverPoint.x*1.9+riverTime*.27)-depthBelow*12.0);
        float focus=pow(max(0.0,.52+.26*waterBand+.22*crossBand),8.0);
        diffuseColor.rgb*=.82+.18*exp(-depthBelow*2.4);
        diffuseColor.rgb=mix(diffuseColor.rgb,vec3(.39,.64,.52),focus*.40);
        diffuseColor.rgb+=vec3(.016,.03,.024)*exp(-depthBelow*14.0);`);
    };
    // Remove the old solid dash placeholders. The authored water volume remains
    // real geometry; the small wave highlights are now a continuous surface.
    for(const child of [...node.children])if(child.userData.ripple!==undefined){node.remove(child);child.geometry.dispose();}
    material.customProgramCacheKey=()=> 'canvaslab-river-volume-showcase-v2';
    if(reflection)addShowcaseReflection(node,time);
  }else material.onBeforeCompile=shader=>{
    shader.uniforms.riverTime=time;
    shader.vertexShader='varying vec3 vRiverPoint; varying vec3 vRiverNormal;\n'+shader.vertexShader;
    shader.vertexShader=shader.vertexShader.replace('#include <worldpos_vertex>','#include <worldpos_vertex>\nvRiverPoint=(modelMatrix*vec4(transformed,1.0)).xyz; vRiverNormal=normalize(mat3(modelMatrix)*normal);');
    shader.fragmentShader=`uniform float riverTime; varying vec3 vRiverPoint; varying vec3 vRiverNormal;
      vec2 riverHash(vec2 p){return fract(sin(vec2(dot(p,vec2(127.1,311.7)),dot(p,vec2(269.5,183.3))))*43758.5453);}
      float riverCells(vec2 p){vec2 cell=floor(p),f=fract(p);float first=9.0,second=9.0;
        for(int y=-1;y<=1;y++)for(int x=-1;x<=1;x++){vec2 g=vec2(float(x),float(y));vec2 o=riverHash(cell+g);o=.5+.38*sin(riverTime*.18+6.2831*o);vec2 r=g+o-f;float d=dot(r,r);if(d<first){second=first;first=d;}else if(d<second){second=d;}}
        return second-first;
      }
      `+shader.fragmentShader;
    shader.fragmentShader=shader.fragmentShader.replace('#include <color_fragment>',`#include <color_fragment>
      vec2 p=vRiverPoint.xz*vec2(1.8,3.5);
      p+=vec2(sin(p.y*1.1+riverTime*.2),cos(p.x*.8-riverTime*.16))*.65;
      float edge=(1.0-smoothstep(.02,.18,riverCells(p)))*smoothstep(.6,.9,vRiverNormal.y);
      float swell=.5+.5*sin(p.x*.73+p.y*1.31+sin(p.y*1.2));
      diffuseColor.rgb*=.87+swell*.18;
      diffuseColor.rgb=mix(diffuseColor.rgb,vec3(.64,.82,.77),edge*(.035+swell*.11));
    `);
  };
  if(!showcase)material.customProgramCacheKey=()=> 'canvaslab-river-cells-v2';
  const colors=['#859858','#a5af71','#687f49'];
  for(let i=0;i<42;i++){
    const pad=new T.Mesh(new T.CylinderGeometry(.05+random()*.11,.05+random()*.11,.008,showcase?28:10),new T.MeshStandardMaterial({color:colors[i%3],roughness:showcase?.78:.9}));
    pad.position.set(-6.4+random()*3.0,node.userData.spec.dimensions[1]+.022,1.2+random()*2.4);
    pad.scale.z=.6+random()*.3;pad.rotation.y=random()*6.28;pad.userData.objectId=node.userData.spec.id;pad.receiveShadow=true;node.add(pad);
  }
  node.userData.updateWater=t=>{time.value=t;};
}

const WATER_SHADER={
  name:'CanvasLabShowcaseWater',
  uniforms:{color:{value:new T.Color('#458f87')},tDiffuse:{value:null},textureMatrix:{value:new T.Matrix4()},riverTime:{value:0},riverNight:{value:0}},
  vertexShader:`uniform mat4 textureMatrix;varying vec4 reflectionUv;varying vec3 waterPosition;varying vec3 worldPosition;
    void main(){reflectionUv=textureMatrix*vec4(position,1.0);waterPosition=position;
      worldPosition=(modelMatrix*vec4(position,1.0)).xyz;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`,
  fragmentShader:`uniform vec3 color;uniform sampler2D tDiffuse;uniform float riverTime;uniform float riverNight;
    varying vec4 reflectionUv;varying vec3 waterPosition;varying vec3 worldPosition;
    float waterHash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
    float waterNoise(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);
      return mix(mix(waterHash(i),waterHash(i+vec2(1,0)),f.x),mix(waterHash(i+vec2(0,1)),waterHash(i+vec2(1,1)),f.x),f.y);}
    void main(){
      vec2 p=waterPosition.xy;
      vec2 q=p+vec2(sin(p.y*1.3+sin(p.x*.8)),cos(p.x*1.1+cos(p.y*.7)))*.24;
      float a=dot(p,vec2(1.2,4.2))+riverTime*.54;
      float b=dot(p,vec2(-3.3,1.3))-riverTime*.43;
      float phase=sin(p.x*1.21+p.y*.71)*1.8+sin(p.y*2.87-p.x*.91)*1.3;
      float c=dot(q,vec2(5.1,9.4))+sin(p.y*1.7+riverTime*.2)+phase;
      float d=dot(q,vec2(-15.3,7.9))+sin(p.x*2.3-riverTime*.31)+sin(p.x*3.13+p.y*1.63)*2.2;
      float e=dot(q,vec2(26.3,16.1))+cos(p.y*4.7+riverTime*.44)+phase*1.7;
      // Most normal energy belongs to broad river swells. Fine ripples add
      // restrained sparkle, not the equally bright all-over carpet of flecks.
      vec2 slope=vec2(cos(a)*.14-cos(b)*.12+cos(c)*.055-cos(d)*.021+cos(e)*.011,cos(a)*.17+cos(b)*.09+cos(c)*.07+cos(d)*.013+cos(e)*.008);
      vec2 uv=reflectionUv.xy/reflectionUv.w+slope*.026;
      vec3 reflectionColor=texture2D(tDiffuse,clamp(uv,vec2(.002),vec2(.998))).rgb*vec3(.48,.74,.65);
      // The reflection is a real clipped mirrored camera. A low-frequency
      // distortion and restrained Fresnel retain readable, moving highlights.
      vec3 normal=normalize(vec3(-slope.x,1.0,slope.y));
      vec3 view=normalize(cameraPosition-worldPosition);
      float fresnel=.13+.29*pow(1.0-max(dot(normal,view),0.0),3.0);
      float broad=waterNoise(p*vec2(.72,1.1)+vec2(riverTime*.025,0.0));
      vec3 body=color*(.58+.48*broad)*(1.0-riverNight*.67);
      vec3 result=mix(body,reflectionColor,fresnel);
      float wave1=sin(c+sin(b)*1.2),wave2=sin(d+sin(c*.4));
      float caustic=pow(.5+.5*wave1,26.0)*smoothstep(.15,.75,wave2);
      result+=vec3(.39,.61,.51)*caustic*.12*(1.0-riverNight*.9);
      // Discontinuous elongated crests are deliberately sparse. Their metre
      // scale reads as river ripples at the native reference view, not a small
      // stipple/foam texture laid uniformly over the whole plane.
      float crestPhase=p.y*14.0+waterNoise(p*vec2(1.4,2.2)+vec2(riverTime*.03,0.0))*8.0-riverTime*.65;
      float crest=pow(.5+.5*sin(crestPhase),24.0);
      float fragments=smoothstep(.40,.64,waterNoise(p*vec2(4.1,6.3)+vec2(17.3,5.1)));
      result+=vec3(.44,.62,.57)*crest*fragments*.28*(1.0-riverNight*.96);
      vec3 halfVector=normalize(view+normalize(vec3(-.45,.8,.35)));
      float glint=pow(max(dot(normal,halfVector),0.0),110.0);
      result+=vec3(1.0,.93,.75)*glint*.82*(1.0-riverNight*.97);
      gl_FragColor=vec4(result,1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`
};

function addShowcaseReflection(node,time){
  const [w,h,d]=node.userData.spec.dimensions;
  const water=new Reflector(new T.PlaneGeometry(w,d),{textureWidth:1024,textureHeight:1024,multisample:0,clipBias:.00001,shader:WATER_SHADER,color:'#3d8178'});
  water.name='crafted-river-reflection';water.rotation.x=-Math.PI/2;water.position.y=h+.006;
  water.userData.objectId=node.userData.spec.id;water.castShadow=false;water.receiveShadow=false;
  water.material.uniforms.riverTime=time;
  const renderReflection=water.onBeforeRender;let previousKey='';
  water.onBeforeRender=function(renderer,scene,camera){
    if(scene.userData.canvaslabDiagnostic||scene.userData.canvaslabReflectionPass)return;
    const key=[...camera.matrixWorld.elements,...camera.projectionMatrix.elements,time.value,scene.userData.canvaslabWaterRevision||0,water.material.uniforms?.riverNight?.value||0].join(',');
    if(key===previousKey)return;
    // One bounded reflection per changed view/time/light/object state. Static
    // interaction frames reuse it; diagnostic passes never enter this renderer.
    const target=renderer.getRenderTarget(),xr=renderer.xr.enabled,auto=renderer.shadowMap.autoUpdate;
    // A camera mirrored below the surface must not see the opaque underside of
    // the water volume: it would occlude all reflected buildings and boats.
    const volume=node.children[0],volumeVisible=volume.visible;volume.visible=false;
    scene.userData.canvaslabReflectionPass=true;
    try{renderReflection.call(water,renderer,scene,camera);previousKey=key;}
    finally{volume.visible=volumeVisible;water.visible=true;renderer.xr.enabled=xr;renderer.shadowMap.autoUpdate=auto;renderer.setRenderTarget(target);scene.userData.canvaslabReflectionPass=false;}
  };
  node.add(water);
  node.userData.setWaterDusk=on=>{water.material.uniforms.riverNight.value=on?1:0;};
}
