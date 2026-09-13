/** Managed embedded PNG subset. No fetch(), URLs, extensions or general imports. */
export const MAX_TEXTURE_PIXELS = 16 * 1024 * 1024;
export const MAX_SCENE_TEXTURE_PIXELS = 32 * 1024 * 1024;
const fail = message => {throw new Error(`Invalid embedded texture: ${message}`);};
const integer = (value, min, max) => {
  if (!Number.isSafeInteger(value) || value < min || value > max) fail('invalid integer or reference');
  return value;
};
const array = value => {
  if (!Array.isArray(value) || value.length > 8) fail('array budget exceeded');
  return value;
};
const object = (value, allowed) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('object required');
  for (const key of Object.keys(value)) if (!allowed.includes(key)) fail(`unsupported field ${key}`);
  if ('name' in value && (typeof value.name !== 'string' || value.name.length > 256)) fail('invalid name');
  return value;
};

const crcTable = Uint32Array.from({length: 256}, (_, n) => {
  for (let i = 0; i < 8; i++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1;
  return n >>> 0;
});
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = crcTable[(crc ^ byte) & 255] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

export function inspectPng(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.length < 45 || bytes.length > 8 * 1024 * 1024) fail('PNG byte budget');
  if (![137,80,78,71,13,10,26,10].every((n, i) => bytes[i] === n)) fail('PNG signature');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = 8, width = 0, height = 0, count = 0, idat = false, ended = false, srgb = false;
  while (offset < bytes.length) {
    if (++count > 256 || offset + 12 > bytes.length || ended) fail('PNG chunk framing');
    const length = view.getUint32(offset), start = offset + 8, end = start + length;
    if (end + 4 > bytes.length) fail('PNG chunk range');
    const kind = String.fromCharCode(...bytes.subarray(offset + 4, start));
    if (crc32(bytes.subarray(offset + 4, end)) !== view.getUint32(end)) fail('PNG CRC');
    if (count === 1) {
      if (kind !== 'IHDR' || length !== 13) fail('PNG IHDR');
      width = integer(view.getUint32(start), 1, 4096);height = integer(view.getUint32(start + 4), 1, 4096);
      if (bytes[start + 8] !== 8 || ![2, 6].includes(bytes[start + 9]) || bytes[start + 10] || bytes[start + 11] || bytes[start + 12]) fail('PNG must be non-interlaced 8-bit RGB(A)');
    } else if (kind === 'sRGB' && !srgb && !idat) {
      if (length !== 1 || bytes[start] > 3) fail('PNG sRGB intent');srgb = true;
    } else if (kind === 'IDAT') {
      if (!length) fail('empty PNG IDAT');idat = true;
    } else if (kind === 'IEND') {
      if (length || !idat || end + 4 !== bytes.length) fail('PNG trailing bytes');ended = true;
    } else fail('unsupported PNG chunk');
    offset = end + 4;
  }
  if (!ended) fail('PNG missing IEND');
  // The server additionally validates the complete, bounded zlib scanline stream.
  // Browser decode is still required and errors are fatal, never a silent fallback.
  return {bytes, width, height, pixels: width * height};
}

export function inspectEmbeddedTextures(document, binary) {
  const images = array(document.images ?? []), textures = array(document.textures ?? []), samplers = array(document.samplers ?? []);
  const views = document.bufferViews ?? [], accessors = document.accessors ?? [];
  const imageViews = new Set();
  const resources = images.map(image => {
    object(image, ['name', 'mimeType', 'bufferView']);
    if (image.mimeType !== 'image/png') fail('only embedded image/png');
    const vi = integer(image.bufferView, 0, views.length - 1), view = views[vi];
    if (imageViews.has(vi) || 'target' in view || 'byteStride' in view || view.buffer !== 0) fail('image bufferView policy');
    const start = integer(view.byteOffset ?? 0, 0, binary.length), size = integer(view.byteLength, 1, 8 * 1024 * 1024);
    if (start + size > binary.length) fail('image outside BIN');
    views.forEach((other, i) => {
      const at = integer(other.byteOffset ?? 0, 0, binary.length), length = integer(other.byteLength, 1, binary.length);
      if (at + length > binary.length) fail('bufferView outside BIN');
      if (i !== vi && Math.max(start, at) < Math.min(start + size, at + length)) fail('image overlaps another bufferView');
    });
    imageViews.add(vi);
    return inspectPng(binary.subarray(start, start + size));
  });
  const choices = {magFilter: [9728,9729], minFilter: [9728,9729,9984,9985,9986,9987], wrapS: [33071,33648,10497], wrapT: [33071,33648,10497]};
  for (const sampler of samplers) {
    object(sampler, ['name', ...Object.keys(choices)]);
    for (const [key, allowed] of Object.entries(choices)) if (key in sampler && !allowed.includes(sampler[key])) fail('sampler value');
  }
  const imageIds = new Set(), samplerIds = new Set();let pixels = 0;
  for (const texture of textures) {
    object(texture, ['name', 'source', 'sampler']);
    const source = integer(texture.source, 0, images.length - 1);imageIds.add(source);
    pixels += resources[source].pixels;
    if ('sampler' in texture) samplerIds.add(integer(texture.sampler, 0, samplers.length - 1));
  }
  if (pixels > MAX_TEXTURE_PIXELS || imageIds.size !== images.length || samplerIds.size !== samplers.length) fail('texture budget or unused resources');
  const materialTextures = new Map(), usedTextures = new Set(), checkedUvs = new Set();
  (document.materials ?? []).forEach((material, index) => {
    const info = material.pbrMetallicRoughness?.baseColorTexture;
    if (info === undefined) return;
    object(info, ['index', 'texCoord']);integer(info.texCoord ?? 0, 0, 0);
    materialTextures.set(index, integer(info.index, 0, textures.length - 1));
  });
  for (const mesh of document.meshes ?? []) for (const primitive of mesh.primitives ?? []) {
    const attributes = primitive.attributes ?? {}, ui = attributes.TEXCOORD_0;
    if (materialTextures.has(primitive.material)) {
      if (ui === undefined) fail('textured primitive requires TEXCOORD_0');
      usedTextures.add(materialTextures.get(primitive.material));
    }
    if (ui === undefined) continue;
    const uv = accessors[integer(ui, 0, accessors.length - 1)];
    const position = accessors[integer(attributes.POSITION, 0, accessors.length - 1)];
    if (uv.componentType !== 5126 || uv.type !== 'VEC2' || uv.count !== position.count || (uv.normalized ?? false) !== false || uv.sparse !== undefined) fail('invalid TEXCOORD_0 accessor');
    const vi = integer(uv.bufferView, 0, views.length - 1), view = views[vi];
    if (imageViews.has(vi) || view.buffer !== 0 || (view.target ?? 34962) !== 34962) fail('invalid TEXCOORD_0 view');
    const offset = integer(uv.byteOffset ?? 0, 0, binary.length), count = integer(uv.count, 1, 1000000);
    const stride = integer(view.byteStride ?? 8, 8, 252), start = integer(view.byteOffset ?? 0, 0, binary.length) + offset;
    if (stride % 4 || offset % 4 || start % 4 || offset + (count - 1) * stride + 8 > view.byteLength || start + (count - 1) * stride + 8 > binary.length) fail('TEXCOORD_0 range or alignment');
    if (!checkedUvs.has(ui)) {
      const values = new DataView(binary.buffer, binary.byteOffset, binary.byteLength);
      for (let i = 0; i < count; i++) for (let axis = 0; axis < 2; axis++) {
        const value = values.getFloat32(start + i * stride + axis * 4, true);
        if (!Number.isFinite(value) || Math.abs(value) > 10000) fail('nonfinite or unbounded TEXCOORD_0');
      }
      checkedUvs.add(ui);
    }
  }
  if (usedTextures.size !== textures.length) fail('unused textures');
  return {images: resources, textures, samplers, pixels};
}

export function createEmbeddedTexturePlugin(T, parser, resources, decode = globalThis.createImageBitmap) {
  const bitmaps = new Map(), textures = new Map();
  function bitmap(index) {
    if (!bitmaps.has(index)) {
      const image = resources.images[index];
      bitmaps.set(index, Promise.resolve().then(async () => {
        if (typeof decode !== 'function') fail('this browser cannot decode managed image bitmaps');
        const result = await decode(new Blob([image.bytes], {type: 'image/png'}), {
          imageOrientation: 'none', premultiplyAlpha: 'none', colorSpaceConversion: 'none',
        });
        if (result.width !== image.width || result.height !== image.height) {result.close();fail('decoded PNG dimensions changed');}
        return result;
      }));
    }
    return bitmaps.get(index);
  }
  const filters = {9728: T.NearestFilter,9729: T.LinearFilter,9984: T.NearestMipmapNearestFilter,9985: T.LinearMipmapNearestFilter,9986: T.NearestMipmapLinearFilter,9987: T.LinearMipmapLinearFilter};
  const wrapping = {33071: T.ClampToEdgeWrapping,33648: T.MirroredRepeatWrapping,10497: T.RepeatWrapping};
  return {
    name: 'CANVASLAB_embedded_png',
    loadTexture(index) {
      integer(index, 0, resources.textures.length - 1);
      if (!textures.has(index)) textures.set(index, (async () => {
        const spec = resources.textures[index], sampler = resources.samplers[spec.sampler] ?? {};
        const texture = new T.Texture(await bitmap(spec.source));
        texture.flipY = false;texture.colorSpace = T.SRGBColorSpace;
        texture.magFilter = filters[sampler.magFilter ?? 9729];texture.minFilter = filters[sampler.minFilter ?? 9987];
        texture.wrapS = wrapping[sampler.wrapS ?? 10497];texture.wrapT = wrapping[sampler.wrapT ?? 10497];
        texture.name = spec.name ?? '';texture.needsUpdate = true;
        parser.associations.set(texture, {textures: index});return texture;
      })());
      return textures.get(index);
    },
    async dispose() {
      // Called on parse failure only. Successful textures live with the scene.
      for (const result of await Promise.allSettled(textures.values())) if (result.status === 'fulfilled') result.value.dispose();
      for (const result of await Promise.allSettled(bitmaps.values())) if (result.status === 'fulfilled') result.value.close();
    },
  };
}
