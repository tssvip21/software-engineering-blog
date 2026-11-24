(() => {
  const menu = document.getElementById('phone-menu');
  if (!menu) return;
  document.querySelector('.phone-menu-trigger').addEventListener('click', () => menu.showModal());
  menu.addEventListener('click', event => {
    if (event.target.closest('a[href]')) menu.close();
  });
})();
