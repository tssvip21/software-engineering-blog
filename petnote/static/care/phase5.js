/* Presentation-only affordances. Saves and permissions remain in the existing handlers. */
(() => {
  'use strict';
  function disclosureLabel(details) {
    if (!details.matches('.status-result')) return;
    const label = details.querySelector('.result-open');
    if (label) label.textContent = details.open ? '접기' : '날짜·값 보기';
  }
  function refreshLabels() {
    document.querySelectorAll('.status-result').forEach(disclosureLabel);
  }
  document.addEventListener('toggle', event => {
    if (event.target instanceof HTMLDetailsElement) disclosureLabel(event.target);
  }, true);
  document.addEventListener('taily:refresh', refreshLabels);
  refreshLabels();

  const dock = document.querySelector('.mobile-dock');
  if (dock) {
    const syncMenu = () => {
      const menuOpen = document.body.classList.contains('mobile-menu-open');
      dock.inert = menuOpen;
      if (menuOpen) dock.setAttribute('aria-hidden', 'true');
      else dock.removeAttribute('aria-hidden');
    };
    new MutationObserver(syncMenu).observe(document.body, {attributes: true, attributeFilter: ['class']});
    syncMenu();
  }
})();
