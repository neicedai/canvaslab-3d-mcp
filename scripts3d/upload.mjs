import fs from 'node:fs/promises';
const [image,base='http://127.0.0.1:8031']=process.argv.slice(2);
if(!image||!process.env.CANVASLAB3D_TOKEN)throw new Error('Usage: set CANVASLAB3D_TOKEN, then node scripts3d/upload.mjs <original-image> [API-base]');
const bytes=await fs.readFile(image);
if(bytes.length>20*1024*1024)throw new Error('Image exceeds 20 MiB; do not retry with a thumbnail');
const response=await fetch(new URL('/assets',base),{method:'POST',headers:{'Authorization':`Bearer ${process.env.CANVASLAB3D_TOKEN}`,'Content-Type':'application/octet-stream'},body:bytes});
if(!response.ok)throw new Error(`Upload failed ${response.status}: ${await response.text()}`);
console.log(JSON.stringify(await response.json(),null,2));
