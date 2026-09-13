import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash, webcrypto} from 'node:crypto';
import * as T from 'three';
import {componentFilename, inspectComponentGlb, validateNormalizedComponent, instantiateComponent, componentInstanceInfo, loadComponentTemplates} from '../src/assets.js';

if (!globalThis.crypto) globalThis.crypto = webcrypto;
const assetId = 'a'.repeat(64);
const spec = (id = 'first') => ({id, kind: 'asset', asset_id: assetId, position: [0, 0, 0], rotation: [0, 0, 0, 1], dimensions: [3, 2, 4]});
function template() {
  const group = new T.Group(), mesh = new T.Mesh(new T.BoxGeometry(1, 1, 1), new T.MeshStandardMaterial({color: '#aABB33'}));
  mesh.position.y = .5;
  group.add(mesh);
  return group;
}
function glb(mutate = () => {}) {
  const positions = new Float32Array([-.5, 0, -.5, .5, 0, -.5, .5, 1, -.5, -.5, 1, -.5, -.5, 0, .5, .5, 0, .5, .5, 1, .5, -.5, 1, .5]);
  const binary = new Uint8Array(positions.buffer);
  const document = {asset: {version: '2.0'}, scene: 0, scenes: [{nodes: [0]}], nodes: [{mesh: 0}], meshes: [{primitives: [{attributes: {POSITION: 0}, material: 0}]}], materials: [{pbrMetallicRoughness: {baseColorFactor: [.4, .5, .6, 1], roughnessFactor: .8, metallicFactor: 0}}], buffers: [{byteLength: binary.byteLength}], bufferViews: [{buffer: 0, byteOffset: 0, byteLength: binary.byteLength}], accessors: [{bufferView: 0, componentType: 5126, count: 8, type: 'VEC3', min: [-.5, 0, -.5], max: [.5, 1, .5]}]};
  mutate(document);
  const encoded = new TextEncoder().encode(JSON.stringify(document)), jsonLength = Math.ceil(encoded.length / 4) * 4;
  const buffer = new ArrayBuffer(12 + 8 + jsonLength + 8 + binary.length), view = new DataView(buffer), bytes = new Uint8Array(buffer);
  view.setUint32(0, 0x46546c67, true);view.setUint32(4, 2, true);view.setUint32(8, buffer.byteLength, true);
  view.setUint32(12, jsonLength, true);view.setUint32(16, 0x4e4f534a, true);
  bytes.fill(32, 20, 20 + jsonLength);bytes.set(encoded, 20);
  view.setUint32(20 + jsonLength, binary.length, true);view.setUint32(24 + jsonLength, 0x004e4942, true);bytes.set(binary, 28 + jsonLength);
  return buffer;
}
const sha = data => createHash('sha256').update(new Uint8Array(data)).digest('hex');
const manifestFor = digest => ({files: {[`component-${digest}.glb`]: digest}});

test('component assets must be flat manifest-declared SHA filenames', () => {
  assert.equal(componentFilename(assetId, manifestFor(assetId)), `component-${assetId}.glb`);
  assert.throws(() => componentFilename('../model', manifestFor(assetId)), /identity/);
  assert.throws(() => componentFilename(assetId, {files: {}}), /declared/);
  assert.throws(() => componentFilename(assetId, {files: {[`component-${assetId}.glb`]: 'b'.repeat(64)}}), /digest/);
});

test('GLB resource inspection rejects unsupported and externally referenced content', () => {
  assert.equal(inspectComponentGlb(glb()).asset.version, '2.0');
  for (const mutation of [d => d.buffers[0].uri = 'https://example.com/mesh.bin', d => d.images = [{uri: 'data:image/png;base64,AA=='}], d => d.extensionsUsed = ['KHR_draco_mesh_compression'], d => d.nodes[0].extensions = {}, d => d.animations = [{}], d => d.materials[0].alphaMode = 'BLEND', d => d.meshes[0].primitives[0].targets = [{}], d => d.materials[0].pbrMetallicRoughness.baseColorTexture = {index: 0}]) {
    assert.throws(() => inspectComponentGlb(glb(mutation)), /Unsupported|opaque/);
  }
  assert.throws(() => inspectComponentGlb(glb().slice(0, -4)), /complete/);
  assert.throws(() => inspectComponentGlb(new ArrayBuffer(64 * 1024 * 1024 + 1)), /oversized/);
});

test('normalized component validation rejects nonfinite or misplaced geometry', () => {
  const valid = template();
  assert.equal(validateNormalizedComponent(valid).mesh_count, 1);
  const shifted = template();shifted.position.x = .01;
  assert.throws(() => validateNormalizedComponent(shifted), /normalized/);
  const flat = template();flat.scale.z = 0;
  assert.throws(() => validateNormalizedComponent(flat), /normalized/);
  const bad = template();bad.children[0].geometry.attributes.position.array[0] = NaN;
  assert.throws(() => validateNormalizedComponent(bad), /nonfinite/);
});

test('instances isolate material and geometry ownership while keeping authored colors', () => {
  const source = template(), a = instantiateComponent(source, spec('first')), b = instantiateComponent(source, spec('second'));
  const find = root => {let result;root.traverse(n => {if (n.isMesh) result = n;});return result;};
  const am = find(a), bm = find(b), sm = find(source);
  assert.notEqual(am.material, bm.material);assert.notEqual(am.geometry, bm.geometry);
  assert.equal(am.material.color.getHex(), sm.material.color.getHex());
  am.material.color.set('red');am.geometry.attributes.position.array[0] = 100;
  assert.notEqual(bm.material.color.getHex(), am.material.color.getHex());
  assert.notEqual(bm.geometry.attributes.position.array[0], 100);
  assert.notEqual(sm.geometry.attributes.position.array[0], 100);
  assert.equal(am.userData.objectId, 'first');assert.equal(bm.userData.objectId, 'second');
  assert.equal(componentInstanceInfo(b).semantic_ids_valid, true);
});

test('asset dimension scaling does not scale semantic children attached by parent_id', () => {
  const root = instantiateComponent(template(), spec());
  const size = new T.Box3().setFromObject(root).getSize(new T.Vector3());
  assert.deepEqual(size.toArray(), [3, 2, 4]);assert.deepEqual(root.scale.toArray(), [1, 1, 1]);
  const child = new T.Group();child.position.set(1, 2, 3);root.add(child);root.updateMatrixWorld(true);
  assert.deepEqual(child.getWorldPosition(new T.Vector3()).toArray(), [1, 2, 3]);
  assert.throws(() => instantiateComponent(template(), {...spec(), dimensions: [1, NaN, 1]}), /finite/);
});

test('loader verifies exact bytes and parses a local static GLB once per asset ID', async () => {
  const buffer = glb(), digest = sha(buffer), calls = [];
  const templates = await loadComponentTemplates([{kind: 'asset', asset_id: digest}, {kind: 'asset', asset_id: digest}], manifestFor(digest), async (url, options) => {
    calls.push({url, options});return {ok: true, arrayBuffer: async () => buffer};
  });
  assert.equal(calls.length, 1);assert.equal(calls[0].url, `./component-${digest}.glb`);
  assert.equal(calls[0].options.redirect, 'error');assert.equal(templates.size, 1);
  assert.equal(validateNormalizedComponent(templates.get(digest)).mesh_count, 1);
  await assert.rejects(loadComponentTemplates([{kind: 'asset', asset_id: assetId}], manifestFor(assetId), async () => ({ok: true, arrayBuffer: async () => buffer})), /Changed component bytes/);
});

test('loader rejects embedded resource references before invoking any resource loader', async () => {
  const buffer = glb(d => d.buffers[0].uri = 'http://127.0.0.1/private'), digest = sha(buffer);
  let requests = 0;
  await assert.rejects(loadComponentTemplates([{kind: 'asset', asset_id: digest}], manifestFor(digest), async () => {requests++;return {ok: true, arrayBuffer: async () => buffer};}), /resource/);
  assert.equal(requests, 1);
});
