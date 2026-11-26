/* The full menu link remains usable without script support. */
(() => {
  "use strict";

  // iOS Safari applies :active (press feedback) only once some touch listener exists.
  document.addEventListener("touchstart", () => {}, { passive: true });

  const menu = document.getElementById("phone-menu");
  const triggers = [...document.querySelectorAll(".phone-menu-trigger")];
  if (!menu || !triggers.length || typeof menu.showModal !== "function") return;

  let opener = null;
  let followingLink = false;

  function syncState() {
    document.body.classList.toggle("phone-menu-open", menu.open);
    triggers.forEach((trigger) => {
      trigger.setAttribute("aria-expanded", String(menu.open));
    });
  }

  triggers.forEach((trigger) => {
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.addEventListener("click", (event) => {
      // Modified link clicks keep the browser's normal new-tab behavior.
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      if (menu.open) {
        event.preventDefault();
        return;
      }
      opener = trigger;
      followingLink = false;
      try {
        menu.showModal();
      } catch (_error) {
        // Preserve the anchor's real destination if the dialog cannot open.
        return;
      }
      event.preventDefault();
      syncState();
    });
  });

  menu.addEventListener("close", () => {
    syncState();
    if (!followingLink && opener?.isConnected) opener.focus({preventScroll: true});
    opener = null;
  });

  menu.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (link && !event.defaultPrevented) {
      followingLink = true;
      menu.close();
      return;
    }
    // A click in dialog whitespace should not be mistaken for its backdrop.
    if (event.target !== menu) return;
    const box = menu.getBoundingClientRect();
    const outside = event.clientX < box.left || event.clientX > box.right ||
      event.clientY < box.top || event.clientY > box.bottom;
    if (outside) menu.close();
  });

  window.addEventListener("pageshow", () => {
    if (menu.open) menu.close();
    followingLink = false;
    syncState();
  });

  syncState();
})();
