import test from 'node:test';
import assert from 'node:assert/strict';
import * as T from 'three';
import {createObject,objectRandom} from '../src/primitives.js';
const mats=new Map([['a',new T.MeshStandardMaterial({color:0xffffff})]]);
test('stable per-object seed unaffected by other objects',()=>{const a=objectRandom(1,'tree');const expected=[a(),a()];const b=objectRandom(1,'other');b();b();const c=objectRandom(1,'tree');assert.deepEqual([c(),c()],expected);});
for(const kind of ['box','cylinder','sphere','building','roof','boat','tree','dock','water'])test(`${kind}: finite nondegenerate geometry and semantic identity`,()=>{
  const g=createObject({id:'sample',kind,position:[0,0,0],rotation:[0,0,0,1],dimensions:[3,2,2],material_id:'a',floors:2,canopy:true},mats,5);
  let count=0;g.traverse(n=>{if(n.isMesh){count++;assert.equal(n.userData.objectId,'sample');const pos=n.geometry.getAttribute('position');assert.ok([...pos.array].every(Number.isFinite));}});
  assert.ok(count>0);const size=new T.Box3().setFromObject(g).getSize(new T.Vector3());assert.ok(size.x>0&&size.y>0&&size.z>0);
});
