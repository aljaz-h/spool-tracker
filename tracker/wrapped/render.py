"""Jinja2 -> HTML -> headless-Chromium screenshot -> PNG. The card layout
lives entirely in templates/cards/card.html as ordinary CSS, so changing the
look is editing an HTML file, not drawing code. Everything the card shows -
including the poster and backdrop artwork - is inlined into the HTML on the
server, so the screenshot never depends on a browser fetching images."""

import secrets
from pathlib import Path

from django.conf import settings
from jinja2 import Environment, FileSystemLoader, select_autoescape

# The share image formats: key -> (label, width, height).
FORMATS = {
    "portrait": ("Portrait 4:5", 1080, 1350),
    "square": ("Square", 1080, 1080),
    "story": ("Story 9:16", 1080, 1920),
}
DEFAULT_FORMAT = "portrait"

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader([str(TEMPLATES_DIR / "cards")]),
    autoescape=select_autoescape(["html"]),
)


def format_size(fmt):
    _label, width, height = FORMATS.get(fmt, FORMATS[DEFAULT_FORMAT])
    return width, height


def generated_dir():
    path = Path(settings.MEDIA_ROOT) / "wrapped"
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_html(template_name, context):
    return _env.get_template(f"{template_name}.html").render(**context)


def screenshot_html(html, output_path, width, height):
    """Needs Chromium (`playwright install chromium`, done in the image)."""
    from playwright.sync_api import sync_playwright

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(html, wait_until="load")
            page.screenshot(path=str(output_path))
        finally:
            browser.close()


def render_png(template_name, context, fmt=DEFAULT_FORMAT):
    """Renders to <MEDIA_ROOT>/wrapped/<token>.png - returns (token, path).
    The token is the random, unguessable part of the served URL."""
    width, height = format_size(fmt)
    html = render_html(template_name, {**context, "height": height})
    token = secrets.token_urlsafe(24)
    path = generated_dir() / f"{token}.png"
    screenshot_html(html, path, width, height)
    return token, path
