"""Render the explainer film (how-it-works.html) to WebM, one file per language, with a poster frame each:

    python app/media/render.py                 # de, fr, it, en -> how-it-works-<lang>.webm and -<lang>.jpg
    python app/media/render.py --lang de       # one language
    python app/media/render.py --fps 24        # fewer frames (30 by default)

The film is a pure function of time (window.seek), so every frame is exact: a headless Chromium (Playwright) is asked for
frame after frame as a PNG and ffmpeg (the imageio-ffmpeg wheel's binary) encodes them as VP9. Needs
    pip install playwright imageio-ffmpeg && python -m playwright install chromium
About a minute per language; the film's fonts come from Google Fonts, so the machine needs the internet.
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "how-it-works.html"
LANGS = ("de", "fr", "it", "en")
POSTER_AT = 20.0                                           # seconds: the extraction, rooms filling in
WIDTH, HEIGHT = 1280, 720


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
    except ImportError:
        sys.exit("imageio-ffmpeg is not installed: pip install imageio-ffmpeg")
    return imageio_ffmpeg.get_ffmpeg_exe()


def render(lang, fps, crf):
    from playwright.sync_api import sync_playwright

    out = HERE / f"how-it-works-{lang}.webm"
    poster = HERE / f"how-it-works-{lang}.jpg"
    ffmpeg = ffmpeg_exe()
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "png", "-i", "-",
           "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(crf), "-pix_fmt", "yuv420p", "-row-mt", "1", "-deadline", "good",
           "-cpu-used", "2", "-g", str(fps * 2), "-an", str(out)]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=1).new_page()
        page.goto(SOURCE.as_uri() + f"?lang={lang}&nocontrols&paused")
        page.evaluate("document.fonts.ready.then(() => true)")
        total = page.evaluate("window.film.total")
        frames = round(total * fps)
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        poster_png = None
        for i in range(frames):
            t = i / fps
            page.evaluate(f"window.seek({t})")
            png = page.screenshot(type="png")
            enc.stdin.write(png)
            if poster_png is None and t >= POSTER_AT:
                poster_png = png
            if i % (fps * 5) == 0:
                print(f"{lang}: {t:5.1f} / {total:.0f} s", flush=True)
        enc.stdin.close()
        if enc.wait() != 0:
            sys.exit(f"ffmpeg failed for {lang}")
        browser.close()
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "image2pipe", "-c:v", "png", "-i", "-", "-frames:v", "1", "-q:v", "4", str(poster)],
                   input=poster_png, check=True)
    print(f"{lang}: {out.name} {out.stat().st_size / 1e6:.2f} MB, {poster.name} {poster.stat().st_size / 1e3:.0f} kB")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lang", default=",".join(LANGS), help="languages, comma-separated (default: all four)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--crf", type=int, default=32, help="VP9 quality, lower is better and larger (default 32)")
    a = ap.parse_args()
    for lang in a.lang.split(","):
        if lang not in LANGS:
            sys.exit(f"unknown language {lang!r}: {', '.join(LANGS)}")
        render(lang, a.fps, a.crf)


if __name__ == "__main__":
    main()
