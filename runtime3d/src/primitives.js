import * as T from 'three';

// Every semantic object keeps its own Group. Cosmetic parts may be repeated, but
// cannot consume object IDs or become independently draggable fragments.
export function objectRandom(seed,id){let n=seed>>>0;for(const c of id)n=Math.imul(n^c.charCodeAt(0),16777619)>>>0;return()=>{n=(Math.imul(1664525,n)+1013904223)>>>0;return n/4294967296;};}
export function createObject(spec,materials,seed){
  const g=new T.Group(),[w,h,d]=spec.dimensions,m=materials.get(spec.material_id),a=materials.get(spec.accent_material_id)||m;
  const random=objectRandom(seed,spec.id);
  function add(geo,mat,x=0,y=0,z=0){const mesh=new T.Mesh(geo,mat);mesh.position.set(x,y,z);mesh.castShadow=true;mesh.receiveShadow=true;mesh.userData.objectId=spec.id;g.add(mesh);return mesh;}
  const box=(x,y,z,bw,bh,bd,mat=m)=>add(new T.BoxGeometry(bw,bh,bd),mat,x,y,z);
  const cylinder=(x,y,z,r,height,mat=m,rt=r)=>add(new T.CylinderGeometry(rt,r,height,12),mat,x,y,z);
  function roof(y,rw,rh,rd){
    for(const side of [-1,1]){
      const cols=12,rows=8,verts=[],indices=[];
      for(let i=0;i<=cols;i++)for(let j=0;j<=rows;j++){
        const v=j/rows;
        verts.push((i/cols-.5)*rw,y+rh*(1-Math.pow(v,.65))+.08*rh*v**8,side*v*rd/2);
      }
      for(let i=0;i<cols;i++)for(let j=0;j<rows;j++){const k=i*(rows+1)+j;indices.push(k,k+1,k+rows+1,k+1,k+rows+2,k+rows+1);}
      const geo=new T.BufferGeometry();geo.setAttribute('position',new T.Float32BufferAttribute(verts,3));geo.setIndex(indices);geo.computeVertexNormals();
      add(geo,a);
      // Raised seams make the curved roof legible without image billboards.
      for(let i=0;i<=cols;i++){
        const points=[];for(let j=0;j<=rows;j++){const v=j/rows;points.push(new T.Vector3((i/cols-.5)*rw,y+rh*(1-Math.pow(v,.65))+.08*rh*v**8+.015,side*v*rd/2));}
        add(new T.TubeGeometry(new T.CatmullRomCurve3(points),12,.022,4,false),a);
      }
    }
    box(0,y+rh+.025,0,rw+.1,.08,.09,a);
  }
  switch(spec.kind){
    case 'sign':{
      box(0,h/2,0,w,h,d);
      const paper=document.createElement('canvas'),ratio=w/h;
      paper.width=Math.min(1024,Math.max(256,Math.round(768*Math.min(1,ratio))));
      paper.height=Math.min(1024,Math.max(256,Math.round(paper.width/ratio)));
      const ctx=paper.getContext('2d');ctx.fillStyle='#'+m.color.getHexString();ctx.fillRect(0,0,paper.width,paper.height);
      ctx.fillStyle='#'+a.color.getHexString();ctx.textAlign='center';ctx.textBaseline='middle';
      const letters=Array.from(spec.text),vertical=ratio<.7&&letters.length>1;
      const fontSize=vertical?Math.min(paper.width*.72,paper.height*.82/letters.length):Math.min(paper.height*.72,paper.width*.88/letters.length);
      ctx.font=`${Math.round(fontSize)}px KaiTi, STKaiti, serif`;
      if(vertical)letters.forEach((letter,i)=>ctx.fillText(letter,paper.width/2,paper.height*(.1+.8*(i+.5)/letters.length),paper.width*.88));
      else ctx.fillText(spec.text,paper.width/2,paper.height*.53,paper.width*.9);
      const map=new T.CanvasTexture(paper);map.colorSpace=T.SRGBColorSpace;
      const face=add(new T.PlaneGeometry(w*.96,h*.94),new T.MeshStandardMaterial({map,roughness:.9}),0,h/2,d/2+.002);
      face.castShadow=false;break;
    }
    case 'box':box(0,h/2,0,w,h,d);if(spec.movement)for(const sx of [-1,1])box(sx*w*.43,h/2,d*.505,.06,h,.035,a);break;
    case 'water':{
      box(0,h/2,0,w,h,d);
      for(let i=0;i<24;i++){const line=box((random()-.5)*w*.9,h+.012,(random()-.5)*d*.9,.12+random()*.3,.01,.025,a);line.userData.ripple=i;}
      break;
    }
    case 'cylinder':cylinder(0,h/2,0,w/2,h,m).scale.z=d/w;break;
    case 'sphere':{const mesh=add(new T.IcosahedronGeometry(1,2),m,0,h/2,0);mesh.scale.set(w/2,h/2,d/2);break;}
    case 'roof':roof(0,w,h,d);break;
    case 'building':{
      const wall=h*.68;
      box(0,wall/2,0,w*.88,wall,d*.82);
      for(let floor=0;floor<spec.floors;floor++){
        const bottom=floor*wall/spec.floors;
        for(const sx of [-1,1])for(const sz of [-1,1])box(sx*w*.45,wall/2,sz*d*.43,.11,wall,.11,a);
        box(0,bottom+.06,d*.43,w,.12,.12,a);
        for(let j=0;j<5;j++){
          const x=(j-2)*w*.16,wy=bottom+wall/spec.floors*.53;
          box(x,wy,d*.425,w*.13,wall/spec.floors*.47,.025,a);
          for(let k=0;k<3;k++)box(x+(k-1)*w*.035,wy,d*.45,.025,wall/spec.floors*.43,.03,m);
        }
        box(0,bottom+wall/spec.floors-.04,0,w*.96,.1,d*.9,a);
      }
      if(spec.roof_enabled!==false)roof(wall,w*1.06,h*.32,d*1.1);break;
    }
    case 'dock':{
      const count=Math.min(40,Math.max(4,Math.ceil(d/.2)));
      for(let i=0;i<count;i++)box(0,h*.8,-d/2+(i+.5)*d/count,w,h*.18,d/count*.94,i%4===0?a:m);
      for(const x of [-w*.42,w*.42])for(const z of [-d*.42,d*.42])cylinder(x,h*.5,z,.055,h,a);
      break;
    }
    case 'boat':{
      // Hull is a closed, volumetric polygonal shell, not a rectangular card.
      const verts=[],idx=[],rings=[[-.5,.03,.38],[-.34,.42,.12],[0,.5,.05],[.34,.42,.12],[.5,.03,.38]];
      for(const [x,half,rise] of rings)verts.push(x*w,rise*h,-half*d,x*w,rise*h,half*d,x*w,(rise+.3)*h,-half*d,x*w,(rise+.3)*h,half*d);
      for(let i=0;i<rings.length-1;i++){const q=i*4,n=q+4;idx.push(q,n,q+1,q+1,n,n+1,q,q+2,n,q+2,n+2,n,q+1,n+1,q+3,q+3,n+1,n+3,q+2,q+3,n+2,q+3,n+3,n+2);}
      idx.push(0,1,2,1,3,2,16,18,17,17,18,19);
      const geo=new T.BufferGeometry();geo.setAttribute('position',new T.Float32BufferAttribute(verts,3));geo.setIndex(idx);geo.computeVertexNormals();add(geo,m);
      box(0,h*.35,0,w*.67,.05,d*.75,a);
      for(const z of [-d*.47,d*.47])box(0,h*.43,z,w*.6,.06,.06,a);
      if(spec.canopy){
        const canopy=add(new T.CylinderGeometry(d*.43,d*.43,w*.4,16,1,true,0,Math.PI),a,0,h*.42,0);
        canopy.rotation.z=Math.PI/2;
        for(const x of [-w*.21,w*.21]){
          const pts=[];for(let i=0;i<=16;i++){const angle=i/16*Math.PI;pts.push(new T.Vector3(x,h*.42+Math.sin(angle)*d*.43,Math.cos(angle)*d*.43));}
          add(new T.TubeGeometry(new T.CatmullRomCurve3(pts),16,.028,5,false),m);
        }
      }
      break;
    }
    case 'tree':{
      cylinder(0,h*.28,0,w*.07,h*.56,a,w*.045);
      for(let i=0;i<9;i++){
        const mesh=add(new T.IcosahedronGeometry(1,0),m,(random()-.5)*w*.53,h*(.55+random()*.25),(random()-.5)*d*.53);
        mesh.scale.set(w*(.22+random()*.15),h*(.15+random()*.12),d*(.22+random()*.15));
        mesh.rotation.set(random(),random(),random());
      }
      break;
    }
  }
  g.position.fromArray(spec.position);g.quaternion.fromArray(spec.rotation);g.name=spec.id;g.userData.spec=spec;
  return g;
}
