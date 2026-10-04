"""Burn the walkthrough video's per-beat captions into the frames, as a post-process step.

**Why a separate script, not a change to `capture_screenshots.py`.** That script's recorder
deliberately injects nothing into the live page while filming — "no caption overlays, no
highlight boxes, no synthetic cursor... those would make the video show something that is not
the product." This script does not touch the recording. It takes the finished `.webm` and the
captions `record_walkthrough` already wrote into `manifest.json`, and overlays them afterward,
so what Playwright captured stays an unmodified recording of the real console and the only new
thing is text drawn over frames that already exist.

**Why Pillow + an image overlay, not ffmpeg's own `drawtext`/`subtitles` filters.** Neither is
available in this machine's Homebrew `ffmpeg` build (`ffmpeg -version` shows no
`--enable-libfreetype` / `--enable-libass`), and rebuilding ffmpeg from source for one script is
not worth it. A per-beat caption bar is rendered once as a PNG via Pillow (already a local-only
dependency, installed the same way Playwright is — not in `requirements-dev.txt`, not needed in
CI), then composited onto the video with ffmpeg's `overlay` filter, gated to each beat's time
window via `enable='between(t,start,end)'`. `libx264` is confirmed present in this build.

Usage:
    .venv/bin/python scripts/burn_captions.py [--theme dark] [--video docs/screenshots/walkthrough-dark.webm]

Reads `docs/screenshots/manifest.json` for the beat timings and captions of the matching video
entry, and writes `docs/screenshots/walkthrough-<theme>-captioned.mp4` alongside the original
(which is left untouched).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs/screenshots"
MANIFEST = OUT_DIR / "manifest.json"

BAR_HEIGHT = 72
FONT_SIZE = 26
PADDING_X = 24


def _font() -> ImageFont.FreeTypeFont:
    # macOS ships Helvetica Neue at this path; fall back to Pillow's bitmap default rather than
    # failing the whole run over a font that is cosmetic, not evidentiary.
    for candidate in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, FONT_SIZE)
    print("  ! no system font found, falling back to Pillow's bitmap default", file=sys.stderr)
    return ImageFont.load_default()


def render_caption_png(text: str, width: int, path: Path) -> None:
    """A translucent bar across the bottom of the frame, white text, left-aligned with padding.

    Translucent rather than opaque so the console behind it stays legible — this is a caption,
    not a title card, and the whole point of the video is to show the product underneath.
    """
    img = Image.new("RGBA", (width, BAR_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (width, BAR_HEIGHT)], fill=(10, 10, 14, 215))
    font = _font()
    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]
    y = (BAR_HEIGHT - text_h) // 2 - bbox[1]
    draw.text((PADDING_X, y), text, font=font, fill=(255, 255, 255, 255))
    img.save(path)


def video_duration_seconds(video: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--theme", default="dark")
    ap.add_argument(
        "--video", default=None, help="defaults to docs/screenshots/walkthrough-<theme>.webm"
    )
    args = ap.parse_args()

    video = Path(args.video) if args.video else OUT_DIR / f"walkthrough-{args.theme}.webm"
    if not video.exists():
        print(f"no video at {video}", file=sys.stderr)
        return 2
    if not MANIFEST.exists():
        print(f"no manifest at {MANIFEST} — run capture_screenshots.py first", file=sys.stderr)
        return 2

    manifest = json.loads(MANIFEST.read_text())
    entry = next((v for v in manifest.get("videos", []) if v.get("theme") == args.theme), None)
    if entry is None or not entry.get("beats"):
        print(
            f"manifest has no beats for theme={args.theme!r} — re-run capture_screenshots.py "
            "(it writes `videos[].beats` fresh on every run)",
            file=sys.stderr,
        )
        return 2
    beats: list[dict] = entry["beats"]

    duration = video_duration_seconds(video)
    width = manifest.get("viewport", {}).get("width", 1440)

    caption_dir = OUT_DIR / f"_captions_{args.theme}"
    caption_dir.mkdir(exist_ok=True)
    pngs: list[tuple[Path, float, float]] = []
    for i, beat in enumerate(beats):
        start = float(beat["at_seconds"])
        end = float(beats[i + 1]["at_seconds"]) if i + 1 < len(beats) else duration
        png_path = caption_dir / f"beat_{i:02d}.png"
        render_caption_png(beat["caption"], width, png_path)
        pngs.append((png_path, start, end))
        print(f"  beat {i}: {start:.1f}s -> {end:.1f}s  {beat['caption']!r}")

    # Chain one `overlay` per beat, each gated to its own time window and feeding the next.
    # `format=rgba` on the overlay inputs keeps the translucent bar's alpha channel intact —
    # without it ffmpeg silently drops alpha and the bar renders opaque black.
    filter_parts = []
    last_label = "[0:v]"
    for i, (_png_path, start, end) in enumerate(pngs):
        in_label = f"[{i + 1}:v]"
        out_label = f"[v{i}]"
        y_expr = "H-h"  # bottom-anchored, same position every beat
        filter_parts.append(
            f"{last_label}{in_label}overlay=x=0:y={y_expr}:"
            f"enable='between(t,{start},{end})'{out_label}"
        )
        last_label = out_label

    output = OUT_DIR / f"walkthrough-{args.theme}-captioned.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        *[arg for png_path, *_ in pngs for arg in ("-i", str(png_path))],
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        last_label,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output),
    ]
    print("\n==> " + " ".join(cmd[:4]) + " ... (filter_complex omitted)")
    subprocess.run(cmd, check=True)

    for png_path, *_ in pngs:
        png_path.unlink()
    caption_dir.rmdir()

    size_mb = output.stat().st_size / 1_048_576
    print(f"\n{output.relative_to(ROOT)}  ({size_mb:.1f} MB, {len(pngs)} captions burned in)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
