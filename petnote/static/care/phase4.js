/* Category-local editing keeps changes separate until the user applies them. */
(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const dialog = $('#category-dialog');
  const form = $('#category-form');
  const choices = $('#category-options');
  const status = $('#category-status');
  const reload = $('#category-reload');
  let state = null, endpoint = '', category = '', dirty = false, busy = false, generation = 0;
  const token = () => $('[name="csrfmiddlewaretoken"]')?.value || '';
  const selected = () => $$('[data-category-choice]:checked', choices).map(x => x.value);
  const same = (a, b) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
  async function call(url, body) {
    const target = new URL(url, location.href);
    if (target.origin !== location.origin) throw new Error('요청 주소를 확인해 주세요.');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 18000);
    try {
      const response = await fetch(target, {method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', mode: 'same-origin', cache: 'no-store', signal: controller.signal,
        headers: {'X-CSRFToken': token(), 'Content-Type': 'application/json'}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
      if (!(response.headers.get('Content-Type') || '').includes('application/json')) {
        throw Object.assign(new Error(response.status === 403 ? '이 설정을 바꿀 권한이 없어요. 참여 상태를 확인해 주세요.' : '설정을 불러오지 못했어요. 다시 시도해 주세요.'), {status: response.status});
      }
      const data = await response.json();
      if (!response.ok) throw Object.assign(new Error(data.message || '변경 내용을 확인해 주세요.'), {status: response.status});
      return data;
    } finally { clearTimeout(timer); }
  }
  function message(text, failed = false) {
    status.textContent = text; status.classList.toggle('form-errors', failed);
    dialog.dataset.status = failed ? 'error' : busy ? 'saving' : 'ready';
  }
  function draw(data) {
    state = data; dirty = false;
    $('#category-title').textContent = data.title + ' 기능 선택';
    choices.replaceChildren();
    const legend = document.createElement('legend'); legend.className = 'sr-only'; legend.textContent = '사용할 기능'; choices.append(legend);
    data.items.forEach(item => {
      const label = document.createElement('label'); label.className = 'category-choice';
      const input = document.createElement('input'); input.type = 'checkbox'; input.value = item.key; input.checked = item.selected; input.dataset.categoryChoice = '';
      const text = document.createElement('span'), title = document.createElement('strong'), description = document.createElement('small');
      title.textContent = item.name; description.textContent = item.description; text.append(title, description); label.append(input, text); choices.append(label);
    });
    $('button[type="submit"]', form).disabled = false; reload.hidden = true; message('선택한 기능만 이 분류에 적용해요.');
  }
  async function open(button) {
    if (busy) return;
    category = button.dataset.categoryManage; endpoint = button.dataset.endpoint;
    state = null; dirty = false; const seq = ++generation;
    $('#category-title').textContent = button.getAttribute('aria-label') || '기능 선택';
    choices.replaceChildren(); $('button[type="submit"]', form).disabled = true; reload.hidden = true;
    if (!dialog.open) dialog.showModal();
    message('기능 목록을 불러오고 있어요.');
    $('#category-title').tabIndex = -1; $('#category-title').focus();
    try { const data = await call(endpoint); if (seq === generation && dialog.open) draw(data); }
    catch (error) { if (seq === generation) { message('기능 목록을 불러오지 못했어요. 다시 불러와 주세요.', true); reload.hidden = false; } }
  }
  function close(force = false) {
    if (!dialog?.open || (busy && !force)) return;
    if (dirty && !force && !confirm('선택한 내용을 적용하지 않고 닫을까요?')) return;
    generation++; dialog.close(); dirty = false;
    $(`[data-category-manage="${category}"]`)?.focus({preventScroll: true});
  }
  async function refresh() {
    const response = await fetch(location.href, {credentials: 'same-origin', cache: 'no-store'});
    if (!response.ok) throw new Error('변경은 저장했지만 화면을 불러오지 못했어요. 새로고침해 주세요.');
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    const next = doc.getElementById('main');
    if (!next || doc.body.dataset.roomState !== document.body.dataset.roomState) throw new Error('참여 상태가 바뀌었어요. 새로고침해서 확인해 주세요.');
    const y = scrollY;
    $('#main').replaceWith(document.importNode(next, true));
    document.body.dataset.revision = doc.body.dataset.revision || '';
    document.dispatchEvent(new Event('taily:refresh'));
    scrollTo({top: y, behavior: 'instant'});
  }
  function finishMessage(text) {
    const box = $('#action-feedback'); if (!box) return;
    $('[data-feedback-message]', box).textContent = text;
    $('[data-feedback-link]', box).hidden = true; $('[data-feedback-undo]', box).hidden = true; box.hidden = false;
  }
  if (dialog) {
    document.addEventListener('click', event => {
      const button = event.target.closest('[data-category-manage]');
      if (button) { open(button); return; }
      if (event.target.closest('[data-category-close]')) close();
    });
    dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const items = $$('button,input,select,textarea,a[href],[tabindex="0"]', dialog)
        .filter(node => !node.matches(':disabled') && node.tabIndex >= 0 && node.getClientRects().length);
      if (!items.length) { event.preventDefault(); $('#category-title').focus(); return; }
      const first = items[0], last = items[items.length - 1], focused = document.activeElement;
      if (event.shiftKey && (focused === first || !items.includes(focused))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && focused === last) { event.preventDefault(); first.focus(); }
    });
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) close();
    });
    choices.addEventListener('change', () => { dirty = state && !same(selected(), state.items.filter(i => i.selected).map(i => i.key)); message(dirty ? '적용을 누르면 변경돼요.' : '선택을 바꾸지 않았어요.'); });
    reload.addEventListener('click', async () => {
      if (busy || (dirty && !confirm('내 선택을 취소하고 최신 설정을 불러올까요?'))) return;
      const seq = generation, requested = endpoint;
      try { const data = await call(requested); if (seq === generation && dialog.open) draw(data); } catch (error) { if (seq === generation && dialog.open) message(error.message, true); }
    });
    form.addEventListener('submit', async event => {
      event.preventDefault(); if (busy || !state) return;
      busy = true; const button = $('button[type="submit"]', form); button.disabled = true;
      const desired = selected(); choices.disabled = true; message('선택한 기능을 적용하고 있어요.');
      let saved = false;
      try {
        try { await call(endpoint, {version: state.version, selected: desired}); saved = true; }
        catch (error) {
          if (!error.status) {
            try { const latest = await call(endpoint); saved = same(latest.items.filter(i => i.selected).map(i => i.key), desired); } catch (_) { /* Keep the unconfirmed selection for retry. */ }
          }
          if (!saved) throw error;
        }
        dirty = false;
        await refresh(); close(true); finishMessage('선택한 기능을 적용했어요. 이전 기록은 그대로예요.');
      } catch (error) {
        if (saved) { close(true); finishMessage(error.message); }
        else { message(error.status === 409 ? error.message : error.name === 'AbortError' || error.message === 'Failed to fetch' ? '저장 여부를 확인하지 못했어요. 선택은 유지했으니 연결 후 다시 적용해 주세요.' : error.message, true); reload.hidden = false; }
      } finally { busy = false; choices.disabled = false; button.disabled = false; }
    });
  }
  // Back links restore a place in a category; only a scroll offset is stored.
  const scrollKey = () => 'taily.records.scroll:' + location.pathname + location.search;
  document.addEventListener('click', event => {
    if (event.target.closest('[data-record-link]')) { try { sessionStorage.setItem(scrollKey(), String(scrollY)); } catch (_) {} }
  });
  if ($('.record-categories')) {
    try { const y = Number(sessionStorage.getItem(scrollKey())); if (Number.isFinite(y) && y > 0) requestAnimationFrame(() => scrollTo({top: y, behavior: 'instant'})); } catch (_) {}
  }
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
})();
