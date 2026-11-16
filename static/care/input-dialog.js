/* One in-place input flow for records, routines and saved settings.
 * Existing Django forms, permissions and idempotence keys remain authoritative.
 */
(() => {
  'use strict';
  const dialog = document.querySelector('#input-dialog');
  if (!dialog) return;
  const content = dialog.querySelector('[data-input-content]');
  const editablePath = /^\/rooms\/[0-9a-f-]+\/(?:settings\/|preferences\/|products\/(?:new|[0-9a-f-]+)\/|modules\/[a-z_]+\/(?:entry|new)\/|modules\/[a-z_]+\/presets\/(?:new|[0-9a-f-]+)\/|routines\/(?:new\/|[0-9a-f-]+\/edit\/|items\/[0-9a-f-]+\/complete\/)|records\/[0-9a-f-]+\/edit\/)$/i;
  let origin = null, originHref = '', initial = '', dirty = false, saving = false, controller = null;
  let loadGeneration = 0, currentURL = '', frozenBody = null, uncertain = false, lastField = null;
  function form() { return content.querySelector('[data-input-form]'); }
  function state() {
    const node=form();
    return node ? JSON.stringify([...new FormData(node).entries()].filter(([key])=>key!=='csrfmiddlewaretoken')) : '';
  }
  function status(text, error=false) {
    const node=content.querySelector('[data-input-status]');
    if (node) { node.textContent=text;node.hidden=false;node.classList.toggle('is-error',error); }
  }
  function lock(value, fields=false) {
    const node=form();if (!node) return;
    node.setAttribute('aria-busy',String(value));
    node.querySelectorAll('button[type="submit"]').forEach(b=>{b.disabled=value;});
    dialog.querySelector('[data-input-close]').disabled=value;
    node.querySelector('[data-input-cancel]')?.toggleAttribute('disabled',value);
    if (fields) node.querySelectorAll('.input-dialog-fields input,.input-dialog-fields select,.input-dialog-fields textarea,.input-dialog-fields button').forEach(el=>{
      if (value && !el.disabled) {el.dataset.lockedByDialog='1';el.disabled=true;}
      else if (!value && el.dataset.lockedByDialog) {el.disabled=false;delete el.dataset.lockedByDialog;}
    });
  }
  function focusBack() {
    const anchor=origin?.isConnected ? origin : [...document.querySelectorAll('a[href]')].find(a=>a.href===originHref&&a.getClientRects().length);
    if(anchor)anchor.focus({preventScroll:true});
    else {const heading=document.querySelector('#main h1');if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}}
  }
  function close() {
    if (saving) return;
    ++loadGeneration;controller?.abort();controller=null;
    dialog.close();dirty=false;frozenBody=null;uncertain=false;content.replaceChildren();focusBack();
  }
  function requestClose() {
    if(saving){status('저장 결과를 확인하고 있어요. 입력은 유지돼요.');return;}
    if(dirty||uncertain){
      const panel=content.querySelector('[data-input-discard]');
      if(panel){lastField=document.activeElement;panel.hidden=false;panel.querySelector('p').textContent=uncertain?'저장 응답을 확인하지 못했어요. 먼저 저장 결과를 확인하는 것이 좋아요.':'저장하지 않은 입력이 있어요.';panel.querySelector('[data-input-continue]').focus();return;}
    }
    close();
  }
  function install(html, first=false) {
    content.innerHTML=html;
    if (!form()) throw new Error('입력 양식을 불러오지 못했어요.');
    content.dispatchEvent(new CustomEvent('taily:input-ready',{bubbles:true}));
    if(first){initial=state();dirty=false;frozenBody=null;uncertain=false;}
    const heading=content.querySelector('#input-dialog-title');
    if(first)heading?.focus({preventScroll:true});
    else {const invalid=content.querySelector('.has-error input,.has-error select,.has-error textarea,.form-errors');(invalid||heading)?.focus({preventScroll:false});}
  }
  async function open(anchor, urlOverride=null) {
    if (dialog.open) return;
    origin=anchor;originHref=anchor.href||'';currentURL=urlOverride||anchor.href;initial='';dirty=false;saving=false;uncertain=false;frozenBody=null;
    const generation=++loadGeneration;
    content.innerHTML='<div data-input-loading><h2 id="input-dialog-title" tabindex="-1">입력창</h2><p role="status">불러오는 중…</p></div>';
    dialog.style.removeProperty('--dialog-top');dialog.style.removeProperty('--available-dialog-height');
    dialog.showModal();dialog.querySelector('[data-input-close]').disabled=false;content.querySelector('h2').focus();
    const loadController=new AbortController();controller=loadController;
    const timeout=setTimeout(()=>loadController.abort(),15000);
    try {
      const response=await fetch(currentURL,{credentials:'same-origin',cache:'no-store',headers:{'X-Taily-Dialog':'1'},signal:loadController.signal});
      if(generation!==loadGeneration)return;
      if(!response.ok || response.headers.get('X-Taily-Fragment')!=='input-form')throw new Error(response.status===403?'입력 권한이 없거나 세션이 바뀌었어요.':'입력창을 불러오지 못했어요. 다시 시도해 주세요.');
      const html=await response.text();
      if(generation!==loadGeneration)return;
      install(html,true);
    } catch(error) {
      if(generation!==loadGeneration)return;
      content.innerHTML='<div data-input-load-error><h2 id="input-dialog-title" tabindex="-1">입력창을 열지 못했어요</h2><p role="alert"></p><div class="row-actions"><button type="button" class="button secondary" data-input-retry>다시 불러오기</button><a class="quiet-link" data-input-fallback>별도 화면에서 열기</a></div></div>';
      content.querySelector('[role="alert"]').textContent=error.name==='AbortError'?'응답이 늦어요. 현재 화면은 유지했어요.':error instanceof TypeError?'연결을 확인한 뒤 다시 불러와 주세요. 현재 화면은 유지했어요.':error.message;
      content.querySelector('[data-input-fallback]').href=currentURL;
      content.querySelector('h2').focus();
    } finally {clearTimeout(timeout);if(controller===loadController)controller=null;}
  }
  function setOrder(node) {
    const list=node.querySelector('.choice-order');const target=node.querySelector('[name="module_order"]');
    if(list&&target)target.value=JSON.stringify([...list.querySelectorAll('.choice-order-row')].filter(row=>row.querySelector('input')?.checked).map(row=>row.dataset.value));
  }
  async function submit(node,submitter) {
    if(saving)return;
    setOrder(node);
    if(!frozenBody){frozenBody=new FormData(node);if(submitter?.name)frozenBody.set(submitter.name,submitter.value);}
    saving=true;lock(true,true);status(uncertain?'앞서 보낸 저장 결과를 확인 중…':'저장 중…');
    const requestController=new AbortController();const timeout=setTimeout(()=>requestController.abort(),20000);
    try {
      const response=await fetch(node.action||currentURL,{method:'POST',credentials:'same-origin',body:frozenBody,signal:requestController.signal,
        headers:{'X-Taily-Dialog':'1','X-CSRFToken':String(frozenBody.get('csrfmiddlewaretoken')||'')}});
      if(response.headers.get('content-type')?.includes('application/json')){
        const result=await response.json();
        if(!response.ok||!result.ok)throw new Error(result.message||'저장 결과를 확인하지 못했어요.');
        saving=false;lock(false,true);dirty=false;uncertain=false;frozenBody=null;close();
        window.TailyUI?.announce(result.message,{url:result.url});
        try {await window.TailyUI?.refresh();if(result.routine_key)window.TailyUI?.completedRoutine(result);else focusBack();}
        catch(error){window.TailyUI?.announce('저장됐지만 화면 갱신에 실패했어요. 기록 보기에서 확인해 주세요.',{url:result.url,error:true});}
        return;
      }
      if(response.headers.get('X-Taily-Fragment')==='input-form'&&[200,400,409,422].includes(response.status)){
        lock(false,true);frozenBody=null;uncertain=false;install(await response.text());dirty=true;
        status(response.status===409?'다른 변경과 충돌했어요. 최신 기록을 확인해 주세요.':'표시된 입력을 확인해 주세요.',true);
        return;
      }
      const error=new Error(response.status===403?'세션 또는 권한을 확인해 주세요. 입력은 유지했어요.':'응답을 확인하지 못했어요. 같은 요청으로 저장 결과를 다시 확인해 주세요.');error.status=response.status;throw error;
    } catch(error) {
      uncertain=true;
      status(error.name==='AbortError'?'응답이 늦어요. 같은 요청으로 저장 결과를 확인해 주세요.':error instanceof TypeError?'연결이 끊겨 응답을 확인하지 못했어요. 같은 요청으로 저장 결과를 다시 확인해 주세요.':error.message,true);
      // Keep the exact body and UUID, and prevent editing it until the ambiguous result is resolved.
      const submit=node.querySelector('button[type="submit"]');if(submit)submit.textContent='저장 결과 다시 확인';
    } finally {
      clearTimeout(timeout);saving=false;lock(false,!uncertain);
      if(uncertain)node.removeAttribute('aria-busy');
    }
  }
  document.addEventListener('click',event=>{
    const a=event.target.closest('a[href]');
    if(!a||event.button!==0||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey||a.target||a.hasAttribute('download'))return;
    if(a.closest('dialog[open]'))return;
    const url=new URL(a.href,location.href);
    if(url.origin===location.origin&&editablePath.test(url.pathname)){
      event.preventDefault();event.stopImmediatePropagation();open(a);
    }
  },true);
  dialog.addEventListener('submit',event=>{
    if(!event.target.matches('[data-input-form]'))return;
    event.preventDefault();event.stopImmediatePropagation();submit(event.target,event.submitter);
  },true);
  dialog.addEventListener('input',()=>{if(!saving&&!uncertain)dirty=state()!==initial;});
  dialog.addEventListener('change',()=>{if(!saving&&!uncertain)dirty=state()!==initial;});
  dialog.addEventListener('click',event=>{
    if(event.target.closest('[data-input-close],[data-input-cancel]')){requestClose();return;}
    if(event.target.closest('[data-input-discard-confirm]')){close();return;}
    if(event.target.closest('[data-input-continue]')){content.querySelector('[data-input-discard]').hidden=true;(lastField?.isConnected?lastField:content.querySelector('h2'))?.focus();return;}
    if(event.target.closest('[data-input-retry]')){const a=origin,url=currentURL;close();open(a,url);return;}
    const move=event.target.closest('[data-dialog-move]');
    if(move){const row=move.closest('.choice-order-row'),other=move.dataset.dialogMove==='up'?row.previousElementSibling:row.nextElementSibling;if(other){if(move.dataset.dialogMove==='up')row.parentNode.insertBefore(row,other);else row.parentNode.insertBefore(other,row);setOrder(form());dirty=true;move.focus();}}
  });
  dialog.addEventListener('cancel',event=>{event.preventDefault();requestClose();});
  dialog.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();const prompt=content.querySelector('[data-input-discard]');if(prompt&&!prompt.hidden){prompt.hidden=true;lastField?.focus();}else requestClose();return;}
    if(event.key!=='Tab')return;
    const items=[...dialog.querySelectorAll('button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]')].filter(x=>x.tabIndex>=0&&x.getClientRects().length>0);
    const first=items[0],last=items.at(-1);if(!first){event.preventDefault();return;}
    if(event.shiftKey&&(document.activeElement===first||!dialog.contains(document.activeElement))){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&(document.activeElement===last||!dialog.contains(document.activeElement))){event.preventDefault();first.focus();}
  });
  window.TailyInput=Object.freeze({open:(url,trigger)=>{const target=new URL(url,location.href);if(target.origin!==location.origin||!editablePath.test(target.pathname))return false;open(trigger,target.href);return true;}});
  window.addEventListener('beforeunload',event=>{if(dialog.open&&(dirty||saving||uncertain)){event.preventDefault();event.returnValue='';}});
})();
