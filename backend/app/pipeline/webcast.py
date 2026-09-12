"""Screen recording of a real web page, scrolled like someone reading it.

What this replaces: the old "scroll" background rendered the page's *text* into
a tall image and slid it past the camera. For an article that reads fine; for a
repository it put raw README markup on screen — `<p align="center">` and all —
which is not what the page looks like to anyone who opens it.

So the page is opened in a real browser at the frame's width and captured as it
renders: the formatted README, the badges, the code blocks with their
highlighting, the site's own typography. Then the camera moves down it, at
reading speed.

The capture is a full-page screenshot rather than a video recording, and that
is deliberate:

  - Playwright's recorder needs a second ffmpeg binary of its own and hands
    back a webm at the browser's variable frame rate, which then has to be
    conformed to the timeline's clock anyway.
  - A pan over a still is exact: the scroll lasts precisely as long as the
    narration it was made for, at the project's fps, every time.

Playwright drives the browser, preferring the Chrome already installed on the
machine over downloading its own — a 150 MB download is a bad surprise
mid-render.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..config import settings
from . import render


class RecorderUnavailable(RuntimeError):
    """No browser to record with. A normal state with a way out, not a crash —
    every caller has a background it can fall back to."""


# The page is rendered at a phone-ish CSS width so sites serve their mobile
# layout: one column, large type, no sidebar. A desktop render scaled into 9:16
# makes body text about four pixels tall.
MOBILE_CSS_WIDTH = 480
DESKTOP_CSS_WIDTH = 1440

# How long to wait for the page to settle before capturing. Long enough for
# fonts and images, short enough that a slow third-party tracker does not hold
# up a render.
LOAD_TIMEOUT_MS = 20000
SETTLE_MS = 1200

# Chromium refuses to compose a screenshot past roughly this height, and a page
# taller than this is not going to be read in a short anyway.
MAX_PAGE_PX = 16000

# How fast the camera may travel: one frame-height every this many seconds. A
# 400-page documentation site panned end to end in twenty seconds is a blur; at
# reading speed the short simply shows the first part of it, which is what
# someone scrolling for twenty seconds would see.
SECONDS_PER_SCREEN = 3.5

# Best-effort cookie banners. A full-screen consent wall over the page is the
# one thing that makes the whole capture worthless, and these selectors cover
# most of what a Portuguese or English site puts up.
CONSENT_SELECTORS = (
    "button:has-text('Aceitar todos')", "button:has-text('Aceitar')",
    "button:has-text('Accept all')", "button:has-text('Accept')",
    "button:has-text('I agree')", "[aria-label='Accept all']",
)


def available() -> tuple[bool, str]:
    """Whether a page can be filmed right now, and what is missing if not."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return False, ("Playwright is not installed — `pip install playwright` "
                       "in the backend environment.")
    if _browser_channel() is None and not _bundled_browser_installed():
        return False, ("No browser for Playwright — install Google Chrome, or "
                       "run `playwright install chromium`.")
    return True, ""


def _browser_channel() -> str | None:
    """A browser already on the machine, preferred over Playwright's own."""
    candidates = {
        "chrome": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   shutil.which("google-chrome"), shutil.which("google-chrome-stable")],
        "msedge": ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                   shutil.which("microsoft-edge")],
    }
    for channel, paths in candidates.items():
        if any(path and Path(path).exists() for path in paths):
            return channel
    return None


def _bundled_browser_installed() -> bool:
    for root in (Path.home() / "Library/Caches/ms-playwright",
                 Path.home() / ".cache/ms-playwright"):
        if root.exists() and any(root.glob("chromium-*")):
            return True
    return False


def capture_page(url: str, out_png: Path, fmt=None, desktop: bool = False,
                 log=lambda m, *_: None) -> Path:
    """The whole page as one tall image, at the frame's width.

    Scrolled to the bottom first and back to the top: that is what triggers the
    lazy-loaded images and the "appears on scroll" animations, so they are in
    the picture instead of being empty boxes.
    """
    ok, reason = available()
    if not ok:
        raise RecorderUnavailable(reason)
    from playwright.sync_api import Error as PlaywrightError, sync_playwright

    from . import formats

    frame = fmt or formats.VERTICAL
    css_width = DESKTOP_CSS_WIDTH if desktop else MOBILE_CSS_WIDTH
    css_height = max(int(round(css_width * frame.height / frame.width)), 320)
    # Capture above the frame's resolution so the downscale to 1080 lands on
    # sharp text, but no further: the screenshot is one image of the WHOLE
    # page, and a tall page at 4x is hundreds of megabytes of bitmap.
    scale = min(max(frame.width / css_width, 1.0), 2.0)

    channel = _browser_channel()
    log(f"site: opening {url} at {css_width}px wide"
        f"{f' in {channel}' if channel else ''}")

    with sync_playwright() as pw:
        launch: dict = {"args": ["--hide-scrollbars", "--mute-audio"]}
        if channel:
            launch["channel"] = channel
        try:
            browser = pw.chromium.launch(**launch)
        except PlaywrightError as exc:
            raise RecorderUnavailable(
                f"The browser did not start ({str(exc)[:200]}). Install Google "
                f"Chrome or run `playwright install chromium`.") from exc
        try:
            context = browser.new_context(
                viewport={"width": css_width, "height": css_height},
                device_scale_factor=scale,
                is_mobile=not desktop,
                user_agent=None if desktop else (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    "Mobile/15E148 Safari/604.1"),
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded",
                          timeout=LOAD_TIMEOUT_MS)
            except PlaywrightError as exc:
                raise RuntimeError(f"Could not open {url}: {str(exc)[:200]}") from exc
            _dismiss_consent(page, log)
            page.wait_for_timeout(SETTLE_MS)
            _wake_lazy_content(page)
            out_png.parent.mkdir(parents=True, exist_ok=True)
            try:
                page.screenshot(path=str(out_png), full_page=True,
                                animations="disabled")
            except PlaywrightError:
                # A page taller than Chromium will compose: take the viewport,
                # which is still the page, just less of it.
                log("site: the page is too tall to capture whole; taking the "
                    "top of it", "warn")
                page.screenshot(path=str(out_png), animations="disabled")
            context.close()
        finally:
            browser.close()
    return out_png


def record_scroll(url: str, seconds: float, out: Path, fmt=None,
                  desktop: bool = False, log=lambda m, *_: None) -> Path:
    """`url`, filmed scrolling, as an MP4 of exactly `seconds`."""
    from . import formats

    frame = fmt or formats.VERTICAL
    shot = out.parent / f"{out.stem}_page.png"
    capture_page(url, shot, fmt=frame, desktop=desktop, log=log)
    return pan(shot, seconds, out, fmt=frame, log=log)


def pan(image: Path, seconds: float, out: Path, fmt=None,
        log=lambda m, *_: None) -> Path:
    """Move the camera down a tall image over `seconds`.

    Eased at both ends, the way a hand scrolls: a constant crawl from the first
    frame is the tell of a generated video. A page that fits the frame does not
    move at all — which is what someone reading a short page does.
    """
    from . import formats

    frame = fmt or formats.VERTICAL
    height, ground = _page_shape(image, frame)
    travel = max(height - frame.height, 0)
    # Never faster than reading speed, however tall the page is.
    travel = min(travel, int(seconds / SECONDS_PER_SCREEN * frame.height))

    if travel <= 2:
        log("site: the page fits the frame — holding it")
        crop = f"crop={frame.width}:{frame.height}:0:0"
    else:
        # smoothstep over the clip: 3t² − 2t³, with t the fraction elapsed
        t = f"min(t/{seconds:.3f}\\,1)"
        eased = f"(3*pow({t}\\,2)-2*pow({t}\\,3))"
        crop = f"crop={frame.width}:{frame.height}:0:'{travel}*{eased}'"
        log(f"site: scrolling {travel}px of a {height}px page over {seconds:.1f}s")

    # A page shorter than the frame is padded with its own background colour,
    # not with black: the crop needs something to cut from, and a black band
    # under a short page reads as a broken render.
    pad = (f"pad={frame.width}:max(ih\\,{frame.height}):0:0:{ground}")
    render._run([  # noqa: SLF001 — the project's one ffmpeg runner
        "ffmpeg", "-y", "-loop", "1", "-i", str(image),
        "-t", f"{seconds:.3f}", "-vf",
        f"scale={frame.width}:-2:flags=lanczos,{pad},{crop},setsar=1,"
        f"format=yuv420p",
        "-r", str(settings.fps), "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", "-pix_fmt", "yuv420p", str(out)])
    return out


def _page_shape(image: Path, frame) -> tuple[int, str]:
    """The captured page's height once scaled to the frame's width, and the
    colour to pad it with — the page's own ground, sampled at its bottom."""
    from PIL import Image

    with Image.open(image) as picture:
        width, height = picture.size
        sample = picture.convert("RGB").getpixel((width // 2, height - 1))
    scaled = int(round(height * (frame.width / width))) if width else height
    return scaled, "0x{:02x}{:02x}{:02x}".format(*sample)


def _dismiss_consent(page, log) -> None:
    for selector in CONSENT_SELECTORS:
        try:
            button = page.locator(selector).first
            if button.is_visible(timeout=600):
                button.click(timeout=1200)
                log("site: dismissed a cookie banner")
                return
        except Exception:  # noqa: BLE001 — no banner is the common case
            continue


def _wake_lazy_content(page) -> None:
    """Scroll to the bottom and back, so what loads on scroll is loaded."""
    page.evaluate(
        """() => new Promise((resolve) => {
            const doc = document.scrollingElement || document.documentElement;
            let y = 0;
            const step = () => {
                y += window.innerHeight;
                window.scrollTo(0, y);
                if (y < doc.scrollHeight) setTimeout(step, 90);
                else { window.scrollTo(0, 0); setTimeout(resolve, 250); }
            };
            step();
        })"""
    )
