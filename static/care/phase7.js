/* Small actions retain one logical request through retries; no external analytics. */
(() => {
  'use strict';
  const body = document.body;
  const endpoint = body.dataset.actionEndpoint7;
  const csrf = () => document.querySelector('#flow-csrf7 [name=csrfmiddlewaretoken]')?.value || document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';
  const pending = new Map(), busy = new Set(), gps = new Map();
  let pageRequest = 0, lastPageRetry = null, memberTrigger = null;
  const token = () => crypto.randomUUID();
  const stored = key => { try { return JSON.parse(sessionStorage.getItem('taily7:' + key)); } catch (_) { return null; } };
  const persist = (key, value) => { try { value === null ? sessionStorage.removeItem('taily7:' + key) : sessionStorage.setItem('taily7:' + key, JSON.stringify(value)); } catch (_) { /* In-memory idempotence remains available. */ } };
  const toast = document.querySelector('#flow-toast7');
  function announce(text, options = {}) {
    if (!toast) return;
    toast.hidden = false; toast.classList.toggle('is-error', Boolean(options.error));
    toast.querySelector('[data-toast-text7]').textContent = text;
    const link = toast.querySelector('[data-toast-link7]');
    link.hidden = !options.url; if (options.url) link.href = options.url;
    const undo = toast.querySelector('[data-toast-undo7]');
    undo.hidden = !options.undo;
    if (options.undo) { undo.dataset.url = options.undo; undo.dataset.version = String(options.version); }
  }
  async function post(url, data, asForm = false) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(url, {method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: {'X-CSRFToken': csrf(), 'X-Taily-UI': '1', ...(asForm ? {} : {'Content-Type': 'application/json'})},
        body: asForm ? data : JSON.stringify(data)});
      let payload;
      try { payload = await response.json(); } catch (_) { throw new Error('서버 응답을 확인하지 못했어요. 같은 요청으로 다시 확인해 주세요.'); }
      if (!response.ok || !payload.ok) { const e = new Error(payload.message || '저장하지 못했어요. 입력을 유지했으니 다시 확인해 주세요.'); e.status = response.status; throw e; }
      return payload;
    } finally { clearTimeout(timer); }
  }
  function feedback(button, text, error = false) {
    const scope = button.closest('.flow-actions,.schedule-item7,.flow-ongoing,[data-notice7]');
    const message = scope?.querySelector('[data-action-feedback7]');
    if (message) { message.hidden = false; message.textContent = text; message.classList.toggle('is-error', error); }
  }
  function actionKey(button) {
    return [endpoint, button.dataset.action7, button.dataset.module, button.dataset.preset || '', button.dataset.activity || '', button.dataset.routineItem || '', button.dataset.date || ''].join(':');
  }
  function markButtons(key, value) {
    document.querySelectorAll('[data-action7]').forEach(button => {
      if (actionKey(button) === key) { button.disabled = value; button.setAttribute('aria-busy', String(value)); }
    });
  }
  function captureBrowse() {
    const details=[...document.querySelectorAll('#main details')];
    return {search: document.querySelector('#workspace-search')?.value,
      filter: document.querySelector('[data-module-browser="workspace"] [data-module-filter][aria-pressed="true"]')?.dataset.moduleFilter,
      custom:document.querySelector('[data-custom-filter]')?.value,
      scroll:window.scrollY,
      opened:details.map((d,i)=>d.open?i:null).filter(i=>i!==null)};
  }
  function replaceMain(html, previous = captureBrowse()) {
    const parsed = new DOMParser().parseFromString(html, 'text/html');
    const next = parsed.querySelector('#main'), current = document.querySelector('#main');
    if (!next || !current) throw new Error('화면을 불러오지 못했어요. 저장된 기록은 원기록에서 확인해 주세요.');
    current.replaceWith(next); document.title = parsed.title;
    if (next.dataset.revision) body.dataset.revision = next.dataset.revision;
    document.dispatchEvent(new Event('taily:refresh'));
    if (previous.filter) document.querySelector('[data-module-browser="workspace"] [data-module-filter="' + CSS.escape(previous.filter) + '"]')?.click();
    if (previous.search !== undefined) { const search = document.querySelector('#workspace-search'); if (search) { search.value = previous.search; search.dispatchEvent(new Event('input', {bubbles:true})); } }
    if(previous.custom!==undefined){const group=document.querySelector('[data-custom-filter]');if(group){group.value=previous.custom;group.dispatchEvent(new Event('change',{bubbles:true}));}}
    if(previous.opened){const details=[...document.querySelectorAll('#main details')];previous.opened.forEach(i=>{if(details[i])details[i].open=true;});}
    if(previous.scroll!==undefined)requestAnimationFrame(()=>window.scrollTo({top:previous.scroll,behavior:'instant'}));
    tick();
  }
  async function refreshSurface() {
    const response = await fetch(location.href, {credentials:'same-origin',cache:'no-store'});
    if (!response.ok) throw new Error('기록은 저장됐지만 화면 갱신에 실패했어요. 새로고침하거나 원기록을 확인해 주세요.');
    const html=await response.text();
    replaceMain(html);
    const updated=new DOMParser().parseFromString(html,'text/html');
    const sidebar=updated.querySelector('.sidebar-members'),current=document.querySelector('.sidebar-members');
    if(sidebar&&current)current.replaceWith(sidebar);
  }
  // Shared UI contract used by in-place forms; does not expose storage or permissions.
  window.TailyUI=Object.freeze({refresh:refreshSurface,announce});
  async function runAction(button) {
    if (!endpoint) return;
    const key = actionKey(button); if (busy.has(key)) return;
    let request = pending.get(key) || stored(key);
    if (!request) {
      request = {token:token(),operation:button.dataset.action7,module:button.dataset.module};
      if (button.dataset.preset) { request.preset_id = button.dataset.preset; if (button.dataset.presetVersion) request.preset_version = Number(button.dataset.presetVersion); }
      if (button.dataset.activity) { request.activity_id = button.dataset.activity; request.version = Number(button.dataset.version); }
      if (button.dataset.routineItem) { request.routine_item = button.dataset.routineItem; request.date = button.dataset.date; }
      if (request.operation === 'finish' && request.module === 'walk') {
        const scope = button.closest('.flow-ongoing'), distance = scope?.querySelector('[data-distance7]');
        if (distance && distance.value !== '') request.fields = {distance:distance.value,distance_unit:'km'};
        const state = gps.get(request.activity_id);
        if (state && !state.manuallyEdited) request.gps = {samples:state.samples,gaps:state.gaps,screen_hidden:state.hidden,complete:state.samples>=2 && state.gaps===0 && !state.hidden && state.wholeSession};
      }
      pending.set(key, request); persist(key, request);
    }
    busy.add(key); markButtons(key, true); feedback(button, '저장 중…');
    let result;
    try {
      result = await post(endpoint, request);
      pending.delete(key);persist(key,null);
      if (request.operation === 'finish') { const state = gps.get(request.activity_id); if (state?.watch !== null && state?.watch !== undefined) navigator.geolocation?.clearWatch(state.watch); gps.delete(request.activity_id); persist('gps:'+request.activity_id,null); }
      announce(result.message,{url:result.url,undo:result.undo_allowed ? result.undo_url : null,version:result.version});
      try { await refreshSurface(); const next=[...document.querySelectorAll('[data-action7]')].find(b=>actionKey(b)===key); if(next)next.focus({preventScroll:true}); else {const h=document.querySelector('#main h1');if(h){h.tabIndex=-1;h.focus({preventScroll:true});}} } catch (e) { announce(e.message,{url:result.url,error:true}); }
    } catch (e) {
      // A response-lost retry retains exactly the same token and body.
      feedback(button,e.name==='AbortError' ? '응답이 늦어요. 같은 버튼으로 결과를 다시 확인해 주세요.' : e.message,true);
      announce(e.name==='AbortError' ? '응답이 늦어요. 다시 시도할 때 같은 요청 번호를 사용해요.' : e.message,{error:true});
      if ([400,403,404,409,422].includes(e.status)) {pending.delete(key);persist(key,null);}
    } finally {busy.delete(key);markButtons(key,false);}
  }
  function pageState(message, error = false) {
    let panel = document.querySelector('#flow-page-state7');
    if (!panel) { panel = document.createElement('div');panel.id='flow-page-state7';panel.className='flow-page-state7';panel.setAttribute('role','status');document.querySelector('#main')?.prepend(panel); }
    panel.textContent=message;panel.hidden=false;
    if (error) { const retry=document.createElement('button');retry.type='button';retry.className='button secondary small';retry.textContent='다시 불러오기';retry.addEventListener('click',()=>lastPageRetry?.());panel.append(' ',retry); }
  }
  async function navigate(url, push = true) {
    const serial = ++pageRequest;
    const main=document.querySelector('#main');main?.setAttribute('aria-busy','true');
    pageState('통계를 불러오는 중이에요. 기존 값은 불러오기가 끝날 때까지 유지해요.');
    const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),20000);
    lastPageRetry=()=>navigate(url,push);
    try {
      const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
      if (!response.ok) throw new Error('통계를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.');
      const html=await response.text();if (serial!==pageRequest)return;
      replaceMain(html,{});if(push)history.pushState({phase7:true},'',url);
      const heading=document.querySelector('#main h1');if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}
    } catch(e) {if(serial===pageRequest)pageState(e.name==='AbortError'?'통계 응답이 늦어요. 기존 내용은 유지했어요.':e.message,true);}
    finally {clearTimeout(timeout);document.querySelector('#main')?.removeAttribute('aria-busy');}
  }
  function haversine(a,b) {
    const radians=x=>x*Math.PI/180, dlat=radians(b.lat-a.lat),dlon=radians(b.lon-a.lon);
    const x=Math.sin(dlat/2)**2+Math.cos(radians(a.lat))*Math.cos(radians(b.lat))*Math.sin(dlon/2)**2;
    return 6371000*2*Math.atan2(Math.sqrt(x),Math.sqrt(Math.max(0,1-x)));
  }
  function gpsNotice(id) {
    const state=gps.get(id),scope=document.querySelector('[data-timer-id="'+CSS.escape(id)+'"]');if(!state||!scope)return;
    const node=scope.querySelector('[data-gps-status7]'),input=scope.querySelector('[data-distance7]'),button=scope.querySelector('[data-gps-start7]');
    if(node)node.textContent=(state.error || '기록된 GPS 거리 '+(state.metres/1000).toFixed(3)+' km · 위치 '+state.samples+'개') + (state.gaps||state.hidden||!state.wholeSession?' · 누락 가능성이 있어 부분 거리로 남겨요.':' · 브라우저가 보이는 동안의 추정값이에요.');
    if(input&&!state.manuallyEdited&&state.samples>=2)input.value=(state.metres/1000).toFixed(3);
    if(button)button.textContent=state.watch!==null?'GPS 측정 멈추기':'이 화면에서 GPS 거리 측정';
  }
  function startGps(button) {
    const scope=button.closest('[data-timer-id]'),id=scope?.dataset.timerId;if(!id)return;
    if(!navigator.geolocation){announce('이 브라우저는 위치 측정을 지원하지 않아요. 거리를 직접 입력할 수 있어요.',{error:true});return;}
    let state=gps.get(id);
    if(state?.watch!==null&&state?.watch!==undefined){navigator.geolocation.clearWatch(state.watch);state.watch=null;state.gaps++;gpsNotice(id);return;}
    if(!state){const recovered=stored('gps:'+id);state={metres:recovered?.metres||0,samples:recovered?.samples||0,gaps:recovered?recovered.gaps+1:0,hidden:Boolean(recovered?.hidden),last:null,watch:null,manuallyEdited:false,error:'',wholeSession:Date.now()-Date.parse(scope.dataset.startedAt)<60000};gps.set(id,state);}
    state.watch=navigator.geolocation.watchPosition(position=>{
      const point={lat:position.coords.latitude,lon:position.coords.longitude,at:position.timestamp,accuracy:position.coords.accuracy};
      if(!Number.isFinite(point.accuracy)||point.accuracy>50){state.gaps++;state.last=null;state.error='위치 오차가 큰 구간은 거리에서 제외했어요.';gpsNotice(id);return;}
      state.error='';state.samples++;
      if(state.last){const dt=(point.at-state.last.at)/1000,distance=haversine(state.last,point);if(dt>0&&dt<=120&&distance/dt<=15)state.metres+=distance;else{state.gaps++;}}
      state.last=point;persist('gps:'+id,{metres:state.metres,samples:state.samples,gaps:state.gaps,hidden:state.hidden});gpsNotice(id);
    },error=>{state.error=error.code===1?'위치 권한이 거부됐어요. 거리를 직접 입력할 수 있어요.':'위치 신호를 받지 못했어요. 시간은 시작 시각으로 유지돼요.';state.gaps++;gpsNotice(id);},
    {enableHighAccuracy:true,maximumAge:0,timeout:15000});gpsNotice(id);
  }
  function tick() {
    document.querySelectorAll('[data-timer-id]').forEach(scope=>{
      const start=Date.parse(scope.dataset.startedAt);if(!Number.isFinite(start))return;
      const elapsed=Math.max(0,Math.floor((Date.now()-start)/1000)),minutes=Math.floor(elapsed/60),seconds=elapsed%60;
      const label=scope.querySelector('[data-elapsed7]');if(label)label.textContent=minutes+'분 '+String(seconds).padStart(2,'0')+'초';gpsNotice(scope.dataset.timerId);
    });
  }
  const memberDialog=document.querySelector('#member-dialog7');
  async function openMember(anchor) {
    if(!memberDialog){location.href=anchor.href;return;}
    memberTrigger=anchor;memberDialog.dataset.url=anchor.href;
    const content=memberDialog.querySelector('[data-member-content7]');content.innerHTML='<h2 id="member-panel-title7">구성원 정보</h2><p role="status">불러오는 중이에요.</p>';
    memberDialog.showModal();body.classList.add('has-member-panel7');memberDialog.querySelector('[data-member-close7]').focus();
    try{const response=await fetch(anchor.href,{credentials:'same-origin',headers:{'X-Taily-Panel':'1'}});if(!response.ok)throw new Error('구성원 정보를 불러오지 못했어요. 권한과 연결을 확인해 주세요.');const html=await response.text();if(memberDialog.open){content.innerHTML=html;memberDialog.dataset.initial=memberFormState()||'';delete memberDialog.dataset.dirty;}}
    catch(e){content.innerHTML='';const h=document.createElement('h2');h.id='member-panel-title7';h.textContent='구성원 정보';const p=document.createElement('p');p.setAttribute('role','alert');p.textContent=e.message;const retry=document.createElement('a');retry.href=anchor.href;retry.className='button secondary';retry.textContent='전체 화면으로 다시 열기';content.append(h,p,retry);}
  }
  function memberFormState(){
    const form=memberDialog?.querySelector('[data-member-form7]');
    return form?JSON.stringify([...new FormData(form).entries()].sort(([a],[b])=>a.localeCompare(b))):null;
  }
  function mayCloseMember() { return !memberDialog?.dataset.dirty || window.confirm('저장하지 않은 입력이 있어요. 닫으시겠어요?'); }
  memberDialog?.addEventListener('cancel',event=>{if(!mayCloseMember())event.preventDefault();});
  memberDialog?.addEventListener('input',()=>{
    if(memberFormState()===memberDialog.dataset.initial)delete memberDialog.dataset.dirty;
    else memberDialog.dataset.dirty='1';
  });
  memberDialog?.addEventListener('keydown',event=>{
    if(event.key==='Escape'){
      event.preventDefault();event.stopPropagation();
      if(mayCloseMember())memberDialog.close();
      return;
    }
    if(event.key!=='Tab') return;
    const elements=[...memberDialog.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')]
      .filter(el=>el.getClientRects().length>0&&el.tabIndex>=0);
    if(!elements.length){event.preventDefault();return;}
    const first=elements[0],last=elements[elements.length-1],active=document.activeElement;
    if(event.shiftKey&&(active===first||!memberDialog.contains(active))){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&(active===last||!memberDialog.contains(active))){event.preventDefault();first.focus();}
  });
  memberDialog?.addEventListener('close',()=>{delete memberDialog.dataset.dirty;body.classList.remove('has-member-panel7');if(memberTrigger?.isConnected)memberTrigger.focus({preventScroll:true});else{const next=[...document.querySelectorAll('[data-member-open7]')].find(a=>a.href===memberDialog.dataset.url);next?.focus({preventScroll:true});}});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)gps.forEach((s,id)=>{if(s.watch!==null){s.hidden=true;s.gaps++;s.last=null;persist('gps:'+id,{metres:s.metres,samples:s.samples,gaps:s.gaps,hidden:true});}});});
  document.addEventListener('input',e=>{if(e.target.matches('[data-distance7]')){const s=gps.get(e.target.closest('[data-timer-id]')?.dataset.timerId);if(s)s.manuallyEdited=true;}});
  document.addEventListener('click',async e=>{
    const action=e.target.closest('[data-action7]');if(action){e.preventDefault();runAction(action);return;}
    const gs=e.target.closest('[data-gps-start7]');if(gs){e.preventDefault();startGps(gs);return;}
    const link=e.target.closest('[data-member-open7]');if(link&&!e.ctrlKey&&!e.metaKey){e.preventDefault();openMember(link);return;}
    if(e.target.closest('[data-member-close7]')){if(mayCloseMember())memberDialog?.close();return;}
    if(e.target.closest('[data-toast-dismiss7]')){toast.hidden=true;return;}
    const undo=e.target.closest('[data-toast-undo7]');if(undo){undo.disabled=true;try{const data=new FormData();data.set('version',undo.dataset.version);const r=await post(undo.dataset.url,data,true);announce(r.message);await refreshSurface();}catch(err){announce(err.message,{error:true});}finally{undo.disabled=false;}return;}
    const notice=e.target.closest('[data-dismiss-notice7]');if(notice){notice.disabled=true;try{const r=await post(body.dataset.noticeEndpoint7,{key:notice.dataset.key,stage:notice.dataset.stage});notice.closest('[data-notice7]').remove();announce(r.message);}catch(err){feedback(notice,err.message,true);}finally{notice.disabled=false;}return;}
    const period=e.target.closest('.statistics-periods a,.date-shortcuts a');if(period&&!e.ctrlKey&&!e.metaKey&&!e.shiftKey){e.preventDefault();navigate(period.href);}
  });
  document.addEventListener('submit',async e=>{
    const memberForm=e.target.closest('[data-member-form7]');
    if(memberForm&&memberDialog?.open){
      e.preventDefault();if(memberForm.dataset.saving==='1')return;memberForm.dataset.saving='1';const submitter=e.submitter;
      const data=new FormData(memberForm);if(submitter?.name)data.set(submitter.name,submitter.value);
      const buttons=[...memberForm.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);
      try{const response=await fetch(memberForm.action,{method:'POST',credentials:'same-origin',body:data,headers:{'X-CSRFToken':csrf(),'X-Taily-Panel':'1'}});if(response.status>=500||response.status===403)throw new Error('설정을 저장하지 못했어요. 현재 입력은 유지돼요.');const html=await response.text();memberDialog.querySelector('[data-member-content7]').innerHTML=html;if(response.ok){memberDialog.dataset.initial=memberFormState()||'';delete memberDialog.dataset.dirty;announce('구성원 설정을 반영했어요.');await refreshSurface();const heading=memberDialog.querySelector('#member-panel-title7');if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}}}
      catch(err){announce(err.message,{error:true});buttons.forEach(b=>b.disabled=false);delete memberForm.dataset.saving;}
      return;
    }
    const form=e.target.closest('[data-safe-submit7]') || (e.target.querySelector('input[name="request_token"]') ? e.target : null);
    if(form){
      if(form.dataset.saving==='1'){e.preventDefault();return;}
      form.dataset.saving='1';
      const state=form.querySelector('[data-submit-state7]');
      if(state){state.hidden=false;state.textContent='저장 중이에요. 같은 요청을 두 번 저장하지 않아요.';}
      // Synchronous disabling of the submitter can cancel the browser's native form submission.
      // Let the current submit event commit first; subsequent submits are blocked above.
      setTimeout(()=>{if(form.isConnected)form.querySelectorAll('button[type=submit]').forEach(b=>{b.disabled=true;b.setAttribute('aria-busy','true');});},0);
    }
  });
  window.addEventListener('popstate',()=>navigate(location.href,false));
  window.addEventListener('pageshow',()=>{document.querySelectorAll('[data-safe-submit7]').forEach(form=>{delete form.dataset.saving;form.querySelectorAll('button[type=submit]').forEach(b=>{b.disabled=false;b.removeAttribute('aria-busy');});});});
  document.addEventListener('taily:refresh',tick);
  tick();setInterval(tick,1000);
})();
