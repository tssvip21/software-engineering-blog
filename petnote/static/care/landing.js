// 첫 화면 히어로의 예시 휴대폰. 하루 기록이 몇 초마다 하나씩 맨 위에 쌓이고, 저녁까지 다 쌓이면 아침 상태로 돌아가 다시 시작한다.
(() => {
  const feed = document.querySelector('[data-landing-feed]');
  const alert = document.querySelector('[data-landing-alert]');
  if (!feed || !alert || matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const EVERY = 6000;
  const next = [
    {av: '엄', who: '엄마', text: '저녁밥을 줬어요', time: '18:40', sub: '사료 55g', alert: ['19:30', '저녁 산책 갈 시간이에요']},
    {av: '도', who: '도윤', text: '저녁 산책을 다녀왔어요', time: '19:30', sub: '32분 · 2.1km', alert: ['21:20', '자기 전 양치할 시간이에요']},
    {av: '지', who: '지우', text: '양치를 했어요', time: '21:20', sub: '자기 전', alert: [null, '오늘 할 일을 모두 마쳤어요']},
  ];
  const startItems = feed.innerHTML;
  const startAlert = alert.querySelector('span').innerHTML;
  let step = 0;
  let visible = true;

  const item = (r) => {
    const li = document.createElement('li');
    const div = document.createElement('div');
    const mark = document.createElement('span');
    mark.className = 'person-mark';
    mark.textContent = r.av;
    const p = document.createElement('p');
    const who = document.createElement('strong');
    who.textContent = r.who;
    const small = document.createElement('small');
    const time = document.createElement('time');
    time.textContent = r.time;
    small.append(time, ` · ${r.sub}`);
    p.append(who, ` ${r.text}`, small);
    div.append(mark, p);
    li.append(div);
    return li;
  };

  const setAlert = (html) => {
    alert.querySelector('span').innerHTML = html;
    alert.classList.remove('is-changing');
    void alert.offsetWidth;
    alert.classList.add('is-changing');
  };

  const push = () => {
    const r = next[step];
    feed.classList.add('is-live');
    const li = item(r);
    li.classList.add('is-entering');
    feed.prepend(li);
    const last = feed.lastElementChild;
    last.classList.add('is-leaving');
    setTimeout(() => last.remove(), 400);
    const [t, text] = r.alert;
    const span = document.createElement('span');
    if (t) {
      const time = document.createElement('time');
      time.textContent = t;
      span.append(time, ` ${text}`);
    } else {
      span.textContent = text;
    }
    setAlert(span.innerHTML);
    step += 1;
  };

  const reset = () => {
    feed.classList.add('is-resetting');
    setTimeout(() => {
      feed.innerHTML = startItems;
      feed.classList.remove('is-resetting');
      setAlert(startAlert);
      step = 0;
    }, 400);
  };

  new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; }).observe(feed);

  setInterval(() => {
    if (document.hidden || !visible) return;
    if (step < next.length) push();
    else reset();
  }, EVERY);
})();
