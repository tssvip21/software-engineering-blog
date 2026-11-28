/* Same-room reading pages share a shell; every visit still checks the server. */
(() => {
  "use strict";
  const body = document.body;
  const roomState = body.dataset.roomState;
  const context = body.dataset.navigationContext;
  const csrfCookie = body.dataset.csrfCookie;
  const root = roomState?.match(/^((?:\/try)?\/rooms\/[0-9a-f-]+\/)state\/$/i)?.[1];
  if (!root || !context || !csrfCookie || !window.fetch || !window.AbortController) return;

  const links = "#sidebar nav a[href], #sidebar .sidebar-members a[href], " +
    ".mobile-dock a[href], [data-page-navigation] a[href], .workspace-topbar a[href], " +
    ".pn-week a[href], .pn-seg a[href], .pn-chips a[href]";
  const readPage = /^(?:(?:modules|records|routines|supplies|overview|members|navigation)\/|modules\/[a-z_]+\/(?:statistics\/)?)?$/;
  const sensitiveForms = "#module-config-form, #group-editor, #main .quick-standalone, #main [data-dirty-form], #main [data-input-form]";
  const shellDialogs = ["quick-dialog", "category-dialog", "input-dialog", "member-dialog7", "phone-menu"];
  const cookie = () => document.cookie.split(/;\s*/).find(part => part.startsWith(csrfCookie + "=")) || "";
  const initialCookie = cookie();
  const assets = page => JSON.stringify([...page.head.querySelectorAll('script[src], link[rel="stylesheet"]')]
    .map(node => [node.tagName, node.getAttribute("src") || node.getAttribute("href")]));
  const initialAssets = assets(document);
  let epoch = 0, pending = null, restoringHistory = false;
  let currentIndex = Number.isInteger(history.state?.petnoteNavigation?.index)
    ? history.state.petnoteNavigation.index : 0;
  let displayedURL = location.href;
  const positions = new Map();
  const originalScrollRestoration = history.scrollRestoration;
  history.scrollRestoration = "manual";
  function rememberPosition() {
    positions.set(currentIndex, [scrollX, scrollY]);
    if (positions.size > 50) positions.delete(positions.keys().next().value);
  }

  const status = document.createElement("div");
  status.id = "page-navigation-status";
  status.className = "sr-only";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const progress = document.createElement("div");
  progress.id = "page-navigation-progress";
  progress.setAttribute("aria-hidden", "true");
  body.append(status, progress);

  function pageURL(value) {
    try {
      const url = new URL(value, location.href);
      if (url.origin !== location.origin || url.username || url.password || url.hash ||
          !url.pathname.startsWith(root) || !readPage.test(url.pathname.slice(root.length))) return null;
      const layout = url.searchParams.get("layout");
      if (layout && layout !== (body.dataset.layout || "desktop")) return null;
      return url;
    } catch (_) { return null; }
  }

  function savePosition() {
    if (location.href !== displayedURL) return;
    rememberPosition();
    const previous = history.state && typeof history.state === "object" ? history.state : {};
    history.replaceState({...previous, petnoteNavigation: {
      index: currentIndex, scroll: [scrollX, scrollY],
    }}, "", displayedURL);
  }
  if (pageURL(location.href)) savePosition();

  function unsaved() {
    return Boolean(window.PetnoteForms?.hasUnsavedChanges());
  }

  function canVisit() {
    if (!pageURL(displayedURL.split("#")[0]) || document.querySelector(sensitiveForms) ||
        document.querySelector('dialog[open]:not(#phone-menu)') || unsaved() ||
        cookie() !== initialCookie) return false;
    return !document.querySelector(
      '[data-submitting="1"], [data-saving="1"], [data-busy="1"], ' +
      'form[aria-busy="true"], button[aria-busy="true"]'
    );
  }

  function clearPending() {
    if (!pending) return;
    pending.controller.abort();
    clearTimeout(pending.timeout);
    pending.link?.removeAttribute("data-navigation-pending");
    if (pending.main?.isConnected) {
      pending.main.inert = pending.wasInert;
      if (pending.wasBusy === null) pending.main.removeAttribute("aria-busy");
      else pending.main.setAttribute("aria-busy", pending.wasBusy);
    }
    delete body.dataset.navigationPending;
    status.textContent = "";
    pending = null;
  }

  function cancel() {
    ++epoch;
    clearPending();
  }

  function compatible(page) {
    const next = page.getElementById("main");
    if (!next || page.querySelector(sensitiveForms) || page.body.dataset.roomState !== roomState ||
        page.body.dataset.navigationContext !== context || page.body.dataset.csrfCookie !== csrfCookie ||
        page.body.dataset.layout !== body.dataset.layout ||
        page.body.dataset.actionEndpoint7 !== body.dataset.actionEndpoint7 ||
        page.body.dataset.noticeEndpoint7 !== body.dataset.noticeEndpoint7 ||
        assets(page) !== initialAssets || cookie() !== initialCookie) return false;
    if (shellDialogs.some(id => Boolean(page.getElementById(id)) !== Boolean(document.getElementById(id)))) return false;
    if (Boolean(page.querySelector(".mobile-dock")) !== Boolean(document.querySelector(".mobile-dock"))) return false;
    if ([...next.querySelectorAll("script")].some(node => node.type !== "application/json")) return false;
    return true;
  }

  function syncShell(page) {
    const selectors = "#sidebar a[href], #phone-menu a[href], .workspace-topbar a[href]";
    const incomingLinks = page.querySelectorAll(selectors);
    document.querySelectorAll(selectors).forEach((link, index) => {
      const source = incomingLinks[index];
      if (!source || source.getAttribute("href") !== link.getAttribute("href")) return;
      const value = source.getAttribute("aria-current");
      if (value) link.setAttribute("aria-current", value);
      else link.removeAttribute("aria-current");
    });
    for (const selector of [".sidebar-members", ".mobile-dock"]) {
      const next = page.querySelector(selector), current = document.querySelector(selector);
      if (next && current) current.replaceWith(document.importNode(next, true));
    }
    for (const selector of [".workspace-name", ".topbar-role"]) {
      const incoming = page.querySelectorAll(selector);
      document.querySelectorAll(selector).forEach((node, index) => {
        if (incoming[index]) node.textContent = incoming[index].textContent;
      });
    }
    for (const selector of [".pn-carers", "#room-switch"]) {
      const next = page.querySelector(selector), current = document.querySelector(selector);
      if (next && current) {
        current.replaceChildren(...[...next.childNodes].map(node => document.importNode(node, true)));
        if (current instanceof HTMLSelectElement) current.value = next.value;
        if (next.hasAttribute("aria-label")) current.setAttribute("aria-label", next.getAttribute("aria-label"));
      }
    }
    const csrf = page.querySelector('[name="csrfmiddlewaretoken"]')?.value;
    if (csrf) document.querySelectorAll('[name="csrfmiddlewaretoken"]').forEach(field => { field.value = csrf; });
    body.dataset.revision = page.body.dataset.revision || "";
    body.dataset.tone = page.body.dataset.tone || "default";
  }

  function fallback(url, replace = false) {
    cancel();
    if (replace) location.replace(url);
    else location.assign(url);
  }

  async function load(url, options) {
    const previousScroll = [scrollX, scrollY];
    cancel();
    const serial = epoch;
    const controller = new AbortController();
    const main = document.getElementById("main");
    if (!main) { fallback(url.href, options.history === "none"); return; }
    pending = {controller, main, link: options.link, wasBusy: main.getAttribute("aria-busy"),
      wasInert: main.inert, timeout: setTimeout(() => controller.abort(), 10000)};
    body.dataset.navigationPending = "";
    options.link?.setAttribute("data-navigation-pending", "");
    main.setAttribute("aria-busy", "true");
    main.inert = true;
    status.textContent = "화면을 불러오는 중이에요.";
    document.dispatchEvent(new CustomEvent("petnote:navigation-start", {detail: {url: url.href}}));
    try {
      const response = await fetch(url.href, {credentials: "same-origin", mode: "same-origin",
        cache: "no-store", redirect: "error", signal: controller.signal});
      if (serial !== epoch) return;
      if (!response.ok || response.url !== url.href ||
          !response.headers.get("Content-Type")?.includes("text/html")) {
        fallback(url.href, options.history === "none"); return;
      }
      const html = await response.text();
      if (serial !== epoch) return;
      const page = new DOMParser().parseFromString(html, "text/html");
      if (!compatible(page)) { fallback(url.href, options.history === "none"); return; }
      const next = document.importNode(page.getElementById("main"), true);
      if (options.history !== "none") {
        savePosition();
        if (options.history === "replace") history.replaceState(
          {petnoteNavigation: {index: currentIndex, scroll: [0, 0]}}, "", url.href);
        else history.pushState({petnoteNavigation: {index: ++currentIndex, scroll: [0, 0]}}, "", url.href);
      } else currentIndex = options.index;
      displayedURL = url.href;
      clearPending();
      main.replaceWith(next);
      document.title = page.title || document.title;
      syncShell(page);
      // The location is committed first: initializers key local choices by URL.
      document.dispatchEvent(new Event("taily:refresh"));
      const heading = next.querySelector("h1") || next;
      heading.setAttribute("tabindex", "-1");
      heading.focus({preventScroll: true});
      const position = options.scroll || (options.preserveScroll ? previousScroll : [0, 0]);
      window.scrollTo({left: position[0], top: position[1], behavior: "instant"});
      document.dispatchEvent(new CustomEvent("petnote:navigated", {detail: {url: url.href}}));
    } catch (_) {
      if (serial === epoch) fallback(url.href, options.history === "none");
    } finally {
      if (serial === epoch) clearPending();
    }
  }

  function navigate(value, options = {}) {
    const url = pageURL(value);
    if (!url || !canVisit()) return false;
    if (url.href === displayedURL && options.history !== "none") {
      cancel();
      return true;
    }
    void load(url, options);
    return true;
  }

  window.PetnoteNavigation = Object.freeze({
    navigate,
    snapshot: () => ({epoch, url: location.href, pending: Boolean(pending)}),
    isCurrent: snapshot => !pending && !snapshot.pending && snapshot.epoch === epoch && snapshot.url === location.href,
  });

  document.addEventListener("click", event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest(links);
    if (!link || link.hasAttribute("download") || (link.target && link.target !== "_self") ||
        link.relList.contains("external") || link.matches("[data-member-open7], [data-quick]")) return;
    if (navigate(link.href, {link})) event.preventDefault();
  });
  // An in-flight read must never replace the result of a newly started write.
  document.addEventListener("submit", () => cancel(), true);
  document.addEventListener("click", event => {
    if (event.target.closest('[data-action7], [data-toast-undo7], [data-feedback-undo], [data-dismiss-notice7]')) cancel();
    const link = event.target.closest("a[href]");
    if (pending && link && event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey &&
        !link.hasAttribute("download") && (!link.target || link.target === "_self")) cancel();
  }, true);
  document.addEventListener("change", event => {
    if (pending && event.target.matches("select[data-navigate]")) cancel();
  }, true);
  window.addEventListener("pagehide", () => {
    cancel();
    history.scrollRestoration = originalScrollRestoration;
  });
  window.addEventListener("pageshow", () => {
    history.scrollRestoration = "manual";
    clearPending();
    displayedURL = location.href;
  });
  window.addEventListener("popstate", event => {
    if (restoringHistory) { restoringHistory = false; return; }
    const previousURL = new URL(displayedURL), nextURL = new URL(location.href);
    if (previousURL.pathname === nextURL.pathname && previousURL.search === nextURL.search &&
        previousURL.hash !== nextURL.hash) {
      cancel();
      displayedURL = location.href;
      return;
    }
    const state = event.state?.petnoteNavigation;
    if (!state || !Number.isInteger(state.index) || !pageURL(location.href)) {
      fallback(location.href, true); return;
    }
    rememberPosition();
    cancel();
    const activeInput = document.querySelector('dialog[open]:not(#phone-menu), [data-submitting="1"], ' +
      '[data-saving="1"], [data-busy="1"], form[aria-busy="true"], button[aria-busy="true"]');
    if (activeInput || (unsaved() && !confirm("저장하지 않은 입력을 두고 다른 화면으로 이동할까요?"))) {
      const delta = currentIndex - state.index;
      if (delta) { restoringHistory = true; history.go(delta); }
      else history.replaceState(event.state, "", displayedURL);
      if (activeInput) status.textContent = "입력이나 저장을 마친 뒤 이동해 주세요.";
      return;
    }
    const scroll = positions.get(state.index) || state.scroll;
    const position = Array.isArray(scroll) && scroll.length === 2 && scroll.every(Number.isFinite)
      ? scroll : [0, 0];
    currentIndex = state.index;
    // The browser already changed the history entry; do not push another entry.
    void load(pageURL(location.href), {history: "none", index: state.index, scroll: position});
  });
})();
