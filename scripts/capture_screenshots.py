"""Capture the console's views as committed screenshots.

**Why these are committed rather than taken on demand.** The demo surfaces are asleep
between sessions by design — the Databricks App is stopped, the agent endpoint is scaled to
zero or stopped, and the AI Search index is deleted to cap billing. A reviewer opening the
repo cold cannot see the product, and the capstone rubric lists "screenshots or demo
transcripts" among the things it will otherwise record as *unverified*. Committed images are
the only evidence that survives the resources going back to sleep.

They are generated, not hand-taken, so they can be regenerated after a UI change instead of
silently going stale — the same argument as `export_evidence.py` for the backtest numbers.

    scripts/run_local_static_dev.sh 8811        # in another shell; live Lakebase
    .venv/bin/python scripts/capture_screenshots.py

**Both themes, and the light one needs a script.** `lib/theme.ts` is dark-by-default and
deliberately ignores `prefers-color-scheme`, so Playwright's `--color-scheme light` does
nothing at all here. The theme is read from `localStorage['fleetguard.theme']` by an inline
pre-paint script in `index.html`, so light shots require `add_init_script` before first
paint — which is why this is a Python file and not a shell one-liner.

**Fixed viewport, not `--full-page`.** Comparable framing between runs, and a few hundred KB
per image rather than a few MB.

Playwright is a local review tool: it is installed in `.venv` with browsers cached, but it is
deliberately **not** in `requirements-dev.txt` or CI — CI holds no credentials and this needs
a live Lakebase session.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs/screenshots"
MANIFEST = OUT_DIR / "manifest.json"

VIEWPORT = {"width": 1440, "height": 1000}

#: (hash route, slug, what a reviewer should take from it). Order matches the sidebar.
VIEWS: list[tuple[str, str, str]] = [
    ("#/home", "home", "Orientation: what the product is and who it is for"),
    ("#/queue", "queue", "Recall queue, ranked consequence-before-volume"),
    ("#/signals", "signals", "Emerging signals — the proactive half"),
    ("#/evidence", "evidence", "The measured backtest, including the negative result"),
    ("#/launched", "launched", "Launched service campaigns"),
    ("#/work-orders", "work-orders", "Work orders, assignment and actual cost"),
    ("#/audit-log", "audit-log", "Append-only audit trail"),
    ("#/depot-risk", "depot-risk", "Depot risk, real component numbers not a blended score"),
    ("#/trends", "trends", "Recall trend over time"),
    ("#/recall-api", "recall-api", "Live NHTSA feed: coverage, health, novel campaigns"),
]

#: Captured only once the agent endpoint and AI Search index are restored. Listed here so
#: the gap is visible and bounded rather than forgotten — see docs/EVIDENCE.md.
DEFERRED = [
    ("#/queue", "assistant", "Assistant panel answering a question (needs the agent endpoint)"),
]


def wait_for_content(page, slug: str) -> None:
    """Block until the view has actually rendered its data, not its loading skeleton.

    **`networkidle` is not enough and the first run proved it.** Every view fetches on mount
    from a `useEffect`, so the document and its assets are idle well before the API call is
    issued — the first capture produced twenty pixel-perfect screenshots of the skeleton
    placeholders, and the script reported success. A screenshot of a spinner is worse than
    no screenshot: it is evidence that the page does not work.

    The reliable signal is the skeleton itself disappearing, since every view in this console
    renders `.skeleton` while `data` is null (`lib/useFetch.ts` + each view's third early
    return). Falling back to a plain delay rather than raising keeps a slow view from
    aborting the whole run — but it prints, so a silent skeleton cannot recur unnoticed.
    """
    try:
        page.wait_for_function(
            "document.querySelectorAll('.skeleton').length === 0", timeout=30_000
        )
    except Exception:  # noqa: BLE001 - a slow view must not abort the other nineteen
        print(f"    ! {slug}: skeleton still present after 30s — check this image")
    # Let charts finish their transition and any late layout settle.
    page.wait_for_timeout(900)


def capture(base_url: str, theme: str, browser) -> list[dict]:
    ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
    # Must run before first paint: index.html reads this synchronously to avoid a flash.
    ctx.add_init_script(f"localStorage.setItem('fleetguard.theme', '{theme}');")
    page = ctx.new_page()
    captured = []
    for route, slug, caption in VIEWS:
        page.goto(f"{base_url}/{route}", wait_until="networkidle")
        wait_for_content(page, slug)
        name = f"{slug}-{theme}.png"
        page.screenshot(path=str(OUT_DIR / name))
        captured.append({"file": name, "route": route, "theme": theme, "caption": caption})
        print(f"  {name}")
    ctx.close()
    return captured


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8811")
    ap.add_argument("--themes", default="dark,light")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright is not installed in this environment (.venv/bin/playwright)",
            file=sys.stderr,
        )
        return 2

    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{args.base_url}/healthz", timeout=5) as r:
            health = json.load(r)
    except (urllib.error.URLError, TimeoutError) as e:
        print(
            f"no console at {args.base_url} ({e}).\n"
            "Start one first:  scripts/run_local_static_dev.sh 8811",
            file=sys.stderr,
        )
        return 2

    # Capturing the snapshot surface and calling it the product would be a lie of exactly
    # the kind the Evidence page exists to avoid.
    if health.get("data_mode") != "lakebase":
        print(
            f"refusing: data_mode is {health.get('data_mode')!r}, not 'lakebase'. These "
            "screenshots are evidence and must come from live data.",
            file=sys.stderr,
        )
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shots: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for theme in args.themes.split(","):
            print(f"{theme} theme:")
            shots += capture(args.base_url, theme.strip(), browser)
        browser.close()

    MANIFEST.write_text(
        json.dumps(
            {
                "captured_at": datetime.now(UTC).isoformat(),
                "base_url": args.base_url,
                "data_mode": health.get("data_mode"),
                "viewport": VIEWPORT,
                "shots": shots,
                "deferred": [
                    {
                        "route": r,
                        "slug": s,
                        "caption": c,
                        "blocked_on": "agent endpoint + AI Search",
                    }
                    for r, s, c in DEFERRED
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\n{len(shots)} screenshots -> {OUT_DIR.relative_to(ROOT)}")
    print(f"{len(DEFERRED)} deferred until the agent endpoint is restored (see manifest)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
