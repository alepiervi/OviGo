/* OVIGO: menu per telefono, app installabile e notifiche push */
(function(){
  const IOS = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const ANDROID = /android/i.test(navigator.userAgent);
  const STANDALONE = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const PUSH_OK = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  let installEvt = null, swReg = null, pushKey = '', syncedFor = '';

  const ls = (k, v) => { try{ if(v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); }catch(e){ return null; } };

  if('serviceWorker' in navigator){
    navigator.serviceWorker.register('/sw.js', {scope:'/'}).then(r => { swReg = r; syncPush(); }).catch(e => console.warn('sw', e));
  }
  window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); installEvt = e; banner(); });
  window.addEventListener('appinstalled', () => { installEvt = null; toast('App OVIGO installata'); banner(); });

  /* ---------- header: pulsante menu su telefono ---------- */
  const _header = header;
  header = function(){
    return _header().replace('<nav class="main">', '<button class="btn hamb" data-testid="mobile-menu-btn" aria-label="Menu" onclick="event.stopPropagation();document.body.classList.toggle(\'nav-open\')">☰</button><nav class="main" data-testid="main-nav">')
      .replace('</nav>', '<a data-testid="nav-app-link" onclick="openAppModal()">📲 Scarica l\'app</a></nav>');
  };
  document.addEventListener('click', e => {
    if(!e.target.closest('.hamb')) document.body.classList.remove('nav-open');
    if(e.target.closest('.sidebar a')) document.body.classList.remove('sb-open');
  });

  /* ---------- dopo ogni render: menu delle aree riservate e banner ---------- */
  const _renderAll = renderAll;
  renderAll = function(){
    _renderAll.apply(this, arguments);
    const sb = document.querySelector('#root .sidebar');
    if(sb && !sb.querySelector('.sb-toggle')){
      const active = sb.querySelector('a.on');
      const label = active ? [...active.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim() : '';
      const b = document.createElement('button');
      b.className = 'btn sb-toggle'; b.setAttribute('data-testid', 'sidebar-toggle-btn');
      b.innerHTML = '☰ ' + (label ? esc(label) + ' · altre sezioni' : 'Sezioni');
      b.onclick = () => document.body.classList.toggle('sb-open');
      const who = sb.querySelector('.who');
      who ? who.after(b) : sb.prepend(b);
    }
    banner(); syncPush();
  };

  const _logout = window.logout;
  if(typeof _logout === 'function'){
    logout = async function(){ await unsubscribeThisDevice(); syncedFor = ''; return _logout.apply(this, arguments); };
  }

  /* ---------- banner in basso ---------- */
  function bannerState(){
    if(STANDALONE()){
      if(PUSH_OK && me() && Notification.permission === 'default' && ls('ovigo-nb-push') !== 'no') return 'push';
      return '';
    }
    if((IOS || ANDROID || installEvt) && ls('ovigo-nb-app') !== 'no') return 'app';
    if(PUSH_OK && me() && Notification.permission === 'default' && ls('ovigo-nb-push') !== 'no' && !IOS) return 'push';
    return '';
  }
  function banner(){
    let el = document.getElementById('appbanner');
    if(!el){ el = document.createElement('div'); el.id = 'appbanner'; el.setAttribute('data-testid', 'app-banner'); document.body.appendChild(el); }
    const s = bannerState();
    if(!s){ el.classList.remove('on'); document.body.classList.remove('ab-on'); return; }
    el.innerHTML = s === 'app'
      ? `<img src="/icon-192.png" alt=""><div class="t"><b>Scarica l'app OVIGO</b>Sul telefono, con le notifiche dei tuoi viaggi. Senza store.</div><button class="btn pri sm" data-testid="app-banner-install-btn" onclick="openAppModal()">Installa</button><button class="x" data-testid="app-banner-close-btn" aria-label="Chiudi" onclick="appBannerNo('app')">×</button>`
      : `<img src="/icon-192.png" alt=""><div class="t"><b>Attiva le notifiche</b>Ricevi subito conferme, bonifici e avvisi di viaggio.</div><button class="btn pri sm" data-testid="app-banner-push-btn" onclick="enablePush()">Attiva</button><button class="x" data-testid="app-banner-close-btn" aria-label="Chiudi" onclick="appBannerNo('push')">×</button>`;
    el.classList.add('on'); document.body.classList.add('ab-on');
  }
  window.appBannerNo = k => { ls('ovigo-nb-' + k, 'no'); banner(); };

  /* ---------- finestra «Scarica l'app» ---------- */
  window.openAppModal = async function(){
    let hasSub = false;
    try{ const r = PUSH_OK && await navigator.serviceWorker.getRegistration('/'); hasSub = !!(r && await r.pushManager.getSubscription()) && ls('ovigo-push-off') !== '1'; }catch(e){}
    let inst;
    if(STANDALONE()) inst = `<div class="notice green">✓ Stai già usando l'app OVIGO installata.</div>`;
    else if(installEvt) inst = `<p>Un tocco e OVIGO compare tra le app del telefono, del tablet o del computer, con la sua icona.</p><button class="btn pri" data-testid="app-install-btn" onclick="installApp()">📲 Installa l'app</button>`;
    else if(IOS) inst = `<p>Su iPhone e iPad si installa da <b>Safari</b> (iOS 16.4 o successivo):</p><ol class="appsteps"><li>Tocca il pulsante <b>Condividi</b> <span style="font-size:1.1em">⬆︎</span> in basso (o in alto su iPad).</li><li>Scegli <b>«Aggiungi alla schermata Home»</b>.</li><li>Tocca <b>Aggiungi</b>: l'icona OVIGO compare sulla Home.</li><li>Apri OVIGO dall'icona ed entra nel tuo account: ti chiederà di attivare le notifiche.</li></ol>`;
    else if(ANDROID) inst = `<p>Su Android, da Chrome:</p><ol class="appsteps"><li>Tocca il menu <b>⋮</b> in alto a destra.</li><li>Scegli <b>«Installa app»</b> o <b>«Aggiungi a schermata Home»</b>.</li><li>Conferma: l'icona OVIGO compare tra le tue app.</li></ol>`;
    else inst = `<p>Dal computer, con Chrome o Edge: clicca l'icona <b>Installa</b> ⊕ a destra nella barra degli indirizzi. Sul telefono apri questo sito e tocca <b>«Scarica l'app»</b>.</p>`;
    let push;
    if(!PUSH_OK) push = `<p class="small muted">${IOS && !STANDALONE() ? "Su iPhone e iPad le notifiche si attivano dopo aver installato l'app sulla Home e averla aperta dall'icona." : 'Questo browser non supporta le notifiche.'}</p>`;
    else if(!me()) push = `<p class="small muted">Accedi al tuo account per attivare le notifiche su questo dispositivo.</p><button class="btn" onclick="closeModal();openAuth('login')">Accedi</button>`;
    else if(Notification.permission === 'denied') push = `<div class="notice">Le notifiche sono bloccate per OVIGO. Riattivale dalle impostazioni del telefono o del browser (Notifiche → OVIGO), poi torna qui.</div>`;
    else if(Notification.permission === 'granted' && hasSub) push = `<div class="notice green" data-testid="push-active">✓ Notifiche attive su questo dispositivo.</div><div class="row" style="margin-top:10px"><button class="btn sm" data-testid="push-test-btn" onclick="testPush()">Invia una notifica di prova</button><button class="btn sm ghost" data-testid="push-off-btn" onclick="disablePush()">Disattiva su questo dispositivo</button></div>`;
    else push = `<p>Ricevi sul telefono le conferme di prenotazione, i bonifici ricevuti, le partenze confermate e gli avvisi del viaggio.</p><button class="btn dark" data-testid="push-enable-btn" onclick="enablePush()">🔔 Attiva le notifiche</button>`;
    openModal(`<div data-testid="app-modal"><h2>📲 L'app OVIGO</h2><p class="small muted">Funziona su telefono, tablet e computer. Non serve passare da App Store o Google Play: si installa direttamente da qui.</p>
      <h3 style="margin-top:18px">1 · Installa</h3>${inst}
      <h3 style="margin-top:22px">2 · Notifiche</h3>${push}</div>`);
  };

  window.installApp = async function(){
    if(!installEvt) return openAppModal();
    installEvt.prompt();
    const r = await installEvt.userChoice.catch(() => null);
    installEvt = null; closeModal();
    if(r && r.outcome === 'accepted') toast('Installazione in corso…');
    banner();
  };

  /* ---------- notifiche push ---------- */
  function b64(s){ const p = '='.repeat((4 - s.length % 4) % 4); const r = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from(r, c => c.charCodeAt(0)); }
  async function key(){ if(!pushKey){ const j = await api('GET', '/push/chiave'); pushKey = j.publicKey || ''; } return pushKey; }
  async function reg(){ return swReg || (swReg = await navigator.serviceWorker.register('/sw.js', {scope:'/'})); }
  async function subscribeAndSend(){
    const k = await key(); if(!k) throw new Error('Notifiche non ancora configurate sul server');
    const r = await reg(); await navigator.serviceWorker.ready;
    let sub = await r.pushManager.getSubscription();
    if(!sub) sub = await r.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:b64(k)});
    await api('POST', '/push/iscrivi', sub.toJSON());
    syncedFor = CUR;
  }
  window.enablePush = async function(){
    if(!PUSH_OK){ openAppModal(); return; }
    if(!me()){ toast('Accedi per attivare le notifiche'); openAuth('login'); return; }
    try{
      const p = await Notification.requestPermission();
      if(p !== 'granted'){ toast('Notifiche non autorizzate'); banner(); return; }
      await subscribeAndSend();
      ls('ovigo-push-off', '0');
      toast('Notifiche attivate su questo dispositivo');
      if(document.getElementById('modal').classList.contains('on')) openAppModal();
    }catch(e){ toast('Attivazione non riuscita: ' + e.message); }
    banner();
  };
  window.testPush = async function(){
    try{ const j = await api('POST', '/push/prova'); toast(j.inviate ? 'Notifica di prova inviata' : 'Nessun dispositivo iscritto: riattiva le notifiche'); }
    catch(e){ toast(e.message); }
  };
  async function unsubscribeThisDevice(){
    try{
      if(!PUSH_OK || !TOKEN) return;
      const r = await navigator.serviceWorker.getRegistration('/'); if(!r) return;
      const sub = await r.pushManager.getSubscription(); if(!sub) return;
      await api('POST', '/push/disiscrivi', {endpoint: sub.endpoint});
    }catch(e){}
  }
  window.disablePush = async function(){
    await unsubscribeThisDevice();
    try{ const r = await navigator.serviceWorker.getRegistration('/'); const s = r && await r.pushManager.getSubscription(); if(s) await s.unsubscribe(); }catch(e){}
    syncedFor = ''; ls('ovigo-push-off', '1'); toast('Notifiche disattivate su questo dispositivo'); closeModal();
  };
  // se il permesso c'è già, collega questo dispositivo all'account con cui si è entrati
  function syncPush(){
    if(!PUSH_OK || !swReg || !CUR || !TOKEN || syncedFor === CUR || Notification.permission !== 'granted' || ls('ovigo-push-off') === '1') return;
    syncedFor = CUR;
    subscribeAndSend().catch(() => { syncedFor = ''; });
  }
})();
