"""Capture the console's views as screenshots, for the submission zip.

**Output is gitignored.** `docs/screenshots/` is build output, not source. Committing it was
tried and reversed: images go stale the moment the UI changes, and a stale screenshot is
worse than none because it still looks like evidence. Regenerating takes about a minute, so
they are made when needed rather than carried in git.

**When they are needed: assembling the submission zip.** The demo surfaces are asleep
between sessions by design — the App is stopped, the agent endpoint is scaled to zero or
stopped, and the AI Search index is deleted to cap billing. A reviewer cannot click through
the product, and the capstone rubric lists "screenshots or demo transcripts" among the
things it otherwise records as *unverified*. Run this before zipping and the images travel
with the submission, matching the code being submitted.

Same argument as `export_evidence.py` for the backtest numbers: derived from the real thing
by a committed script, rather than hand-made once and quietly rotting.

    scripts/run_local_static_dev.sh 8811        # in another shell; live Lakebase
    .venv/bin/python scripts/capture_screenshots.py

**The Assistant pass is the one that calls a billed endpoint** — twice, once per theme. Everything
else reads Lakebase and the local process only. `--no-assistant` skips it, for a UI-only re-shoot
while the agent and the AI Search index are torn down.

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

#: The Assistant shot, captured for the first time in Run 2 (2026-09-30). It was `DEFERRED`
#: from the day this script was written, because the agent endpoint and the AI Search index are
#: both torn down between the two online windows and a panel photographed without them shows an
#: error, not a product. Run 2 is the only window in which both are live at once — and since the
#: index is deleted again immediately afterwards, this image is the *only* evidence a reviewer
#: gets that the retrieval path works. It is therefore load-bearing, not decoration.
#:
#: **The default question exercises `search_complaints` on purpose.** The deterministic
#: exposure answer (`Which fleet vehicles does recall 17V629000 affect?`) is already evidenced
#: by RUNBOOK 1.4's raw REST call and by `gold_fleet_exposure` itself. What nothing else
#: evidences is retrieval over the 2.24M-narrative corpus with complaint ids cited back — which
#: is also the path `_neutralise` defends (I-118) and the one `cites_complaint_ids` scores.
ASSISTANT_QUESTION = "Search complaints about brake failures"
ASSISTANT_CAPTION = (
    "Assistant answering over the complaint corpus, citing complaint ids "
    "(agent endpoint + AI Search index both live)"
)


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


def capture_assistant(base_url: str, theme: str, browser, question: str) -> dict | None:
    """Ask the live agent one question and photograph the answer.

    **Runs after every other view, and never aborts the run.** A cold serving endpoint, a
    deleted index or a model that simply declines must not cost the twenty images that do not
    depend on the agent — which is why this is a separate pass with its own context rather than
    an eleventh entry in `VIEWS`.

    **No image is written unless an answer actually rendered.** `wait_for_content` above tolerates
    a stuck skeleton because a half-rendered table is still informative; here the failure mode is
    the one its docstring calls worse than nothing — a screenshot of the `running tools` spinner
    would be evidence that the agent does *not* work, filed as evidence that it does. So the two
    conditions are checked separately and both must hold: the `.thinking` indicator gone **and** a
    `.turn.agent` present.

    Budget: the endpoint is scale-to-zero, so the first call pays a cold start (~47 s measured),
    the agent budgets 90 s for its own tool calls against the client's 120 s, and `/api/chat`
    fails at 120 s. 150 s covers the whole chain with room, and is not a number to trim — an
    early timeout here reads exactly like a broken agent.
    """
    ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
    ctx.add_init_script(f"localStorage.setItem('fleetguard.theme', '{theme}');")
    page = ctx.new_page()
    try:
        page.goto(f"{base_url}/#/queue", wait_until="networkidle")
        wait_for_content(page, "assistant")
        # Stable selectors, all of them already in the shipped UI — App.tsx's FAB aria-label,
        # Assistant.tsx's input placeholder, and the `.turn.agent` / `.thinking` classes it
        # renders. Nothing here required a frontend change to accommodate a screenshot.
        page.get_by_role("button", name="Open assistant").click()
        page.wait_for_selector('[role="dialog"][aria-label="Assistant"]', timeout=5_000)
        box = page.get_by_placeholder("Ask about a campaign or a symptom…")
        box.fill(question)
        box.press("Enter")

        page.wait_for_selector(".thinking", timeout=15_000)  # it started
        page.wait_for_selector(".thinking", state="detached", timeout=150_000)  # it finished
        page.wait_for_selector(".turn.agent", timeout=5_000)  # and it answered
        page.wait_for_timeout(600)  # let the markdown table settle

        name = f"assistant-{theme}.png"
        page.screenshot(path=str(OUT_DIR / name))
        print(f"  {name}")
        return {
            "file": name,
            "route": "#/queue",
            "theme": theme,
            "caption": ASSISTANT_CAPTION,
            "question": question,
        }
    except Exception as exc:  # noqa: BLE001 - never cost the other twenty images
        print(
            f"  ! assistant-{theme}.png NOT captured: {type(exc).__name__}: "
            f"{' '.join(str(exc).split())[:160]}\n"
            "    The agent endpoint and the AI Search index must BOTH be live. Check\n"
            "    /api/readyz before assuming this is a script problem.",
            file=sys.stderr,
        )
        return None
    finally:
        ctx.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://127.0.0.1:8811")
    ap.add_argument("--themes", default="dark,light")
    ap.add_argument("--assistant-question", default=ASSISTANT_QUESTION)
    ap.add_argument(
        "--no-assistant",
        action="store_true",
        help="skip the Assistant capture (the only pass that calls the billed agent endpoint)",
    )
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
    missing_assistant: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for raw in args.themes.split(","):
            theme = raw.strip()
            print(f"{theme} theme:")
            shots += capture(args.base_url, theme, browser)
            if args.no_assistant:
                continue
            shot = capture_assistant(args.base_url, theme, browser, args.assistant_question)
            if shot:
                shots.append(shot)
            else:
                missing_assistant.append(theme)
        browser.close()

    MANIFEST.write_text(
        json.dumps(
            {
                "captured_at": datetime.now(UTC).isoformat(),
                "base_url": args.base_url,
                "data_mode": health.get("data_mode"),
                "viewport": VIEWPORT,
                "shots": shots,
                # Recorded even when empty. An absent Assistant image is the one gap a reader
                # cannot infer from the file listing — every other view is unconditional — so
                # the manifest says so explicitly rather than leaving it to be noticed.
                "assistant_question": args.assistant_question,
                "assistant_not_captured": [
                    {"theme": t, "blocked_on": "agent endpoint + AI Search index"}
                    for t in missing_assistant
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\n{len(shots)} screenshots -> {OUT_DIR.relative_to(ROOT)}")
    if missing_assistant:
        print(
            f"!! the Assistant shot is MISSING for: {', '.join(missing_assistant)}.\n"
            "   It is the only evidence the retrieval path works once the index is torn down —\n"
            "   do not assemble the submission zip without it.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
