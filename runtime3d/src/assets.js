import * as T from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';

const SHA256 = /^[a-f0-9]{64}$/;
const NORMALIZED_MIN = [-0.5, 0, -0.5];
const NORMALIZED_MAX = [0.5, 1, 0.5];
const EPSILON = 1e-4;

export function componentFilename(assetId, manifest) {
  if (!SHA256.test(assetId ?? '')) throw new Error('Invalid component identity');
  const filename = `component-${assetId}.glb`;
  if (!Object.hasOwn(manifest.files ?? {}, filename) || manifest.files[filename] !== assetId) {
    throw new Error(`Component is not declared with its exact digest: ${assetId}`);
  }
  return filename;
}

// This intentionally supports only the managed worker's static, opaque PBR
// subset. Inspect before GLTFLoader runs: no URI, texture loader, extension
// resolver, animation or executable payload may silently expand that contract.
export function inspectComponentGlb(buffer) {
  // Absolute transport ceiling matches managed showcase assets. The server
  // still enforces the smaller standard-profile geometry and byte budgets.
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength < 24 || buffer.byteLength > 64 * 1024 * 1024) {
    throw new Error('Invalid or oversized GLB component');
  }
  const view = new DataView(buffer);
  if (view.getUint32(0, true) !== 0x46546c67 || view.getUint32(4, true) !== 2 || view.getUint32(8, true) !== buffer.byteLength) {
    throw new Error('Component must be a complete GLB version 2 file');
  }
  let offset = 12, document = null, binaryLength = null;
  while (offset < buffer.byteLength) {
    if (offset + 8 > buffer.byteLength) throw new Error('Truncated GLB chunk');
    const length = view.getUint32(offset, true), type = view.getUint32(offset + 4, true);
    offset += 8;
    if (length % 4 || offset + length > buffer.byteLength) throw new Error('Invalid GLB chunk length');
    if (type === 0x4e4f534a && document === null && binaryLength === null) {
      document = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(new Uint8Array(buffer, offset, length)));
    } else if (type === 0x004e4942 && document !== null && binaryLength === null) {
      binaryLength = length;
    } else throw new Error('Unsupported or repeated GLB chunk');
    offset += length;
  }
  if (document?.asset?.version !== '2.0' || binaryLength === null || document.buffers?.length !== 1) {
    throw new Error('Component must use one embedded GLB buffer');
  }
  const declared = document.buffers[0].byteLength;
  if (!Number.isSafeInteger(declared) || declared < 1 || declared > binaryLength || binaryLength - declared > 3) {
    throw new Error('Invalid embedded component buffer');
  }
  for (const field of ['images', 'textures', 'skins', 'animations', 'cameras', 'extensionsUsed', 'extensionsRequired']) {
    if (document[field] !== undefined && (!Array.isArray(document[field]) || document[field].length !== 0)) {
      throw new Error(`Unsupported component feature: ${field}`);
    }
  }
  function inspect(value) {
    if (!value || typeof value !== 'object') return;
    for (const [key, item] of Object.entries(value)) {
      if (['uri', 'extensions', 'targets', 'weights'].includes(key) || key.endsWith('Texture')) {
        throw new Error(`Unsupported component resource or feature: ${key}`);
      }
      if (key === 'alphaMode' && item !== 'OPAQUE') throw new Error('Only opaque component materials are supported');
      inspect(item);
    }
  }
  inspect(document);
  return document;
}

export function validateNormalizedComponent(scene) {
  let meshes = 0;
  scene.updateMatrixWorld(true);
  scene.traverse(node => {
    if (!node.matrixWorld.elements.every(Number.isFinite)) throw new Error('Nonfinite component transform');
    if (node.isSkinnedMesh || node.isCamera || node.isLight) throw new Error('Component must contain only static meshes');
    if (!node.isMesh) return;
    meshes++;
    const position = node.geometry?.getAttribute('position');
    if (!position || position.count < 3 || !Array.from(position.array).every(Number.isFinite)) {
      throw new Error('Component has missing or nonfinite mesh positions');
    }
  });
  if (!meshes) throw new Error('Component has no visible geometry');
  const box = new T.Box3().setFromObject(scene, true);
  const minimum = box.min.toArray(), maximum = box.max.toArray();
  if (![...minimum, ...maximum].every(Number.isFinite) || minimum.some((v, i) => Math.abs(v - NORMALIZED_MIN[i]) > EPSILON) || maximum.some((v, i) => Math.abs(v - NORMALIZED_MAX[i]) > EPSILON)) {
    throw new Error('Component is not normalized to a bottom-centered unit volume');
  }
  return {normalized_box: [minimum, maximum], mesh_count: meshes};
}

export function instantiateComponent(template, spec, digest = spec.asset_id) {
  if (!SHA256.test(spec.asset_id ?? '') || digest !== spec.asset_id) throw new Error('Component instance identity mismatch');
  if (!Array.isArray(spec.dimensions) || spec.dimensions.length !== 3 || spec.dimensions.some(v => !Number.isFinite(v) || v <= 0)) {
    throw new Error('Component dimensions must be finite and positive');
  }
  const metadata = validateNormalizedComponent(template);
  const instance = template.clone(true);
  instance.traverse(node => {
    if (!node.isMesh) return;
    node.geometry = node.geometry.clone();
    node.material = Array.isArray(node.material) ? node.material.map(m => m.clone()) : node.material.clone();
    node.userData.objectId = spec.id;
    node.castShadow = true;
    node.receiveShadow = true;
  });
  // The dimensions transform belongs to content, not the semantic object root.
  // A child attached through parent_id therefore does not inherit this scale.
  const content = new T.Group();
  content.name = `component-content-${spec.id}`;
  content.scale.fromArray(spec.dimensions);
  content.add(instance);
  const root = new T.Group();
  root.name = spec.id;
  root.position.fromArray(spec.position);
  root.quaternion.fromArray(spec.rotation);
  root.add(content);
  root.userData.spec = spec;
  root.userData.component = {...metadata, asset_id: digest, dimensions: [...spec.dimensions]};
  return root;
}

export function componentInstanceInfo(root) {
  if (!root.userData.component) return null;
  const materialIds = [], geometryIds = [], objectIds = [];
  root.children[0].traverse(node => {
    if (!node.isMesh) return;
    objectIds.push(node.userData.objectId);
    geometryIds.push(node.geometry.uuid);
    materialIds.push(...(Array.isArray(node.material) ? node.material : [node.material]).map(m => m.uuid));
  });
  return {...root.userData.component, object_id: root.name, semantic_ids_valid: objectIds.every(id => id === root.name), mesh_count: objectIds.length, material_ids: [...new Set(materialIds)], geometry_ids: [...new Set(geometryIds)]};
}

export async function loadComponentTemplates(objects, manifest, fetchImpl = fetch) {
  const templates = new Map();
  for (const assetId of new Set(objects.filter(o => o.kind === 'asset').map(o => o.asset_id))) {
    const filename = componentFilename(assetId, manifest);
    const response = await fetchImpl(`./${filename}`, {redirect: 'error', credentials: 'same-origin'});
    if (!response.ok) throw new Error(`Missing component: ${assetId}`);
    const buffer = await response.arrayBuffer();
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', buffer)), b => b.toString(16).padStart(2, '0')).join('');
    if (digest !== assetId) throw new Error(`Changed component bytes: ${assetId}`);
    inspectComponentGlb(buffer);
    const manager = new T.LoadingManager();
    manager.setURLModifier(() => {throw new Error('Component resource loading is forbidden');});
    const gltf = await new GLTFLoader(manager).parseAsync(buffer, '');
    if (gltf.animations.length) throw new Error('Embedded component animations are not supported');
    validateNormalizedComponent(gltf.scene);
    templates.set(assetId, gltf.scene);
  }
  return templates;
}
