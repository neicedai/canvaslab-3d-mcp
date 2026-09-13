import test from 'node:test';
import assert from 'node:assert/strict';
import * as T from 'three';
import {classifyCraftMaterial, applyCraftSurface, enhanceShowcaseMaterials} from '../src/showcase-materials.js';
import {createObject} from '../src/primitives.js';
import {enhanceRiverWater} from '../src/river-water.js';

const standard = (color, name = '') => new T.MeshStandardMaterial({color, name, roughness: .83, metalness: .06});
const compile = material => {
  const shader = {vertexShader: T.ShaderLib.standard.vertexShader, fragmentShader: T.ShaderLib.standard.fragmentShader, uniforms: {}};
  material.onBeforeCompile(shader);
  return shader;
};

test('water geometry fits profile budget and preserves standard lily roughness', () => {
  for(const showcase of [false,true]){
    const spec={id:'water',kind:'water',dimensions:[15,.32,10.6],position:[0,0,0],rotation:[0,0,0,1],material_id:'water'};
    const node=createObject(spec,new Map([['water',standard('#438b84')]]),1977);
    enhanceRiverWater(node,1977,{showcase,reflection:showcase});
    let triangles=0;node.traverse(mesh=>{if(mesh.isMesh)triangles+=(mesh.geometry.index?.count??mesh.geometry.attributes.position.count)/3;});
    assert.ok(triangles<=(showcase?5000:2500),`actual triangles: ${triangles}`);
    const pads=node.children.filter(mesh=>mesh.geometry?.type==='CylinderGeometry');
    assert.equal(pads.length,42);assert.equal(pads[0].material.roughness,showcase?.78:.9);
  }
});

test('showcase classification reads authored palettes without changing standard materials', () => {
  for (const [color, name, family] of [
    ['#d9482f', '', 'lantern'], ['#bf964f', '', 'pane'],
    ['#66776a', '', 'tile'], ['#cbbd93', '', 'plaster'],
    ['#a3ac89', '', 'cloth'], ['#858b88', 'iron fittings', 'metal'],
    ['#b7b4a1', 'stone quay', 'stone'], ['#608844', '', 'leaf'],
    ['#ec9eb9', '', 'petal'], ['#75513c', '', 'wood']
  ]) {
    const material = standard(color, name), before = material.toJSON(), hook = material.onBeforeCompile;
    assert.equal(classifyCraftMaterial(material), family, `${color} ${name}`);
    assert.deepEqual(material.toJSON(), before);
    assert.equal(material.onBeforeCompile, hook);
  }
  const ordinary = standard('#75513c'); ordinary.map = new T.Texture();
  const before = ordinary.toJSON(), hook = ordinary.onBeforeCompile;
  assert.equal(applyCraftSurface(ordinary), null);
  assert.deepEqual(ordinary.toJSON(), before);
  assert.equal(ordinary.onBeforeCompile, hook);
  assert.equal(classifyCraftMaterial(new T.MeshBasicMaterial()), null);
});

test('fixed craft shader families have distinct programs and never request textures or code', () => {
  const keys = new Set();
  for (const family of ['wood', 'stone', 'plaster', 'tile', 'cloth', 'leaf', 'petal', 'lantern', 'pane', 'metal']) {
    const material = standard('#75513c');
    assert.equal(applyCraftSurface(material, family), family);
    keys.add(material.customProgramCacheKey());
    const shader = compile(material);
    assert.match(shader.fragmentShader, /craftVariation/);
    assert.match(shader.fragmentShader, /craftGradient/);
    assert.match(shader.vertexShader, /vCraftPoint=position\*/);
    assert.deepEqual(shader.uniforms, {});
    for (const key of Object.keys(material).filter(key => /map$/i.test(key))) assert.equal(material[key], null, key);
    assert.doesNotMatch(shader.vertexShader + shader.fragmentShader, /https?:\/\/|data:|sampler2D\s+craft/);
    assert.equal(material.transparent, false);
    assert.ok(Number.isFinite(material.roughness) && material.roughness >= 0 && material.roughness <= 1);
  }
  assert.equal(keys.size, 10);
});

test('craft grain stays on the component and independent material clones keep their state', () => {
  const source = standard('#75513c'), first = source.clone(), second = source.clone();
  applyCraftSurface(first, 'wood'); applyCraftSurface(second, 'wood');
  const program = compile(first);
  assert.deepEqual(compile(second), program);
  // Grain scales by the three basis-vector lengths, but never the world
  // translation column; moving an imported boat cannot shift its grain.
  assert.match(program.vertexShader, /length\(modelMatrix\[0\]\.xyz\)/);
  assert.match(program.vertexShader, /length\(modelMatrix\[1\]\.xyz\)/);
  assert.match(program.vertexShader, /length\(modelMatrix\[2\]\.xyz\)/);
  assert.doesNotMatch(program.vertexShader, /vCraftPoint\s*=.*modelMatrix\[3\]/);
  const root = new T.Group(), mesh = new T.Mesh(new T.BoxGeometry(), first);
  root.add(mesh); root.position.set(11, 7, -5); root.rotation.y = 1.2; root.updateMatrixWorld(true);
  assert.deepEqual(compile(first), program);
  first.emissiveIntensity = 4;
  assert.notEqual(first.emissiveIntensity, second.emissiveIntensity);
  assert.equal(source.roughness, .83);
  assert.equal(source.onBeforeCompile, T.Material.prototype.onBeforeCompile);
});

function lanternNode(id, index) {
  const node = new T.Group(); node.userData.spec = {kind: 'asset', id}; node.position.set(index * 2, 1, 0);
  const geometry = new T.BoxGeometry(.3, .5, .3);
  geometry.clearGroups(); // GLTFLoader creates single-material primitives this way.
  const mesh = new T.Mesh(geometry, standard('#d9482f')); mesh.position.set(.4, .8, .2);
  node.add(mesh); return node;
}

test('single-material imported lanterns receive bounded attached lights and reversible dusk materials', () => {
  const nodes = new Map(Array.from({length: 10}, (_, i) => [`lamp-${i}`, lanternNode(`lamp-${i}`, i)]));
  const originalMeshes = [...nodes.values()].map(node => node.children[0]);
  const materials = originalMeshes.map(mesh => mesh.material);
  const geometries = originalMeshes.map(mesh => mesh.geometry);
  const craft = enhanceShowcaseMaterials(nodes);
  assert.equal(craft.lightCount, 7);
  assert.equal(craft.families.lantern, 10);
  const lamps = [...nodes.values()].flatMap(node => node.children.filter(child => child.isPointLight));
  assert.equal(lamps.length, 7);
  for (const lamp of lamps) {
    assert.equal(lamp.castShadow, false);
    assert.ok(lamp.distance > 0 && lamp.distance <= 4);
    assert.deepEqual(lamp.position.toArray().map(n => +n.toFixed(3)), [.4, .775, .2]);
  }
  const daytime = materials.map(material => material.emissiveIntensity);
  craft.setDusk(true);
  assert.ok(materials.every((material, i) => material.emissiveIntensity > daytime[i]));
  craft.setDusk(false);
  assert.deepEqual(materials.map(material => material.emissiveIntensity), daytime);
  assert.deepEqual(originalMeshes.map(mesh => mesh.material), materials);
  assert.deepEqual(originalMeshes.map(mesh => mesh.geometry), geometries);
  const firstNode = nodes.values().next().value, lamp = lamps[0];
  firstNode.position.x += 3; firstNode.updateMatrixWorld(true);
  assert.equal(+lamp.getWorldPosition(new T.Vector3()).x.toFixed(3), 3.4);
});

test('warm window panes restore exact daylight while adjacent timber never emits light', () => {
  const node = new T.Group(); node.userData.spec = {kind: 'asset', id: 'window'};
  const pane = standard('#bf964f'), wood = standard('#75513c');
  node.add(new T.Mesh(new T.BoxGeometry(.5, .7, .04), pane));
  node.add(new T.Mesh(new T.BoxGeometry(.05, .9, .08), wood));
  const craft = enhanceShowcaseMaterials(new Map([['window', node]]));
  assert.equal(craft.families.pane, 1);
  assert.equal(craft.families.wood, 1);
  const daytime = pane.toJSON(), timber = wood.toJSON();
  assert.ok(pane.emissiveIntensity > .3);
  assert.notEqual(pane.emissive.getHex(), 0);
  assert.equal(wood.emissive.getHex(), 0);
  craft.setDusk(true);
  assert.ok(pane.emissiveIntensity > daytime.emissiveIntensity);
  assert.equal(wood.emissive.getHex(), 0);
  assert.deepEqual(wood.toJSON(), timber);
  craft.setDusk(false);
  assert.deepEqual(pane.toJSON(), daytime);
  assert.deepEqual(wood.toJSON(), timber);
});
