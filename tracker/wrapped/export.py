"""The Year in Review as an image. The page is drawn in the browser (charts,
world map), so the export loads it the same way: one self-contained HTML
document - the app's CSS, the bundled fonts, the chart scripts and the map
data all inlined - screenshotted full height by headless Chromium. Needs
nothing from the web server, so it works the same from a request and from
the Celery worker."""

import json
import secrets
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string

from . import render

EXPORT_WIDTH = 1320
EXPORT_SCALE = 1.25

_SCRIPTS = ("d3-array.min.js", "d3-geo.min.js", "topojson-client.min.js")


def _static(*parts):
    return Path(settings.BASE_DIR, "static", *parts)


REVIEW_BODY = "tracker/partials/wrapped_review_body.html"
ALL_TIME_BODY = "tracker/partials/wrapped_all_time_body.html"


def review_document(context, body=REVIEW_BODY):
    """The standalone HTML for `context` (see service.year_in_review_page_context
    / service.all_time_context); `body` is the page's own template."""
    world = json.loads(_static("vendor", "world-110m.json").read_text(encoding="utf-8"))
    scripts = [_static("vendor", name).read_text(encoding="utf-8") for name in _SCRIPTS]
    scripts.append(f"window.__yrWorldData = {json.dumps(world, separators=(',', ':'))};")
    scripts.append(_static("js", "wrapped-review.js").read_text(encoding="utf-8"))
    return render_to_string(
        "tracker/wrapped_export.html",
        {
            **context,
            "export": True,
            "body_template": body,
            "css": _static("dist", "app.css").read_text(encoding="utf-8"),
            "fonts": (render.TEMPLATES_DIR / "cards" / "_fonts.html").read_text(encoding="utf-8"),
            "js": "\n".join(scripts).replace("</script", "<\\/script"),
        },
    )


def render_review_png(context, body=REVIEW_BODY):
    """Renders the Year in Review (or, with `body`, All Time) to
    <MEDIA_ROOT>/wrapped/<token>.png and returns (token, path)."""
    from playwright.sync_api import sync_playwright

    html = review_document(context, body)
    token = secrets.token_urlsafe(24)
    path = render.generated_dir() / f"{token}.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": EXPORT_WIDTH, "height": 1000}, device_scale_factor=EXPORT_SCALE)
            page.set_content(html, wait_until="networkidle")
            page.screenshot(path=str(path), full_page=True)
        finally:
            browser.close()
    return token, path
