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
import time
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
    # `#/emerging`, NOT `#/signals`. The hash router has no `signals` case and falls through to
    # Home, so this line shipped a screenshot of Home captioned "the proactive half" in every
    # run to date — and reported success, because Home renders cleanly (I-128). The internal
    # View name is `signals`; only the URL is `emerging`. The slug stays `signals` so the file
    # name and caption still match the view a reader is looking for.
    ("#/emerging", "signals", "Emerging signals — the proactive half"),
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
#: by docs/RUNBOOK.md's raw REST call (agent restore/verification steps) and by
#: `gold_fleet_exposure` itself. What nothing else
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


def _fingerprint(page) -> str:
    """Collapsed text of `<main>` — enough to tell two views apart, stable across runs."""
    try:
        return " ".join(page.locator("main").inner_text().split())[:400]
    except Exception:  # noqa: BLE001 - a fingerprint is a check, never the reason a run fails
        return ""


def capture(base_url: str, theme: str, browser) -> list[dict]:
    ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
    # Must run before first paint: index.html reads this synchronously to avoid a flash.
    ctx.add_init_script(f"localStorage.setItem('fleetguard.theme', '{theme}');")
    page = ctx.new_page()
    captured = []
    seen: dict[str, str] = {}  # fingerprint -> slug that produced it first
    for route, slug, caption in VIEWS:
        page.goto(f"{base_url}/{route}", wait_until="networkidle")
        wait_for_content(page, slug)
        name = f"{slug}-{theme}.png"
        page.screenshot(path=str(OUT_DIR / name))
        captured.append({"file": name, "route": route, "theme": theme, "caption": caption})

        # THE GUARD THIS SCRIPT DID NOT HAVE (I-128). `#/signals` is not a route — the hash
        # router falls through to Home — so `signals-*.png` was a screenshot of Home captioned
        # "Emerging signals, the proactive half", in every run, in the submission zip. Nothing
        # failed: Home renders cleanly, no skeleton, so every existing check passed and the
        # manifest listed the view as captured. A mislabelled screenshot is worse than a missing
        # one for the same reason a spinner is (see `wait_for_content`) — it still looks like
        # evidence.
        #
        # Any route that silently falls back produces a page some other slug already produced,
        # so comparing rendered text across views catches the whole class rather than this one
        # instance. Loud, and non-fatal: the images are still worth having while someone reads
        # this line.
        fp = _fingerprint(page)
        if fp and fp in seen:
            print(
                f"    ! {slug} ({route}) rendered the SAME PAGE as {seen[fp]} — {name} is "
                f"mislabelled. Check the route against App.tsx's viewFromHash(); an unknown "
                f"hash falls through to Home without erroring (I-128).",
                file=sys.stderr,
            )
        elif fp:
            seen[fp] = slug
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


#: The walkthrough video's beats, in `docs/DEMO.md`'s order and using its framing. Taken from
#: that file rather than invented here, so the video and the spoken demo cannot tell different
#: stories — `detect -> scope -> decide -> dispatch -> prove`.
#:
#: **Beat 5 (the human approval gate) is deliberately absent, and it is DEMO.md's strongest
#: moment.** Recording it means actually approving a campaign, which writes a service campaign
#: and a fan-out of work orders into the submission database. A video is not worth mutating the
#: dataset a reviewer will read, so the walkthrough is **read-only navigation plus one agent
#: question** — itself a read. `manifest.json` says so explicitly rather than letting the
#: omission read as coverage.
#:
#: **Beat 9 is absent too**, for a duller reason: it is a tour of the Databricks workspace, not
#: of this console, so there is nothing here to film.
WALKTHROUGH: list[tuple[str, float, str]] = [
    ("#/evidence", 5.0, "Beat 1 — Evidence first, including the negative result"),
    ("#/emerging", 5.0, "Beat 2 — Emerging signals, the proactive half"),
    ("#/queue", 5.0, "Beat 3 — Recall queue, consequence before volume"),
    ("#/work-orders", 4.0, "Beat 6 — Work orders, closing the loop"),
    # Beat 7's cost figures live on the launched-campaigns view (ServiceCampaigns.tsx calls
    # `costBreakdown()`); there is no `#/cost` route. Checked against the router rather than
    # assumed — the same mistake one line up cost this script two mislabelled screenshots.
    ("#/launched", 4.0, "Beat 7 — Launched campaigns and cost, not one blended $/vehicle"),
    ("#/audit-log", 4.0, "Beat 10 — The append-only audit trail"),
]


def record_walkthrough(base_url: str, theme: str, browser, question: str) -> dict | None:
    """Record a silent walkthrough of the console as `.webm`, via Playwright's own recorder.

    **Why video at all.** The index is torn down immediately after Run 2 to stop billing, so a
    reviewer opening the App gets a working console with a dead retrieval path — `/api/readyz`
    503s and the Assistant cannot answer. Stills prove each view rendered; only a recording shows
    the *flow* the product is actually about, which is the thing a torn-down demo loses.

    **Playwright's recorder, not a desktop capture.** Same dependency already in `.venv`, runs
    headless, needs no screen-recording permission, and re-runs identically — the same argument
    that made the stills a committed script instead of someone's saved PNGs. A `screencapture`
    or `ffmpeg` recording would film whatever else was on the desktop and could not be
    reproduced.

    **Nothing is injected into the page.** No caption overlays, no highlight boxes, no synthetic
    cursor. Those would make the video show something that is not the product, in a file whose
    whole purpose is to be evidence of the product. The cost is that clicks are invisible and the
    page appears to change on its own — so the beats and their timings go in `manifest.json`
    instead, and `docs/DEMO.md` remains the narration.

    One context, one video: Playwright writes the file when the context closes, and a per-beat
    context would produce six clips of a product that is meant to be one flow.
    """
    ctx = browser.new_context(
        viewport=VIEWPORT,
        device_scale_factor=1,
        record_video_dir=str(OUT_DIR),
        record_video_size=VIEWPORT,
    )
    ctx.add_init_script(f"localStorage.setItem('fleetguard.theme', '{theme}');")
    page = ctx.new_page()
    beats: list[dict] = []
    started = time.monotonic()
    try:
        for route, dwell, caption in WALKTHROUGH:
            at = round(time.monotonic() - started, 1)
            page.goto(f"{base_url}/{route}", wait_until="networkidle")
            wait_for_content(page, f"walkthrough{route}")
            page.wait_for_timeout(int(dwell * 1000))
            beats.append({"at_seconds": at, "route": route, "caption": caption})

        # Beat 8 last, because it is the slowest and the only one that can fail: if the agent
        # is cold or the index is gone, the six beats above are already recorded.
        at = round(time.monotonic() - started, 1)
        page.get_by_role("button", name="Open assistant").click()
        page.wait_for_selector('[role="dialog"][aria-label="Assistant"]', timeout=5_000)
        box = page.get_by_placeholder("Ask about a campaign or a symptom…")
        box.fill(question)
        box.press("Enter")
        page.wait_for_selector(".thinking", timeout=15_000)
        page.wait_for_selector(".thinking", state="detached", timeout=150_000)
        page.wait_for_selector(".turn.agent", timeout=5_000)
        page.wait_for_timeout(6_000)  # long enough to read the answer back
        beats.append({"at_seconds": at, "route": "#/queue", "caption": f"Beat 8 — {question}"})
    except Exception as exc:  # noqa: BLE001 - keep whatever was recorded before the failure
        print(
            f"  ! walkthrough cut short: {type(exc).__name__}: {' '.join(str(exc).split())[:140]}",
            file=sys.stderr,
        )

    video = page.video
    ctx.close()  # the file is only written on close
    if video is None:
        print("  ! no video was produced", file=sys.stderr)
        return None
    name = f"walkthrough-{theme}.webm"
    try:
        video.save_as(str(OUT_DIR / name))
        video.delete()  # drop playwright's random-named original
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not save the video: {exc}", file=sys.stderr)
        return None
    size_mb = (OUT_DIR / name).stat().st_size / 1_048_576
    print(f"  {name}  ({size_mb:.1f} MB, {len(beats)} beats)")
    return {"file": name, "theme": theme, "beats": beats, "size_mb": round(size_mb, 1)}


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
    ap.add_argument(
        "--no-video",
        action="store_true",
        help="skip the walkthrough recording (also calls the agent, once per theme)",
    )
    ap.add_argument(
        "--video-themes",
        default="dark",
        help="themes to record a walkthrough for (default: dark only — a second theme doubles "
        "the runtime and the agent calls for a film of the same flow)",
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
    videos: list[dict] = []
    missing_assistant: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for raw in args.themes.split(","):
            theme = raw.strip()
            if not theme:
                continue  # `--themes ""` must mean none, not a theme whose name is empty
            print(f"{theme} theme:")
            shots += capture(args.base_url, theme, browser)
            if args.no_assistant:
                continue
            shot = capture_assistant(args.base_url, theme, browser, args.assistant_question)
            if shot:
                shots.append(shot)
            else:
                missing_assistant.append(theme)

        if not args.no_video:
            for raw in args.video_themes.split(","):
                theme = raw.strip()
                if not theme:
                    continue
                print(f"{theme} walkthrough:")
                clip = record_walkthrough(args.base_url, theme, browser, args.assistant_question)
                if clip:
                    videos.append(clip)
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
                # What the walkthrough does NOT do, said here rather than inferred from its
                # absence: no approval is performed (DEMO.md beat 5), because recording it means
                # writing a service campaign and its work orders into the submission database.
                "videos": videos,
                "video_scope": (
                    "read-only navigation plus one agent question; no approval is performed "
                    "(DEMO.md beat 5) and nothing is injected into the page"
                ),
                "assistant_not_captured": [
                    {"theme": t, "blocked_on": "agent endpoint + AI Search index"}
                    for t in missing_assistant
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\n{len(shots)} screenshots, {len(videos)} video(s) -> {OUT_DIR.relative_to(ROOT)}")
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
