/* Card library and live layout preview. All writes use the existing versioned configuration API. */
(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const library = $('#module-library');
  let origin = null;
  if (library) {
    function closeLibrary() {
      if (!library.open) return;
      library.close();
      if (origin?.isConnected) origin.focus({preventScroll: true});
    }
    document.addEventListener('click', event => {
      const open = event.target.closest('[data-library-open]');
      if (open) {
        origin = open;
        if (!library.open) library.showModal();
        $('#catalog-search', library)?.focus({preventScroll: true});
      }
      if (event.target.closest('[data-library-close]')) closeLibrary();
      const add = event.target.closest('[data-add-module]');
      if (add && library.open) queueMicrotask(() => {
        const label = add.closest('.catalog-item')?.querySelector('h3')?.textContent || '기능';
        $('[data-library-status]').textContent = `${label}을 보드에 추가했어요. 저장 상태를 확인하고 있어요.`;
        if (add.disabled) ($$('[data-add-module]:not(:disabled)', library)[0] || $('[data-library-close]', library))?.focus({preventScroll: true});
      });
    });
    library.addEventListener('cancel', event => {event.preventDefault();closeLibrary();});
    library.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const items = $$('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex="0"]', library)
        .filter(el => el.getClientRects().length && !el.closest('[hidden]'));
      const first = items[0], last = items[items.length - 1];
      if (!first) {event.preventDefault();return;}
      if (event.shiftKey && (document.activeElement === first || !library.contains(document.activeElement))) {
        event.preventDefault();last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !library.contains(document.activeElement))) {
        event.preventDefault();first.focus();
      }
    });
    library.addEventListener('click', event => {
      if (event.target !== library) return;
      const r = library.getBoundingClientRect();
      if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) closeLibrary();
    });
    const status = $('#config-status');
    if (status) new MutationObserver(() => {
      if (library.open) $('[data-library-status]').textContent = status.textContent;
    }).observe(status, {childList: true, subtree: true, characterData: true});
  }
  const list = $('#chosen-modules');
  const preview = $('[data-layout-preview]');
  if (list && preview) {
    function renderPreview() {
      const rows = $$('.chosen-row', list).filter(row => $('[data-pin-module]', row)?.checked);
      preview.replaceChildren(...rows.map((row, i) => {
        const card = document.createElement('div');
        const number = document.createElement('small');
        number.textContent = String(i + 1).padStart(2, '0');
        const name = document.createElement('span');
        name.textContent = $('.chosen-label > strong', row)?.textContent || '';
        card.append(number, name);
        return card;
      }));
      const empty = $('[data-preview-empty]');
      if (empty) empty.hidden = rows.length !== 0;
    }
    new MutationObserver(renderPreview).observe(list, {childList: true});
    list.addEventListener('change', renderPreview);
    renderPreview();
  }
})();
