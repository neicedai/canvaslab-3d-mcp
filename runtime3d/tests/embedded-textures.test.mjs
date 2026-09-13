import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {inspectPng,inspectEmbeddedTextures,createEmbeddedTexturePlugin} from '../src/embedded-textures.js';
const fixture=JSON.parse(fs.readFileSync(new URL('./fixtures/embedded-png.json',import.meta.url),'utf8'));
function input(){return [structuredClone(fixture.document),new Uint8Array(Buffer.from(fixture.binary_base64,'base64'))];}
function resources(){return inspectEmbeddedTextures(...input());}
class Texture {constructor(image){this.image=image;this.disposed=false;}dispose(){this.disposed=true;}}
const T={Texture,SRGBColorSpace:'srgb',NearestFilter:1003,LinearFilter:1006,NearestMipmapNearestFilter:1004,LinearMipmapNearestFilter:1007,NearestMipmapLinearFilter:1005,LinearMipmapLinearFilter:1008,ClampToEdgeWrapping:1001,MirroredRepeatWrapping:1002,RepeatWrapping:1000};
test('canonical embedded PNG and float UVs survive the browser resource gate',()=>{
  const r=resources();assert.equal(r.images.length,1);assert.equal(r.pixels,4);
  assert.equal(r.images[0].width,2);assert.equal(r.images[0].height,2);
});
test('corrupt CRCs, missing chunks and trailing bytes fail before decode',()=>{
  const original=resources().images[0].bytes;
  for(const bytes of [original.slice(0,-1),new Uint8Array([...original,0]),original.slice()]){
    if(bytes.length===original.length)bytes[40]^=1;
    assert.throws(()=>inspectPng(bytes));
  }
});
test('external resource references cannot reach a loader',()=>{
  for(const uri of ['https://invalid.example/x.png','data:image/png;base64,AAAA','blob:untrusted']){
    const [d,b]=input();d.images[0].uri=uri;assert.throws(()=>inspectEmbeddedTextures(d,b));
  }
});
test('image views must be exclusive and inside the binary payload',()=>{
  for(const change of [{byteLength:999999},{byteOffset:0},{target:34962},{byteStride:4}]){
    const [d,b]=input();Object.assign(d.bufferViews.at(-1),change);assert.throws(()=>inspectEmbeddedTextures(d,b));
  }
});
test('textured primitives require finite correctly sized UVs',()=>{
  for(const change of [{count:7},{type:'VEC3'},{normalized:true},{byteOffset:2},{sparse:{}}]){
    const [d,b]=input();Object.assign(d.accessors[2],change);assert.throws(()=>inspectEmbeddedTextures(d,b));
  }
  const [d,b]=input();new DataView(b.buffer).setFloat32(d.bufferViews[2].byteOffset,NaN,true);
  assert.throws(()=>inspectEmbeddedTextures(d,b));
  delete d.meshes[0].primitives[0].attributes.TEXCOORD_0;assert.throws(()=>inspectEmbeddedTextures(d,b));
});
test('unsupported texture coordinates, references and sampler values fail closed',()=>{
  for(const change of [{index:99},{texCoord:1},{index:true},{extensions:{}}]){
    const [d,b]=input();Object.assign(d.materials[0].pbrMetallicRoughness.baseColorTexture,change);assert.throws(()=>inspectEmbeddedTextures(d,b));
  }
  const [d,b]=input();d.samplers=[{wrapS:0}];d.textures[0].sampler=0;assert.throws(()=>inspectEmbeddedTextures(d,b));
});
test('unused resources are rejected and shared image textures are charged separately',()=>{
  const [d,b]=input();d.textures.push({source:0});assert.throws(()=>inspectEmbeddedTextures(d,b));
  d.materials.push({pbrMetallicRoughness:{baseColorTexture:{index:1}}});
  d.meshes[0].primitives.push({...d.meshes[0].primitives[0],material:1});
  assert.equal(inspectEmbeddedTextures(d,b).pixels,8);
});
test('plugin decodes embedded bytes once and sets glTF orientation, colors and sampler',async()=>{
  let calls=0;const bitmap={width:2,height:2,closed:false,close(){this.closed=true;}};
  const parser={associations:new Map()},r=resources();r.samplers=[{wrapS:33071,magFilter:9728}];r.textures[0].sampler=0;
  const plugin=createEmbeddedTexturePlugin(T,parser,r,async(blob,options)=>{
    calls++;assert.equal(blob.type,'image/png');assert.equal(blob.size,r.images[0].bytes.length);
    assert.deepEqual(options,{imageOrientation:'none',premultiplyAlpha:'none',colorSpaceConversion:'none'});return bitmap;
  });
  const [a,b]=await Promise.all([plugin.loadTexture(0),plugin.loadTexture(0)]);
  assert.equal(a,b);assert.equal(calls,1);assert.equal(a.flipY,false);assert.equal(a.colorSpace,'srgb');
  assert.equal(a.wrapS,T.ClampToEdgeWrapping);assert.equal(a.magFilter,T.NearestFilter);assert.equal(a.needsUpdate,true);
  assert.deepEqual(parser.associations.get(a),{textures:0});await plugin.dispose();assert.equal(a.disposed,true);assert.equal(bitmap.closed,true);
});
test('different samplers share one decoded bitmap without sharing Texture state',async()=>{
  const r=resources();r.textures.push({source:0});let decoded=0,closed=0;
  const plugin=createEmbeddedTexturePlugin(T,{associations:new Map()},r,async()=>{decoded++;return {width:2,height:2,close(){closed++;}};});
  const [a,b]=await Promise.all([plugin.loadTexture(0),plugin.loadTexture(1)]);
  assert.notEqual(a,b);assert.equal(a.image,b.image);assert.equal(decoded,1);await plugin.dispose();assert.equal(closed,1);
});
test('decoder failures propagate and partial-load cleanup does not mask the error',async()=>{
  const plugin=createEmbeddedTexturePlugin(T,{associations:new Map()},resources(),async()=>{throw new Error('decode failed');});
  await assert.rejects(plugin.loadTexture(0),/decode failed/);await plugin.dispose();
});
test('unexpected decoded dimensions close the bitmap and reject',async()=>{
  let closed=false;const plugin=createEmbeddedTexturePlugin(T,{associations:new Map()},resources(),async()=>({width:3,height:2,close(){closed=true;}}));
  await assert.rejects(plugin.loadTexture(0),/dimensions changed/);assert.equal(closed,true);await plugin.dispose();
});
