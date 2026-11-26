// 오늘 0~24시 막대는 서버 시각으로 시작하고, 부분 갱신 뒤에도 새 요소를 따라간다.
(() => {
  'use strict';
  let bar = null, fill = null, line = null, label = null, start = 0, loaded = 0;
  const pad = value => String(value).padStart(2, '0');

  function place() {
    if (!bar?.isConnected || document.hidden) return;
    const sec = Math.min(86399, start + (performance.now() - loaded) / 1000);
    const percent = `${(sec / 864).toFixed(3)}%`;
    const text = `지금 ${pad(Math.floor(sec / 3600))}:${pad(Math.floor(sec / 60) % 60)}`;
    if (fill) fill.style.width = percent;
    if (line) { line.style.left = percent; line.title = text; }
    if (label) label.textContent = text;
  }

  function initialize() {
    const current = document.querySelector('[data-now-sec]');
    if (current === bar) return;
    bar = current;
    if (!bar || !/^\d+$/.test(bar.dataset.nowSec)) { bar = null; return; }
    fill = bar.querySelector('.day-bar-fill');
    line = bar.querySelector('.day-now');
    label = document.querySelector('[data-now-label]');
    start = Number(bar.dataset.nowSec);
    loaded = performance.now();
    place();
  }

  initialize();
  setInterval(place, 30000);
  document.addEventListener('taily:refresh', initialize);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) place(); });
})();
