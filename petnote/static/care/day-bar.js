// 오늘 화면의 0~24시 막대. 처음 열 때 0시에서 지금까지 차오르고, 열어 둔 동안 30초마다 실제 시간을 따라간다.
// 시작 값은 서버가 넘긴 지금 시각(초)이라 브라우저 시계가 달라도 화면의 다른 시각 표시와 맞는다.
(() => {
  const bar = document.querySelector('[data-now-sec]');
  if (!bar || !/^\d+$/.test(bar.dataset.nowSec)) return;
  const fill = bar.querySelector('.day-bar-fill');
  const line = bar.querySelector('.day-now');
  const label = document.querySelector('[data-now-label]');
  const start = Number(bar.dataset.nowSec);
  const loaded = performance.now();
  const pad = (n) => String(n).padStart(2, '0');

  const place = () => {
    const sec = Math.min(86399, start + (performance.now() - loaded) / 1000);
    const pct = `${(sec / 864).toFixed(3)}%`;
    if (fill) fill.style.width = pct;
    if (line) line.style.left = pct;
    if (label) label.textContent = `지금 ${pad(Math.floor(sec / 3600))}:${pad(Math.floor(sec / 60) % 60)}`;
  };

  if (fill) fill.style.width = '0%';
  if (line) line.style.left = '0%';
  requestAnimationFrame(() => requestAnimationFrame(place));
  setInterval(place, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) place(); });
})();
