import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveAppearance,referenceLightState,REFERENCE_LIGHTING_DEFAULTS} from '../src/reference-appearance.js';
const analysis={scene_box:[10,20,1200,800]};
test('legacy profiles retain their framing and showcase opt-in',()=>{
  assert.equal(resolveAppearance({},analysis).framingAspect,1.45);
  assert.equal(resolveAppearance({},analysis).stylized,false);
  assert.equal(resolveAppearance({render_quality:'showcase'},analysis).stylized,true);
});
test('reference quality never enables decorative style or changes framing',()=>{
  const standard=resolveAppearance({appearance_mode:'reference',render_quality:'standard'},analysis);
  const showcase=resolveAppearance({appearance_mode:'reference',render_quality:'showcase'},analysis);
  assert.deepEqual(standard,showcase);assert.equal(standard.stylized,false);assert.equal(standard.framingAspect,1.5);
});
test('portrait crops use their source aspect instead of a landscape constant',()=>{
  assert.equal(resolveAppearance({appearance_mode:'reference'},{scene_box:[0,0,600,1200]}).framingAspect,.5);
});
test('authored lighting is copied without changing the signed plan or defaults',()=>{
  const plan={appearance_mode:'reference',reference_lighting:{sun_position:[2,3,4],exposure:.7}};
  const prior=structuredClone(plan), appearance=resolveAppearance(plan,analysis);
  assert.equal(appearance.lighting.exposure,.7);appearance.lighting.sun_position[0]=99;
  assert.deepEqual(plan,prior);assert.equal(REFERENCE_LIGHTING_DEFAULTS.sun_position[0],-8);
});
test('dusk is marked inferred and day restores exact authored values',()=>{
  const lighting={...REFERENCE_LIGHTING_DEFAULTS,sun_intensity:4,hemisphere_intensity:2,sun_color:'#abcdef'};
  const dusk=referenceLightState(lighting,true), day=referenceLightState(lighting,false);
  assert.equal(dusk.variant,'inferred-dusk');assert.equal(dusk.sun_intensity,1);
  assert.equal(day.variant,'reference');assert.equal(day.sun_intensity,4);assert.equal(day.sun_color,'#abcdef');
  assert.equal(lighting.sun_intensity,4);
});
test('unknown modes and malformed crop ratios fail instead of silently falling back',()=>{
  assert.throws(()=>resolveAppearance({appearance_mode:'magical'},analysis));
  for(const box of [[0,0,1,0],[0,0,-1,1],[0,0,NaN,1]])assert.throws(()=>resolveAppearance({},{scene_box:box}));
});
