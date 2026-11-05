/* Purpose-specific interactions. The server owns records, versions and room permissions. */
(() => {
  'use strict';
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const csrf = (root = document) => $('[name="csrfmiddlewaretoken"]', root)?.value || '';
  const headers = () => ({'X-Taily-UI': '1', 'X-CSRFToken': csrf()});
  const mobile = matchMedia('(max-width: 1023px)');
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const storage = {
    get(key, fallback) { try { return localStorage.getItem(key) ?? fallback; } catch (_) { return fallback; } },
    set(key, value) { try { localStorage.setItem(key, String(value)); } catch (_) { /* The view still works without storage. */ } }
  };
  async function request(url, options = {}) {
    const target = new URL(url, location.href);
    if (target.origin !== location.origin) throw new Error('잘못된 요청 주소예요. 새로고침 후 확인해 주세요.');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      return await fetch(target, {credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', ...options, signal: controller.signal});
    } finally { clearTimeout(timer); }
  }
  async function jsonResponse(response) {
    const type = response.headers.get('content-type') || '';
    if (!type.includes('application/json')) {
      const message = response.status === 403 ? '참여 권한이나 로그인 상태가 바뀌었어요. 새로고침해서 확인해 주세요.' : response.status === 404 ? '이 방이나 활동에 접근할 수 없어요.' : '응답을 확인하지 못했어요. 입력은 그대로예요. 다시 시도해 주세요.';
      throw Object.assign(new Error(message), {status: response.status});
    }
    const value = await response.json();
    if (!response.ok && !value.html) throw Object.assign(new Error(value.message || '저장하지 못했어요. 입력을 확인해 주세요.'), {status: response.status});
    return value;
  }

  // Desktop width and collapsed state are independent from the mobile drawer.
  const sidebar = $('#sidebar');
  const separator = $('#sidebar-resize');
  let desktopClosed = storage.get('taily.sidebar.closed', '0') === '1';
  let sidebarWidth = Math.max(200, Math.min(360, Number(storage.get('taily.sidebar.width', '240')) || 240));
  let resizeSession = null;
  function setSidebarWidth(value, persist = false) {
    sidebarWidth = Math.max(200, Math.min(360, value));
    document.documentElement.style.setProperty('--sidebar-w', `${sidebarWidth}px`);
    separator?.setAttribute('aria-valuenow', String(sidebarWidth));
    separator?.setAttribute('aria-valuetext', `${sidebarWidth}픽셀`);
    if (persist) storage.set('taily.sidebar.width', sidebarWidth);
  }
  function applySidebar() {
    if (!sidebar) return;
    const open = mobile.matches ? document.body.classList.contains('mobile-menu-open') : !desktopClosed;
    document.body.classList.toggle('sidebar-closed', !mobile.matches && desktopClosed);
    sidebar.inert = !open;
    sidebar.setAttribute('aria-hidden', String(!open));
    $$('[data-sidebar-toggle]').forEach(button => {
      button.setAttribute('aria-expanded', String(open));
      button.setAttribute('aria-label', open ? '사이드바 접기' : '사이드바 펼치기');
    });
    const overlay = $('#sidebar-overlay');
    if (overlay) overlay.hidden = !mobile.matches || !open;
    if (separator) separator.tabIndex = !mobile.matches && open ? 0 : -1;
  }
  function toggleSidebar(force) {
    if (!sidebar) return;
    if (mobile.matches) {
      const open = typeof force === 'boolean' ? force : !document.body.classList.contains('mobile-menu-open');
      document.body.classList.toggle('mobile-menu-open', open);
      applySidebar();
      (open ? $('[data-sidebar-toggle]', sidebar) : $('.sidebar-restore'))?.focus({preventScroll: true});
    } else {
      desktopClosed = typeof force === 'boolean' ? !force : !desktopClosed;
      storage.set('taily.sidebar.closed', desktopClosed ? '1' : '0');
      applySidebar();
      (desktopClosed ? $('.sidebar-restore') : $('[data-sidebar-toggle]', sidebar))?.focus({preventScroll: true});
    }
  }
  setSidebarWidth(sidebarWidth);
  applySidebar();
  mobile.addEventListener('change', () => { document.body.classList.remove('mobile-menu-open'); applySidebar(); });
  separator?.addEventListener('pointerdown', event => {
    if (event.button !== 0 || mobile.matches) return;
    event.preventDefault();
    resizeSession = event.pointerId;
    separator.setPointerCapture(event.pointerId);
    document.body.classList.add('is-resizing');
  });
  separator?.addEventListener('pointermove', event => { if (resizeSession === event.pointerId) setSidebarWidth(Math.round(event.clientX)); });
  const endResize = () => { if (resizeSession !== null) setSidebarWidth(sidebarWidth, true); resizeSession = null; document.body.classList.remove('is-resizing'); };
  separator?.addEventListener('pointerup', endResize);
  separator?.addEventListener('pointercancel', endResize);
  separator?.addEventListener('keydown', event => {
    const step = event.shiftKey ? 40 : 10;
    const next = {ArrowLeft: sidebarWidth - step, ArrowRight: sidebarWidth + step, Home: 200, End: 360}[event.key];
    if (next !== undefined) { event.preventDefault(); setSidebarWidth(next, true); }
  });

  // Quick entry is a real form; failed requests retain its input and idempotency key.
  const dialog = $('#quick-dialog');
  let quickOrigin = null, quickHref = '', quickDirty = false, quickBusy = false, quickGeneration = 0;
  let undo = null;
  function feedback(message, data = null) {
    const box = $('#action-feedback');
    if (!box) return;
    $('[data-feedback-message]', box).textContent = message;
    const link = $('[data-feedback-link]', box), button = $('[data-feedback-undo]', box);
    link.hidden = !data?.url;
    if (data?.url) link.href = data.url;
    undo = data?.undo_allowed && data?.created ? {url: data.undo_url, version: data.version} : null;
    button.hidden = !undo; button.disabled = false; box.hidden = false;
  }
  function focusQuick() {
    const root = $('#quick-content');
    const error = $('.form-errors, .field-error', root);
    if (error) { error.tabIndex = -1; error.focus(); return; }
    const heading = $('#quick-title', root);
    if (heading) { heading.tabIndex = -1; heading.focus({preventScroll: true}); }
  }
  async function openQuick(url, origin) {
    if (!dialog) { location.assign(url); return; }
    if (quickBusy) return;
    if (dialog.open && quickDirty && !confirm('입력 중인 내용을 지우고 최근 값을 가져올까요?')) return;
    const generation = ++quickGeneration;
    if (!dialog.open) { quickOrigin = origin; quickHref = origin?.getAttribute('href') || ''; }
    quickDirty = false;
    const content = $('#quick-content');
    content.textContent = '입력 화면을 여는 중이에요…';
    content.setAttribute('aria-busy', 'true');
    if (!dialog.open) dialog.showModal();
    try {
      const response = await request(url, {headers: {'X-Taily-UI': '1'}});
      if (!response.ok) {
        if ((response.headers.get('content-type') || '').includes('application/json')) await jsonResponse(response);
        throw new Error('기록창을 열 수 없어요. 이 기능의 사용 설정과 참여 권한을 확인해 주세요.');
      }
      const html = await response.text();
      if (generation !== quickGeneration) return;
      content.innerHTML = html;
      focusQuick();
    } catch (error) {
      if (generation !== quickGeneration) return;
      content.textContent = error.name === 'AbortError' ? '연결이 늦어지고 있어요. 닫은 뒤 다시 열어 주세요.' : error.message;
    } finally { if (generation === quickGeneration) content.removeAttribute('aria-busy'); }
  }
  function closeQuick(force = false) {
    if (!dialog?.open) return;
    if (quickBusy && !force) return;
    if (quickDirty && !force && !confirm('입력한 내용을 저장하지 않고 닫을까요?')) return;
    ++quickGeneration;
    dialog.close(); quickDirty = false;
    if (quickOrigin?.isConnected) quickOrigin.focus({preventScroll: true});
  }
  dialog?.addEventListener('cancel', event => { event.preventDefault(); closeQuick(); });
  dialog?.addEventListener('click', event => {
    if (event.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeQuick();
    }
  });
  async function refreshSurface() {
    const response = await request(location.href);
    if (!response.ok) throw new Error('저장은 처리됐지만 화면을 새로 불러오지 못했어요. 새로고침해 주세요.');
    const page = new DOMParser().parseFromString(await response.text(), 'text/html');
    const next = page.getElementById('main');
    if (!next) throw new Error('새 화면을 확인하지 못했어요. 새로고침해 주세요.');
    const y = window.scrollY;
    $('#main').replaceWith(document.importNode(next, true));
    document.body.dataset.revision = page.body.dataset.revision || next.dataset.revision || '';
    const people = $('.sidebar-members', page);
    if (people && $('.sidebar-members')) $('.sidebar-members').replaceWith(document.importNode(people, true));
    document.dispatchEvent(new Event('taily:refresh'));
    updateTimers();
    if (quickHref) $$('a[data-quick]').find(link => link.getAttribute('href') === quickHref)?.focus({preventScroll: true});
    window.scrollTo({top: y, behavior: 'instant'});
  }
  async function submitQuick(form) {
    if (quickBusy) return;
    if (!form.reportValidity()) return;
    quickBusy = true;
    const status = $('.quick-save-status', form);
    const buttons = $$('button[type="submit"]', form);
    buttons.forEach(b => b.disabled = true);
    if (status) status.textContent = '저장 중이에요…';
    try {
      const response = await request(form.action, {method: 'POST', headers: headers(), body: new FormData(form)});
      const result = await jsonResponse(response);
      if (!result.ok) {
        if (result.html) {
          const target = dialog?.open ? $('#quick-content') : $('.quick-standalone');
          target.innerHTML = result.html;
          quickDirty = true;
          if (dialog?.open) focusQuick();
        } else if (status) status.textContent = result.message || '입력한 내용을 확인해 주세요.';
        return;
      }
      quickDirty = false;
      if (!dialog?.open) { location.assign(result.url); return; }
      closeQuick(true);
      feedback(result.message, result);
      try { await refreshSurface(); } catch (error) { feedback(`${result.message} ${error.message}`, result); }
    } catch (error) {
      if (status) status.textContent = error.name === 'AbortError' ? '서버 응답을 확인하지 못했어요. 입력은 유지돼요. 같은 화면에서 다시 저장하면 중복 요청을 확인해요.' : error.message === 'Failed to fetch' ? '연결이 끊겼어요. 입력은 그대로예요. 연결 후 다시 저장해 주세요.' : error.message;
    } finally { quickBusy = false; buttons.forEach(b => b.disabled = false); }
  }
  document.addEventListener('invalid', event => {
    if (event.target.closest('[data-quick-form]')) {
      let parent = event.target.parentElement;
      while (parent) { if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement; }
    }
  }, true);
  document.addEventListener('input', event => { if (event.target.closest('[data-quick-form]')) quickDirty = true; });
  document.addEventListener('change', event => { if (event.target.closest('[data-quick-form]')) quickDirty = true; });
  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.matches('[data-quick-form]')) { event.preventDefault(); await submitQuick(form); }
    if (form.matches('[data-snooze]')) {
      event.preventDefault();
      if (form.dataset.busy === '1') return;
      form.dataset.busy = '1';
      const button = $('button', form); if (button) button.disabled = true;
      try {
        const result = await jsonResponse(await request(form.action, {method: 'POST', headers: headers(), body: new FormData(form)}));
        feedback(result.message); await refreshSurface();
      } catch (error) { feedback(error.message || '미루지 못했어요. 다시 확인해 주세요.'); }
      finally { form.dataset.busy = '0'; if (button) button.disabled = false; }
    }
    if (form.matches('[data-module-config]')) event.preventDefault();
  });
  document.addEventListener('click', async event => {
    const toggle = event.target.closest('[data-sidebar-toggle]');
    if (toggle) { toggleSidebar(); return; }
    if (event.target.closest('#sidebar-overlay')) { toggleSidebar(false); return; }
    const link = event.target.closest('a[data-quick]');
    if (link && event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) {
      event.preventDefault(); await openQuick(link.href, link); return;
    }
    if (event.target.closest('[data-quick-close]')) { if (dialog?.open) closeQuick(); else { const back = $('.quick-standalone > a'); if (back) location.assign(back.href); } return; }
    if (event.target.closest('[data-feedback-dismiss]')) { $('#action-feedback').hidden = true; return; }
    const undoButton = event.target.closest('[data-feedback-undo]');
    if (undoButton && undo) {
      const target = undo; undoButton.disabled = true;
      try {
        const data = new FormData(); data.append('version', target.version);
        const result = await jsonResponse(await request(target.url, {method: 'POST', headers: headers(), body: data}));
        feedback(result.message); await refreshSurface();
      } catch (error) { $('[data-feedback-message]').textContent = error.message; undoButton.disabled = false; }
    }
  });
  document.addEventListener('keydown', event => {
    if (dialog?.open) return;
    if (mobile.matches && document.body.classList.contains('mobile-menu-open')) {
      if (event.key === 'Escape') { event.preventDefault(); toggleSidebar(false); }
      if (event.key === 'Tab') {
        const items = $$('a[href],button:not([disabled]),select,input,[tabindex="0"]', sidebar).filter(e => e.offsetParent !== null);
        const first = items[0], last = items[items.length-1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    }
  });
  function updateTimers() {
    if (document.hidden) return;
    $$('[data-elapsed]').forEach(node => {
      const seconds = Math.max(0, Math.floor((Date.now()-Date.parse(node.dataset.elapsed))/1000));
      if (Number.isFinite(seconds)) node.textContent = `· ${Math.floor(seconds/60)}분 ${String(seconds%60).padStart(2,'0')}초`;
    });
  }
  updateTimers(); setInterval(updateTimers, 1000);

  // Two-list configuration. Save requests are serialized; failure never discards the working order.
  const config = $('[data-module-config]');
  let configDirty = false;
  if (config) {
    const list = $('#chosen-modules'), status = $('#config-status');
    const retry = $('#config-retry'), reload = $('#config-reload');
    let stored = JSON.parse($('#module-config-state').textContent);
    let version = stored.version, saving = false, blocked = false, epoch = 0, timer = null, drag = null, keyboard = null;
    const order = () => $$('.chosen-row', list).map(row => row.dataset.key);
    const current = () => {
      const keys = order();
      return {enabled: keys, order: keys, pinned: $$('[data-pin-module]:checked', list).map(input => input.dataset.pinModule)};
    };
    const equal = (a, b) => ['enabled','order','pinned'].every(key => JSON.stringify(a[key]) === JSON.stringify(b[key]));
    function announce(text, error = false) {
      status.textContent = text;
      status.closest('.config-toolbar').classList.toggle('has-error', error);
      config.dataset.status = error ? 'error' : saving ? 'saving' : configDirty ? 'dirty' : 'saved';
    }
    function catalog() {
      const chosen = new Set(order());
      $('#chosen-count').textContent = chosen.size;
      $('#chosen-empty').hidden = chosen.size > 0;
      $$('[data-add-module]').forEach(button => { button.disabled = chosen.has(button.dataset.addModule); button.textContent = button.disabled ? '추가됨' : '추가'; });
      $$('.chosen-row', list).forEach((row, i, all) => {
        const handle = $('.drag-handle', row);
        handle.setAttribute('aria-description', `${all.length}개 중 ${i+1}번째. Space로 선택하고 방향키로 옮긴 뒤 Enter로 저장해요. Escape로 되돌려요.`);
        const up = $('[data-order-step="-1"]', row), down = $('[data-order-step="1"]', row);
        if (up) up.disabled = i === 0;
        if (down) down.disabled = i === all.length-1;
      });
    }
    async function save() {
      if (saving || blocked || drag || keyboard || !configDirty) return;
      const payload = {...current(), version}, sentEpoch = epoch;
      saving = true; announce('기능 설정을 저장하고 있어요…');
      try {
        const response = await request(config.dataset.endpoint, {method: 'POST', headers: {...headers(), 'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
        const result = await jsonResponse(response);
        version = result.version; stored = result;
        if (result.revision !== undefined) document.body.dataset.revision = result.revision;
        configDirty = epoch !== sentEpoch || !equal(current(), stored);
        retry.hidden = true; reload.hidden = true;
        saving = false;
        announce(configDirty ? '추가 변경을 저장하고 있어요…' : '저장했어요. 오늘과 돌봄 기능 화면에도 반영돼요.');
        if (configDirty) await save();
      } catch (error) {
        saving = false; blocked = true;
        announce(error.status === 409 ? '다른 참여자가 구성을 바꿨어요. 내 변경은 화면에 남아 있어요. 최신 구성을 먼저 확인해 주세요.' : (error.message === 'Failed to fetch' || error.name === 'AbortError') ? '연결을 확인하지 못했어요. 내 변경은 유지돼요. 연결 후 다시 저장해 주세요.' : error.message, true);
        retry.hidden = error.status === 409 || error.status === 403 || error.status === 404;
        reload.hidden = false;
      }
    }
    function changed() {
      epoch++; configDirty = !equal(current(), stored); catalog();
      clearTimeout(timer);
      if (!blocked) { announce('변경했어요. 저장 중…'); timer = setTimeout(save, 160); }
    }
    async function retrySave() {
      if (saving) return;
      retry.disabled = true;
      try {
        const result = await jsonResponse(await request(config.dataset.endpoint));
        if (equal(current(), result)) {
          stored = result; version = result.version; configDirty = false; blocked = false;
          retry.hidden = reload.hidden = true; announce('저장된 구성을 확인했어요.');
        } else if (result.version !== version) {
          blocked = true; announce('서버의 구성이 바뀌었어요. 내 변경은 유지돼요. 최신 구성을 불러온 뒤 다시 조정해 주세요.', true); retry.hidden = true; reload.hidden = false;
        } else { blocked = false; await save(); }
      } catch (_) { announce('아직 연결하지 못했어요. 내 변경은 그대로 유지돼요.', true); }
      finally { retry.disabled = false; }
    }
    retry.addEventListener('click', retrySave);
    reload.addEventListener('click', async () => {
      if (configDirty && !confirm('저장되지 않은 화면의 변경을 버리고 최신 구성을 불러올까요?')) return;
      try {
        const result = await jsonResponse(await request(config.dataset.endpoint));
        list.replaceChildren();
        result.order.forEach(key => {
          const template = document.getElementById('config-template-'+key);
          if (template) {
            const row = template.content.firstElementChild.cloneNode(true);
            $('[data-pin-module]', row).checked = result.pinned.includes(key); list.append(row);
          }
        });
        stored = result; version = result.version; configDirty = false; blocked = false;
        retry.hidden = reload.hidden = true; catalog(); announce('최신 구성을 불러왔어요.');
      } catch (error) { announce(error.message, true); }
    });
    config.addEventListener('click', event => {
      const add = event.target.closest('[data-add-module]');
      const remove = event.target.closest('[data-remove-module]');
      const move = event.target.closest('[data-order-step]');
      if (drag || keyboard) return;
      if (add && !add.disabled) {
        const key = add.dataset.addModule;
        if (order().includes(key)) return;
        const row = document.getElementById('config-template-'+key).content.firstElementChild.cloneNode(true);
        list.append(row); changed();
        if (mobile.matches) { row.scrollIntoView({block:'center',behavior: reduced.matches ? 'instant' : 'smooth'}); $('.drag-handle', row).focus({preventScroll:true}); }
      }
      if (remove) {
        const row = remove.closest('.chosen-row'), next = row.nextElementSibling || row.previousElementSibling;
        row.remove(); changed();
        (next ? $('.drag-handle',next) : $('#catalog-search'))?.focus({preventScroll:true});
      }
      if (move) {
        const row = move.closest('.chosen-row'), delta = Number(move.dataset.orderStep);
        if (delta < 0 && row.previousElementSibling) list.insertBefore(row, row.previousElementSibling);
        if (delta > 0 && row.nextElementSibling) list.insertBefore(row.nextElementSibling, row);
        changed(); $('.drag-handle', row).focus({preventScroll:true});
      }
    });
    config.addEventListener('change', event => { if (event.target.matches('[data-pin-module]')) changed(); });
    $('#catalog-search').addEventListener('input', event => {
      const query = event.target.value.trim().toLocaleLowerCase('ko'); let visible = 0;
      $$('.catalog-item').forEach(item => { item.hidden = !item.dataset.search.toLocaleLowerCase('ko').includes(query); visible += !item.hidden; });
      $('#catalog-empty').hidden = visible !== 0;
    });
    function reorder(row, before) {
      const prior = new Map($$('.chosen-row', list).filter(item => item !== row).map(item => [item,item.getBoundingClientRect().top]));
      list.insertBefore(row, before);
      if (!reduced.matches) prior.forEach((top,item) => {
        const delta = top - item.getBoundingClientRect().top;
        if (Math.abs(delta) > 1) { item.getAnimations().forEach(animation => animation.cancel()); item.animate([{transform:`translateY(${delta}px)`},{transform:'translateY(0)'}],{duration:140,easing:'ease-out'}); }
      });
    }
    function restoreOrder(keys) { keys.forEach(key => { const row = $$('.chosen-row',list).find(r => r.dataset.key === key); if (row) list.append(row); }); }
    function movePointer(y) {
      if (!drag) return;
      const {row} = drag;
      row.style.transform = '';
      const others = $$('.chosen-row',list).filter(item => item !== row);
      const target = others.find(item => { const r=item.getBoundingClientRect(); return y < r.top+r.height/2; }) || null;
      if (row.nextElementSibling !== target) reorder(row,target);
      const box=row.getBoundingClientRect();
      row.style.transform=`translateY(${y-drag.offset-box.top}px)`;
      drag.y=y; catalog();
    }
    function scrollDrag() {
      if (!drag) return;
      const edge = 80, y = drag.y;
      if (y < edge) window.scrollBy(0, -12);
      else if (y > innerHeight-edge) window.scrollBy(0,12);
      movePointer(y);
      drag.frame=requestAnimationFrame(scrollDrag);
    }
    list.addEventListener('pointerdown', event => {
      const handle=event.target.closest('.drag-handle');
      if (!handle || event.button !== 0 || keyboard || drag) return;
      event.preventDefault(); const row=handle.closest('.chosen-row'), box=row.getBoundingClientRect();
      drag={row,handle,pointer:event.pointerId,initial:order(),offset:event.clientY-box.top,y:event.clientY,frame:0};
      row.classList.add('is-picked'); handle.focus({preventScroll:true});
      document.body.classList.add('module-dragging');
      announce('놓을 위치로 옮겨 주세요. Escape로 되돌릴 수 있어요.');
      drag.frame=requestAnimationFrame(scrollDrag);
    });
    window.addEventListener('pointermove', event => { if (drag && event.pointerId===drag.pointer) { event.preventDefault(); movePointer(event.clientY); } },{passive:false});
    function finishDrag(cancel=false) {
      if (!drag) return;
      const finished=drag; drag=null; cancelAnimationFrame(finished.frame);
      finished.row.classList.remove('is-picked'); finished.row.style.transform='';
      document.body.classList.remove('module-dragging');
      if (cancel) restoreOrder(finished.initial);
      if (!cancel && JSON.stringify(finished.initial)!==JSON.stringify(order())) changed();
      else { catalog(); announce(cancel?'이동을 취소했어요.':'순서를 유지했어요.'); if(configDirty&&!blocked) save(); }
      finished.handle.focus({preventScroll:true});
    }
    window.addEventListener('pointerup', event => { if(drag&&event.pointerId===drag.pointer) finishDrag(); });
    window.addEventListener('pointercancel', event => { if(drag&&event.pointerId===drag.pointer) finishDrag(true); });
    window.addEventListener('blur', () => { if(drag) finishDrag(true); });
    document.addEventListener('keydown', event => { if(event.key==='Escape'&&drag) { event.preventDefault(); finishDrag(true); } });
    list.addEventListener('keydown', event => {
      const handle=event.target.closest('.drag-handle'); if(!handle) return;
      const row=handle.closest('.chosen-row');
      if((event.key===' '||event.key==='Enter')&&!keyboard) {
        event.preventDefault(); keyboard={row,initial:order()}; row.classList.add('is-picked');
        handle.setAttribute('aria-pressed','true'); announce('방향키로 순서를 바꾸고 Enter로 저장해요. Escape로 취소해요.'); return;
      }
      if(!keyboard||keyboard.row!==row) return;
      if(['ArrowUp','ArrowDown','Home','End'].includes(event.key)) {
        event.preventDefault();
        if(event.key==='ArrowUp'&&row.previousElementSibling) reorder(row,row.previousElementSibling);
        if(event.key==='ArrowDown'&&row.nextElementSibling) reorder(row,row.nextElementSibling.nextElementSibling);
        if(event.key==='Home'&&list.firstElementChild!==row) reorder(row,list.firstElementChild);
        if(event.key==='End') reorder(row,null);
        catalog(); handle.focus({preventScroll:true}); row.scrollIntoView({block:'nearest'});
        announce(`${order().indexOf(row.dataset.key)+1}번째 위치예요. Enter로 저장해요.`);
      }
      if(['Enter',' ','Escape','Tab'].includes(event.key)) {
        if(event.key!=='Tab') event.preventDefault();
        const initial=keyboard.initial; keyboard=null; row.classList.remove('is-picked'); handle.removeAttribute('aria-pressed');
        if(event.key==='Escape') { restoreOrder(initial); catalog(); announce('순서 변경을 취소했어요.'); }
        else if(JSON.stringify(initial)!==JSON.stringify(order())) changed();
        if(configDirty&&!blocked) save();
      }
    });
    catalog(); config.dataset.status='saved';
  }
  window.addEventListener('beforeunload', event => {
    if (configDirty || (quickDirty && !quickBusy)) { event.preventDefault(); event.returnValue=''; }
  });
})();
