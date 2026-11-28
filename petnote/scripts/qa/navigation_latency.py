"""Measure navigation on an isolated loopback demo, never on a live account."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "manage.py").is_file())
KEY = "petnote-navigation-measurement"
STEPS = ("modules/", "routines/", "supplies/", "", "records/", "overview/", "")
INSTRUMENT = r"""
(() => {
  const key = "petnote-navigation-measurement";
  const documentId = crypto.randomUUID();
  const now = () => performance.timeOrigin + performance.now();
  let arm = null, previousMain = null, mainScheduled = false, pendingScheduled = false;
  const read = () => { try { return JSON.parse(sessionStorage.getItem(key)); } catch (_) { return null; } };
  const save = value => sessionStorage.setItem(key, JSON.stringify(value));
  const frames = () => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  window.petnoteBenchmark = {
    arm(href) {
      arm = href; previousMain = document.getElementById("main");
      mainScheduled = false; pendingScheduled = false;
      sessionStorage.removeItem(key);
    }
  };
  function check() {
    const record = read();
    if (!record || record.stable_ms !== null) return;
    const current = document.getElementById("main");
    if (!pendingScheduled && document.body?.hasAttribute("data-navigation-pending")) {
      pendingScheduled = true;
      frames().then(() => {
        const latest = read();
        if (!latest || latest.started !== record.started) return;
        if (document.body?.hasAttribute("data-navigation-pending")) {
          latest.pending_paint_ms = now() - latest.started; save(latest);
        }
      });
    }
    const changed = record.source_document !== documentId || (previousMain && current !== previousMain);
    if (!mainScheduled && current && changed &&
        location.origin + location.pathname + location.search === record.destination) {
      mainScheduled = true;
      frames().then(async () => {
        const latest = read();
        if (!latest || latest.started !== record.started) return;
        latest.main_paint_ms = now() - latest.started;
        latest.soft_navigation = latest.source_document === documentId;
        save(latest);
        const effects = document.getAnimations().filter(animation => {
          const timing = animation.effect?.getComputedTiming();
          return timing && Number.isFinite(timing.endTime) && timing.endTime <= 1200;
        });
        await Promise.allSettled(effects.map(animation => animation.finished));
        await frames();
        const final = read();
        if (!final || final.started !== record.started) return;
        final.stable_ms = now() - final.started;
        save(final);
      });
    }
  }
  document.addEventListener("click", event => {
    const anchor = event.target.closest("a[href]");
    if (!arm || !anchor || anchor.href !== arm) return;
    const destination = new URL(anchor.href);
    save({started: now(), destination: destination.origin + destination.pathname + destination.search,
          source_document: documentId, pending_paint_ms: null, main_paint_ms: null,
          stable_ms: null, soft_navigation: null});
    check();
  }, true);
  new MutationObserver(check).observe(document, {childList: true, subtree: true, attributes: true,
      attributeFilter: ["data-navigation-pending", "aria-busy", "class"]});
  document.addEventListener("DOMContentLoaded", check);
  document.addEventListener("petnote:navigated", check);
})();
"""


def loopback_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not parsed.port or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Use an explicit HTTP loopback origin and port for an isolated demo.")
    return value.rstrip("/")


def login_demo(page, origin):
    page.goto(origin + "/", wait_until="networkidle")
    form = page.locator('form').filter(has=page.locator('button[name="identity"][value="admin"]'))
    if form.count() != 1 or not page.locator(".demo-banner").count():
        raise RuntimeError("Navigation measurements require the isolated local demo login.")
    form.locator('button[name="identity"][value="admin"]').click()
    page.wait_for_load_state("networkidle")
    cards = page.locator("a.room-card")
    if cards.count() != 2:
        raise RuntimeError("Expected the two synthetic demo rooms.")
    return cards.first.get_attribute("href")


def summarize(rows):
    output = {}
    for name in ("pending_paint_ms", "main_paint_ms", "stable_ms", "response_wait_ms", "requests"):
        values = [row[name] for row in rows if row.get(name) is not None]
        if values:
            ordered = sorted(values)
            output[name] = {"median": round(statistics.median(values), 2),
                            "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 2),
                            "min": round(min(values), 2), "max": round(max(values), 2)}
    output["samples"] = len(rows)
    output["soft_navigations"] = sum(row["soft_navigation"] for row in rows)
    return output


def scenario(browser, origin, layout, latency, repeats):
    context = browser.new_context(
        locale="ko-KR", timezone_id="Asia/Seoul",
        viewport={"width": 390 if layout == "mobile" else 1440, "height": 900},
    )
    context.add_init_script(INSTRUMENT)
    page = context.new_page()
    page.set_default_timeout(15000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    root = login_demo(page, origin)
    page.goto(origin + root + "?layout=" + layout, wait_until="networkidle")
    # Warm common styles and fonts before comparing repeated tab interactions.
    page.evaluate("() => document.fonts.ready")
    cdp = context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", {
        "offline": False, "latency": latency,
        "downloadThroughput": 250000 if latency else -1,
        "uploadThroughput": 125000 if latency else -1,
        "connectionType": "cellular3g" if latency else "ethernet",
    })
    rows = []
    for cycle in range(repeats):
        for suffix in STEPS:
            destination = origin + root + suffix
            nav = ".mobile-dock" if layout == "mobile" else "#sidebar"
            link = page.locator(nav + ' a[href="' + root + suffix + '"]').first
            link.wait_for(state="visible")
            requests = []
            def capture(request):
                if request.resource_type in ("document", "fetch", "xhr") and urlsplit(request.url).path == root + suffix:
                    requests.append(request)
            page.on("request", capture)
            page.evaluate("(href) => window.petnoteBenchmark.arm(href)", destination)
            link.click(no_wait_after=True)
            page.wait_for_function(
                "(key) => { const r=JSON.parse(sessionStorage.getItem(key)); return r && r.stable_ms!==null; }",
                arg=KEY, timeout=20000,
            )
            row = page.evaluate("(key) => JSON.parse(sessionStorage.getItem(key))", KEY)
            page.remove_listener("request", capture)
            waits = []
            for request in requests:
                timing = request.timing
                if timing["responseStart"] >= 0 and timing["requestStart"] >= 0:
                    waits.append(timing["responseStart"] - timing["requestStart"])
            row = {key: value for key, value in row.items() if key not in {"source_document", "started", "destination"}}
            row.update(cycle=cycle + 1, route=suffix or "today", requests=len(requests),
                       response_wait_ms=sum(waits) if waits else None)
            rows.append(row)
            print(json.dumps({"layout": layout, "latency_ms": latency, **row}, ensure_ascii=False), flush=True)
    context.close()
    return {"layout": layout, "latency_ms": latency, "rows": rows,
            "browser_errors": errors, "summary": summarize(rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--latencies", type=int, nargs="+", default=[0, 150])
    parser.add_argument("--layouts", choices=("mobile", "desktop"), nargs="+", default=["mobile", "desktop"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        origin = loopback_origin(args.origin)
    except ValueError as error:
        parser.error(str(error))
    if not 1 <= args.repeats <= 20 or any(value < 0 or value > 2000 for value in args.latencies):
        parser.error("Use 1-20 repeats and 0-2000ms latency.")
    output = {"label": args.label, "origin": origin, "browser": "chromium",
              "measurement": "Click event to double animation-frame paint and finite animation completion.",
              "network": "CDP latency plus 2Mbps download/1Mbps upload when latency is nonzero; warm common assets.",
              "scenarios": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for latency in args.latencies:
                for layout in args.layouts:
                    output["scenarios"].append(scenario(browser, origin, layout, latency, args.repeats))
                    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            browser.close()
    output["elapsed_seconds"] = round(time.monotonic() - started, 2)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summaries": [
        {"layout": row["layout"], "latency_ms": row["latency_ms"], **row["summary"]}
        for row in output["scenarios"]
    ]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
