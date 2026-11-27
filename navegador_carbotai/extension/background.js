// Carbotai: sólo órdenes locales, nunca scripts en páginas visitadas.
const FACE = 'http://localhost:5173/';
const HOME = 'https://www.google.com/';
let lastNormal = null;
let port = null;
let queue = Promise.resolve();
const knownSites = new Set(['www.google.com','www.youtube.com','es.wikipedia.org','github.com','www.facebook.com']);
function faceUrl(s) {
  try { const u = new URL(s); return u.protocol==='http:' && ['localhost','127.0.0.1','[::1]'].includes(u.hostname) && u.port==='5173'; }
  catch (_) { return false; }
}
function faceTab(t) { return faceUrl(t.url)||faceUrl(t.pendingUrl); }
function faceWindow(w) { return (w.tabs||[]).some(faceTab); }
function faceTabs(ws) {
  return ws.flatMap(w=>(w.tabs||[]).filter(faceTab).map(t=>({w,t})));
}
// Chromium cambia el estado de una *ventana*, nunca de una pestaña.
// Separar la cara de otras pestañas sin cerrarlas ni abrir un rostro duplicado.
async function ensureDedicatedFace(ws) {
  const faces=faceTabs(ws);
  if(faces.length!==1) throw Error(faces.length ?
    'Hay más de una pestaña del rostro. Ciérralas manualmente hasta dejar una.' :
    'No encontré la pestaña del rostro.');
  const {w,t}=faces[0];
  if((w.tabs||[]).length===1) return {window:w,moved:false};
  const created=await chrome.windows.create({tabId:t.id,type:'normal',focused:false});
  const isolated=await chrome.windows.get(created.id,{populate:true});
  if(!faceWindow(isolated)||(isolated.tabs||[]).length!==1)
    throw Error('No pude separar la pestaña del rostro de manera segura.');
  return {window:isolated,moved:true};
}

function normal(w) { return w.type==='normal' && !w.incognito && !faceWindow(w) && (w.tabs||[]).length>0 && (w.tabs||[]).every(t=>typeof t.url==='string'||typeof t.pendingUrl==='string'); }
function validUrl(s) {
  if (s==='chrome://newtab/'||s==='about:blank') return 'chrome://newtab/';
  const u=new URL(s);
  const host=u.hostname.toLowerCase();
  // Navegación HTTPS normal a sitios públicos, incluidas fuentes oficiales.
  // Jamás localhost, IP privadas, otras rutas locales ni scripts arbitrarios.
  if (u.protocol!=='https:'||u.username||u.password||u.port||
      !host.includes('.')||host==='localhost'||host.endsWith('.local')||
      host.endsWith('.internal')||/^\d+\.\d+\.\d+\.\d+$/.test(host))
    throw Error('Dirección no permitida.');
  return u.href;
}
async function snapshot() { return (await chrome.windows.getAll({populate:true,windowTypes:['normal','popup','app']})).filter(w=>!w.incognito); }
function choose(ws) {
  const all=ws.filter(normal); const active=all.find(w=>w.focused);
  if(active) return active;
  const previous=all.find(w=>w.id===lastNormal);
  return previous || (all.length===1?all[0]:null);
}
async function freshNormal(id) {
  const w=await chrome.windows.get(id,{populate:true});
  if(!normal(w)) throw Error('La ventana cambió o contiene el rostro. No la controlaré.');
  return w;
}
async function operate(m) {
  const ws=await snapshot(); const faces=ws.filter(faceWindow);
  if(m.op==='status') {
    const w=choose(ws); const t=w?.tabs.find(t=>t.active);
    let site=null;
    try {
      const host=new URL(t?.pendingUrl||t?.url).hostname.toLowerCase();
      if(host==='www.youtube.com'||host==='youtube.com') site='youtube';
      else if(host.endsWith('.wikipedia.org')) site='wikipedia';
      else if(host==='github.com') site='github';
      else if(host==='www.google.com'||host==='google.com') site='google';
    }catch(_){}
    return {ok:true,face:faces.length,face_tabs:faceTabs(ws).length,face_shared:faceTabs(ws).filter(x=>x.w.tabs.length>1).length,normal:ws.filter(normal).length,youtube:site==='youtube',site,selected:!!w};
  }
  if(m.op==='face_isolate') {
    const result=await ensureDedicatedFace(ws);
    return {ok:true,isolated:true,moved:result.moved};
  }
  if(m.op==='face_open') {
    if(faces.length) {
      const result=await ensureDedicatedFace(ws);
      return {ok:true,existing:true,isolated:true,moved:result.moved};
    }
    const face=await chrome.windows.create({url:FACE,type:'popup',focused:m.focused===true});
    // Reapertura MAXIMIZADA, no pantalla completa; no duplica el rostro.
    await chrome.windows.update(face.id,{state:'maximized'});
    const actual=await chrome.windows.get(face.id,{populate:true});
    if(!faceWindow(actual)||(actual.tabs||[]).length!==1 || actual.state!=='maximized')
      throw Error('No se confirmó la recuperación maximizada del rostro.');
    return {ok:true,maximized:true};
  }
  if(m.op==='web') {
    const url=validUrl(m.url);
    if(!['ventana','pestana','actual'].includes(m.mode)) throw Error('Modo inválido.');
    let w=choose(ws);
    if(m.mode==='ventana'||(!w&&!ws.some(normal))) {
      const created=await chrome.windows.create({url,type:'normal',focused:true}); lastNormal=created.id;
      return {ok:true,mode:'ventana'};
    }
    if(!w) throw Error(ws.some(normal)?'No pude identificar la última ventana normal. Selecciona una ventana del navegador.':'Abre primero una ventana normal del navegador.');
    w=await freshNormal(w.id);
    if(w.state==='minimized') await chrome.windows.update(w.id,{state:'normal'});
    await chrome.windows.update(w.id,{focused:true});
    w=await freshNormal(w.id);
    if(m.mode==='pestana') await chrome.tabs.create({windowId:w.id,url,active:true});
    else {
      const t=w.tabs.find(t=>t.active);
      if(!t||faceTab(t)) throw Error('No hay una pestaña normal segura.');
      await chrome.tabs.update(t.id,{url,active:true});
    }
    lastNormal=w.id; return {ok:true,mode:m.mode};
  }
  if(m.op==='site_search') {
    if(m.site!=='youtube') throw Error('Buscador de sitio no autorizado.');
    const url=validUrl(m.url);
    const parsed=new URL(url);
    if(parsed.hostname!=='www.youtube.com'||parsed.pathname!=='/results')
      throw Error('Solicitud no corresponde a una búsqueda de YouTube.');
    const windows=ws.filter(normal);
    const matches=windows.flatMap(w=>(w.tabs||[])
      .filter(t=>{try{return new URL(t.pendingUrl||t.url).hostname==='www.youtube.com';}catch(_){return false;}})
      .map(t=>({w,t})));
    const selected=choose(ws);
    let target=matches.find(x=>x.w.id===selected?.id&&x.t.active);
    if(!target) target=matches.find(x=>x.t.active);
    if(!target && matches.length===1) target=matches[0];
    if(matches.length>1&&!target)
      throw Error('Hay varias pestañas de YouTube. Selecciona la que deseas utilizar.');
    if(target) {
      const w=await freshNormal(target.w.id);
      if(!w.tabs.some(t=>t.id===target.t.id)) throw Error('La pestaña cambió.');
      if(w.state==='minimized') await chrome.windows.update(w.id,{state:'normal'});
      await chrome.tabs.update(target.t.id,{url,active:true});
      await chrome.windows.update(w.id,{focused:true});
      lastNormal=w.id;
      return {ok:true,mode:'actual',existing:true};
    }
    // Sin ninguna pestaña de YouTube, crear una pestaña/ventana nueva;
    // no modificar ChatGPT ni otras páginas abiertas.
    if(!selected) {
      if(windows.length) throw Error('Selecciona una ventana normal del navegador.');
      const made=await chrome.windows.create({url,type:'normal',focused:true});
      lastNormal=made.id;return {ok:true,mode:'ventana',existing:false};
    }
    await freshNormal(selected.id);
    await chrome.tabs.create({windowId:selected.id,url,active:true});
    await chrome.windows.update(selected.id,{focused:true});
    lastNormal=selected.id;
    return {ok:true,mode:'pestana',existing:false};
  }
  if(m.op==='tab_close') {
    const w=choose(ws);
    if(!w) throw Error('No hay ventana normal del navegador seleccionada.');
    const safe=await freshNormal(w.id);
    const site=m.site||'actual';
    const byTopic=typeof site==='string' && site.startsWith('tema:');
    if(!['actual','youtube','google','navegador','chromium'].includes(site)&&!byTopic)
      throw Error('No identifiqué la pestaña solicitada.');
    let candidates=safe.tabs.filter(t=>!faceTab(t));
    if(byTopic) {
      const topic=site.slice(5).normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim();
      if(!topic || topic.length>65 || !/^[a-z0-9 ñ]+$/.test(topic))
        throw Error('Nombre de búsqueda no válido.');
      candidates=candidates.filter(t=>{
        try {
          const u=new URL(t.pendingUrl||t.url);
          const q=(u.hostname==='www.google.com'?u.searchParams.get('q'):
                   u.hostname==='www.youtube.com'?u.searchParams.get('search_query'):null);
          return q && q.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase().includes(topic);
        }catch(_){return false;}
      });
    }
    if(site==='youtube'||site==='google') {
      candidates=candidates.filter(t=>{
        try {return new URL(t.pendingUrl||t.url).hostname===
          (site==='youtube'?'www.youtube.com':'www.google.com');}
        catch(_){return false;}
      });
    }
    if(byTopic && candidates.length>1)
      throw Error('Encontré varias pestañas con ese tema. Selecciona una o usa su pestaña activa.');
    let chosen=candidates.find(t=>t.active);
    if(!chosen && candidates.length===1) chosen=candidates[0];
    if(!chosen) throw Error(candidates.length?'Varias pestañas coinciden. Selecciona primero la que deseas cerrar.':'No encontré esa pestaña.');
    // Nunca cerrar una pestaña del rostro aunque deje de estar activa.
    if(faceTab(chosen)) throw Error('No cerraré el rostro del robot.');
    await chrome.tabs.remove(chosen.id);
    if(lastNormal!==w.id)lastNormal=w.id;
    return {ok:true};
  }
  if(m.op==='window') {
    const valid=['minimiza','maximiza','restaura','completa','cierra'];
    if(!valid.includes(m.action)||!['face','browser'].includes(m.target)) throw Error('Orden inválida.');
    let w;
    if(m.target==='face') {
      if(m.action==='cierra') throw Error('No cerraré el rostro mediante voz.');
      // Una orden para la cara nunca puede modificar la ventana donde
      // el usuario está navegando por Google/YouTube u otras pestañas.
      w=(await ensureDedicatedFace(ws)).window;
      w=await chrome.windows.get(w.id,{populate:true});
      if(!faceWindow(w)||(w.tabs||[]).length!==1)
        throw Error('La cara no tiene una ventana independiente. No modificaré otras pestañas.');
    } else {
      w=choose(ws);
      if(!w) throw Error('No hay una ventana normal del navegador identificable.');
      w=await freshNormal(w.id);
    }
    if(m.action==='cierra') {
      if(m.target!=='browser') throw Error('No cerraré el rostro mediante voz.');
      await freshNormal(w.id); await chrome.windows.remove(w.id);
      if(lastNormal===w.id) lastNormal=null;
      return {ok:true};
    }
    const state={minimiza:'minimized',maximiza:'maximized',restaura:'normal',completa:'fullscreen'}[m.action];
    // Al pasar de minimizada o pantalla completa a maximizada, primero
    // restaurar, y después aplicar el nuevo estado a la ventana correcta.
    if(m.action!=='minimiza' && w.state!=='normal' && w.state!==state)
      await chrome.windows.update(w.id,{state:'normal'});
    if(m.target==='browser') await freshNormal(w.id);
    await chrome.windows.update(w.id,{state});
    if(m.action!=='minimiza') await chrome.windows.update(w.id,{focused:true});
    // Algunos compositores Wayland confirman el estado un poco después.
    // Comprobar varias veces sin modificar ninguna ventana adicional.
    let actual=await chrome.windows.get(w.id,{populate:true});
    for (let retry=0;actual.state!==state && retry<3;retry++) {
      await new Promise(resolve=>setTimeout(resolve,200*(retry+1)));
      actual=await chrome.windows.get(w.id,{populate:true});
    }
    if(m.target==='face' && (!faceWindow(actual)||actual.tabs.length!==1))
      throw Error('No puedo confirmar que la cara esté aislada.');
    if(m.target==='browser') lastNormal=w.id;
    if(actual.state!==state) return {ok:false,error:'Chromium no confirmó el estado de la ventana. No afirmaré que lo cambié.'};
    return {ok:true,state:actual.state};
  }
  throw Error('Operación no permitida.');
}
function connect() {
  if(port) return;
  try {
    const p=chrome.runtime.connectNative('org.carbotai.browser'); port=p;
    p.onMessage.addListener(m=>{
      queue=queue.catch(()=>{}).then(async()=>{
        try { const result=await operate(m); p.postMessage(result); }
        catch(e) { try { p.postMessage({ok:false,error:String(e.message||e).slice(0,240)}); } catch(_){} }
      });
    });
    p.onDisconnect.addListener(()=>{ if(port===p) port=null; void chrome.runtime.lastError; });
  }catch(_) {port=null;}
}
chrome.windows.onFocusChanged.addListener(async id=>{try{const w=await chrome.windows.get(id,{populate:true}); if(normal(w)) lastNormal=id;}catch(_){} });
chrome.windows.onRemoved.addListener(id=>{if(lastNormal===id)lastNormal=null;});
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
chrome.action.onClicked.addListener(connect);
chrome.alarms.onAlarm.addListener(a=>{if(a.name==='reconnect')connect();});
chrome.alarms.create('reconnect',{periodInMinutes:1});
connect();
