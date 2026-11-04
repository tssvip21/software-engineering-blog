/* Progressive enhancement: the server remains the authority for data and permissions. */
(() => {
  "use strict";
  const dirtyForms = new Set();
  let navigating = false;
  const formValue = (form) => JSON.stringify(Array.from(new FormData(form).entries()).filter(([key]) => key !== "csrfmiddlewaretoken"));

  function initialize(root = document) {
    root.querySelectorAll("form[data-dirty-form]").forEach((form) => {
      if (form.dataset.initialized) return;
      form.dataset.initialized = "1";
      form.dataset.initialValue = formValue(form);
      const update = () => {
        if (formValue(form) === form.dataset.initialValue) dirtyForms.delete(form);
        else dirtyForms.add(form);
      };
      form.addEventListener("input", update);
      form.addEventListener("change", update);
    });
    const firstError = root.querySelector(".form-errors, .field-error");
    if (firstError) {
      firstError.setAttribute("tabindex", "-1");
      firstError.focus({ preventScroll: true });
      firstError.scrollIntoView({ block: "center", behavior: "instant" });
    }
  }

  function setBusy(form, busy) {
    form.dataset.submitting = busy ? "1" : "0";
    form.querySelectorAll('button[type="submit"], button:not([type])').forEach((button) => {
      button.disabled = busy;
    });
  }

  function updateOrder() {
    const list = document.getElementById("module-order-list");
    const input = document.getElementById("module-order");
    if (list && input) {
      input.value = JSON.stringify(Array.from(list.children).map((row) => row.dataset.key));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function updateRoutineOrder(form) {
    const input = form.querySelector('[name="module_order"]');
    if (!input) return;
    input.value = JSON.stringify(Array.from(form.querySelectorAll('.choice-order-row input[type="checkbox"]:checked')).map((field) => field.value));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.submitting === "1") { event.preventDefault(); return; }
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) { event.preventDefault(); return; }
    if (form.id === "module-settings-form") updateOrder();
    if (form.hasAttribute("data-routine-form")) updateRoutineOrder(form);
    if (!form.hasAttribute("data-dirty-form")) {
      navigating = true;
      return;
    }
    event.preventDefault();
    if (!form.reportValidity()) return;
    const payload = new FormData(form);
    if (event.submitter?.name) payload.append(event.submitter.name, event.submitter.value);
    const status = form.querySelector(".save-status");
    if (status) status.textContent = "저장 요청 중…";
    setBusy(form, true);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(form.action || location.href, {
        method: "POST", body: payload, credentials: "same-origin", mode: "same-origin",
        signal: controller.signal, headers: { "X-Requested-With": "fetch" },
      });
      if (response.redirected) {
        const destination = new URL(response.url);
        if (destination.origin !== location.origin) throw new Error("Unexpected redirect");
        dirtyForms.delete(form);
        navigating = true;
        location.assign(destination.href);
        return;
      }
      const contentType = response.headers.get("Content-Type") || "";
      if (!contentType.includes("text/html")) throw new Error("Unexpected response");
      const html = await response.text();
      const parsed = new DOMParser().parseFromString(html, "text/html");
      const next = parsed.getElementById("main");
      if (!next) throw new Error("Incomplete response");
      document.getElementById("main").replaceWith(document.importNode(next, true));
      document.title = parsed.title || document.title;
      dirtyForms.delete(form);
      initialize(document.getElementById("main"));
    } catch (_error) {
      if (status) status.textContent = "서버 응답을 확인하지 못했습니다. 입력은 유지됩니다. 기록 목록을 확인하거나 같은 화면에서 다시 저장해 주세요.";
      setBusy(form, false);
    } finally {
      clearTimeout(timeout);
    }
  });

  window.addEventListener("beforeunload", (event) => {
    for (const form of dirtyForms) if (!document.contains(form)) dirtyForms.delete(form);
    if (!navigating && dirtyForms.size) { event.preventDefault(); event.returnValue = ""; }
  });
  window.addEventListener("pageshow", () => {
    navigating = false;
    document.querySelectorAll('form[data-submitting="1"]').forEach((form) => setBusy(form, false));
  });

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (target.matches("select[data-navigate]") && target.value) {
      const url = new URL(target.value, location.origin);
      if (url.origin === location.origin) location.assign(url.href);
    }
    if (target.id === "id_metric") {
      const units = { temperature: ["C", "F"], humidity: ["percent"], light: ["hours"], ph: ["ph"] }[target.value];
      const unitField = document.getElementById("id_unit");
      if (unitField && units && !units.includes(unitField.value)) {
        unitField.value = units[0];
        unitField.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
  });

  document.addEventListener("input", (event) => {
    if (!event.target.matches("[data-module-search]")) return;
    const query = event.target.value.trim().toLocaleLowerCase("ko");
    let total = 0;
    document.querySelectorAll("[data-module-search-item]").forEach((item) => {
      const found = item.dataset.moduleSearchItem.toLocaleLowerCase("ko").includes(query);
      item.hidden = !found; total += found ? 1 : 0;
    });
    document.querySelectorAll(".module-section").forEach((section) => {
      section.hidden = !section.querySelector('[data-module-search-item]:not([hidden])');
    });
    const empty = document.getElementById("module-no-results");
    if (empty) empty.hidden = total > 0;
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("button");
    if (!button) return;
    if (button.hasAttribute("data-refresh")) { location.reload(); return; }
    if (button.dataset.copy) {
      const input = document.getElementById(button.dataset.copy);
      const output = button.closest(".invite-result")?.querySelector(".copy-status");
      try {
        await navigator.clipboard.writeText(input.value);
        if (output) output.textContent = "초대 링크를 복사했습니다.";
      } catch (_error) {
        input.focus(); input.select();
        const copied = document.execCommand("copy");
        if (output) output.textContent = copied ? "초대 링크를 복사했습니다." : "선택된 링크를 직접 복사해 주세요.";
      }
    }
    const direction = button.dataset.move || button.dataset.choiceMove;
    if (direction) {
      const row = button.closest(".module-order-row, .choice-order-row");
      if (!row) return;
      if (direction === "up" && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
      if (direction === "down" && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
      button.focus();
      updateOrder();
      const form = row.closest("form[data-routine-form]");
      if (form) updateRoutineOrder(form);
    }
  });

  let dragged = null;
  document.addEventListener("dragstart", (event) => {
    const row = event.target.closest(".module-order-row");
    if (!row) return;
    dragged = row; row.classList.add("is-dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", row.dataset.key);
  });
  document.addEventListener("dragover", (event) => {
    const row = event.target.closest(".module-order-row");
    if (!dragged || !row || row === dragged) return;
    event.preventDefault();
    const bounds = row.getBoundingClientRect();
    row.parentNode.insertBefore(dragged, event.clientY < bounds.top + bounds.height / 2 ? row : row.nextSibling);
  });
  const endDrag = () => {
    if (dragged) { dragged.classList.remove("is-dragging"); dragged = null; updateOrder(); }
  };
  document.addEventListener("drop", (event) => { if (dragged) event.preventDefault(); endDrag(); });
  document.addEventListener("dragend", endDrag);

  const endpoint = document.body.dataset.roomState;
  const initialRevision = document.body.dataset.revision;
  async function refreshCheck() {
    if (!endpoint) return;
    if (document.hidden) { setTimeout(refreshCheck, 30000); return; }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(endpoint, { credentials: "same-origin", cache: "no-store", signal: controller.signal });
      if (response.ok) {
        const state = await response.json();
        if (String(state.revision) !== initialRevision) {
          const notice = document.getElementById("refresh-notice");
          if (notice) notice.hidden = false;
        }
      } else if (response.status === 403 || response.status === 404) {
        const notice = document.getElementById("refresh-notice");
        if (notice) { notice.hidden = false; notice.firstChild.textContent = "참여 권한이나 방 상태가 변경되었습니다. "; }
        return;
      }
    } catch (_error) {
      // Polling never interrupts input; the next scheduled check can retry.
    } finally { clearTimeout(timeout); }
    setTimeout(refreshCheck, 30000);
  }

  initialize();
  const joinForm = document.getElementById("join-token-form");
  if (joinForm && location.hash.length > 1) {
    const token = location.hash.slice(1);
    history.replaceState(null, "", location.pathname + location.search);
    if (/^[A-Za-z0-9_-]{32,120}$/.test(token)) {
      document.getElementById("join-token").value = token;
      joinForm.requestSubmit();
    }
  }
  if (endpoint) setTimeout(refreshCheck, 30000);
})();
