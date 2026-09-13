import * as T from 'three';
import {FullScreenQuad} from 'three/addons/postprocessing/Pass.js';
import {OutputPass} from 'three/addons/postprocessing/OutputPass.js';

// A bounded contact-occlusion pass reuses the beauty pass's real depth buffer.
// There is no second normal/geometry render and no image background masquerading
// as scene detail. Fixed sample locations keep exact-time captures reproducible.
export function createShowcaseComposite(renderer){
  // Hardware sample averaging is continuous. Threshold-based post AA can
  // amplify a one-code-value HDR rounding change into a visibly different edge.
  const target=new T.WebGLRenderTarget(1,1,{type:T.HalfFloatType,samples:Math.min(4,renderer.capabilities.maxSamples),depthBuffer:true,resolveDepthBuffer:true});
  target.depthTexture=new T.DepthTexture(1,1,T.UnsignedIntType);
  target.depthTexture.minFilter=T.NearestFilter;target.depthTexture.magFilter=T.NearestFilter;
  const uniforms={beauty:{value:target.texture},depthMap:{value:target.depthTexture},inverseProjection:{value:new T.Matrix4()},projectionScale:{value:1},perspective:{value:0},resolution:{value:new T.Vector2(1,1)}};
  const material=new T.ShaderMaterial({name:'CanvasLabContactComposite',uniforms,depthTest:false,depthWrite:false,
    vertexShader:`varying vec2 screenUv;void main(){screenUv=uv;gl_Position=vec4(position.xy,0.0,1.0);}`,
    fragmentShader:`uniform sampler2D beauty;uniform sampler2D depthMap;uniform mat4 inverseProjection;
      uniform vec2 resolution;uniform float projectionScale;uniform float perspective;varying vec2 screenUv;
      vec3 viewPoint(vec2 uv,float depth){vec4 p=inverseProjection*vec4(uv*2.0-1.0,depth*2.0-1.0,1.0);return p.xyz/p.w;}
      void main(){vec3 color=texture2D(beauty,screenUv).rgb;float depth=texture2D(depthMap,screenUv).x;
        // Derivatives must run in all lanes, including background lanes along
        // silhouettes. Their value is undefined inside a divergent depth branch.
        vec3 p=viewPoint(screenUv,depth),normal=normalize(cross(dFdx(p),dFdy(p)));
        if(depth<.999999){
          if(normal.z<0.0)normal=-normal;
          float radius=.26,pixels=clamp(radius*projectionScale*resolution.y*.5/mix(1.0,max(.1,-p.z),perspective),2.0,36.0);
          float occlusion=0.0;
          for(int i=0;i<12;i++){
            float f=float(i),angle=f*2.39996323,r=(.24+.76*(f+.5)/12.0)*pixels;
            vec2 uv=screenUv+vec2(cos(angle),sin(angle))*r/resolution;
            float sampleDepth=texture2D(depthMap,clamp(uv,vec2(.001),vec2(.999))).x;
            vec3 delta=viewPoint(uv,sampleDepth)-p;float distanceToSample=length(delta);
            float horizon=max(0.0,dot(normal,delta)/max(distanceToSample,.0001)-.12);
            occlusion+=horizon*(1.0-smoothstep(.025,radius,distanceToSample))*step(sampleDepth,.999999);
          }
          color*=clamp(1.0-occlusion*.24,.53,1.0);
        }
        gl_FragColor=vec4(color,1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`});
  const contactTarget=new T.WebGLRenderTarget(1,1,{type:T.HalfFloatType,depthBuffer:false});
  const outputPass=new OutputPass();
  outputPass.renderToScreen=true;
  outputPass.uniforms.backgroundDepth={value:target.depthTexture};
  outputPass.uniforms.preservedBackground={value:new T.Color()};
  outputPass.uniforms.backgroundLinear={value:new T.Color()};
  outputPass.material.fragmentShader=outputPass.material.fragmentShader
    .replace('uniform sampler2D tDiffuse;','uniform sampler2D tDiffuse; uniform sampler2D backgroundDepth; uniform vec3 preservedBackground; uniform vec3 backgroundLinear;')
    .replace('gl_FragColor = texture2D( tDiffuse, vUv );','gl_FragColor = texture2D( tDiffuse, vUv );vec3 backgroundRatio=gl_FragColor.rgb/max(backgroundLinear,vec3(.0001));')
    .replace(/\}\s*$/,'if(texture2D(backgroundDepth,vUv).r>=.999999){float attenuation=min(1.0,min(backgroundRatio.r,min(backgroundRatio.g,backgroundRatio.b))+.0005);gl_FragColor=vec4(preservedBackground*attenuation,1.0);}\n}');
  const quad=new FullScreenQuad(material);let width=0,height=0;
  return {render(scene,camera){
    const size=renderer.getDrawingBufferSize(new T.Vector2());
    if(size.x!==width||size.y!==height){width=size.x;height=size.y;target.setSize(width,height);contactTarget.setSize(width,height);uniforms.resolution.value.set(width,height);}
    uniforms.inverseProjection.value.copy(camera.projectionMatrixInverse);
    uniforms.projectionScale.value=camera.projectionMatrix.elements[5];uniforms.perspective.value=camera.isPerspectiveCamera?1:0;
    const previous=renderer.getRenderTarget();
    outputPass.uniforms.preservedBackground.value.copy(scene.background).convertLinearToSRGB();
    outputPass.uniforms.backgroundLinear.value.copy(scene.background);
    try{renderer.setRenderTarget(target);renderer.render(scene,camera);renderer.setRenderTarget(contactTarget);quad.render(renderer);outputPass.render(renderer,null,contactTarget);}
    finally{renderer.setRenderTarget(previous);}
  }};
}
