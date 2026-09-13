import test from 'node:test';
import assert from 'node:assert/strict';
import * as T from 'three';
import {createObject} from '../src/primitives.js';

test('declared sign uses exactly 14 triangles for solid board and text face', () => {
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const lettering=[];
  const context = {fillRect() {}, fillText(...args) {lettering.push(args);}};
  const canvas = {getContext: type => {
    assert.equal(type, '2d');
    return context;
  }};
  Object.defineProperty(globalThis, 'document', {configurable: true, value: {
    createElement(tag) {
      assert.equal(tag, 'canvas');
      return canvas;
    }
  }});
  try {
    const materials = new Map([['wood', new T.MeshStandardMaterial({color: '#75513c'})]]);
    const sign = createObject({id: 'inn-sign', kind: 'sign', text: '青崖渡',
      position: [0, 0, 0], rotation: [0, 0, 0, 1], dimensions: [1.4, 0.4, 0.1],
      material_id: 'wood'}, materials, 1);
    let triangles = 0, meshes = 0;
    sign.traverse(node => {
      if (!node.isMesh) return;
      meshes++;
      const positions = node.geometry.getAttribute('position');
      assert.ok([...positions.array].every(Number.isFinite));
      triangles += (node.geometry.index?.count ?? positions.count) / 3;
      assert.equal(node.userData.objectId, 'inn-sign');
    });
    // Both server budget paths use this real mesh cost, not the generic 2500 estimate.
    assert.equal(meshes, 2);
    assert.equal(triangles, 14);
    const size = new T.Box3().setFromObject(sign).getSize(new T.Vector3());
    assert.ok(size.x > 0 && size.y > 0 && size.z > 0);
    assert.equal(lettering[0][0], '青崖渡');
    createObject({id:'side-sign',kind:'sign',text:'客栈',position:[0,0,0],
      rotation:[0,0,0,1],dimensions:[.45,1.17,.05],material_id:'wood'},materials,1);
    assert.equal(lettering[1][0], '客');assert.equal(lettering[2][0], '栈');
    assert.equal(lettering[1][1],lettering[2][1]);
    assert.ok(lettering[1][2]<lettering[2][2]);
    assert.ok(canvas.height>canvas.width&&canvas.height<=1024);
  } finally {
    if (originalDocument) Object.defineProperty(globalThis, 'document', originalDocument);
    else delete globalThis.document;
  }
});
