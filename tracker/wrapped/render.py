"""Jinja2 -> HTML -> headless-Chromium screenshot -> PNG. The card layout
lives entirely in templates/*.html as ordinary CSS, so a new preset card is
"write an HTML file", not drawing code."""

import secrets
from pathlib import Path

from django.conf import settings
from jinja2 import Environment, FileSystemLoader, select_autoescape

CARD_WIDTH = 1080
CARD_HEIGHT = 1920
REPORT_WIDTH = 1080

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Display order = order in the picker; "spool" leads as the default.
AVAILABLE_TEMPLATES = {
    "spool": {"name": "Spool", "description": "Matches Spool's own theme"},
    "classic": {"name": "Classic", "description": "Dark gradient, bold numbers"},
    "minimal": {"name": "Minimal", "description": "Light, editorial, understated"},
    "vibrant": {"name": "Vibrant", "description": "Bright poster-style color blocks"},
}

# One loader over both dirs so year_in_review.html can include the same
# cards/_fonts.html the card presets use, instead of duplicating the
# embedded font data.
_env = Environment(
    loader=FileSystemLoader([str(TEMPLATES_DIR / "cards"), str(TEMPLATES_DIR / "reports")]),
    autoescape=select_autoescape(["html"]),
)


def generated_dir():
    path = Path(settings.MEDIA_ROOT) / "wrapped"
    path.mkdir(parents=True, exist_ok=True)
    return path


def render_html(template_name, context):
    return _env.get_template(f"{template_name}.html").render(**context)


def screenshot_html(html, output_path, width=CARD_WIDTH, height=CARD_HEIGHT, full_page=False):
    """Needs Chromium (`playwright install chromium`, done in the image)."""
    from playwright.sync_api import sync_playwright

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(html, wait_until="networkidle")
            page.screenshot(path=str(output_path), full_page=full_page)
        finally:
            browser.close()


def render_png(template_name, context, full_page=False):
    """Renders to <MEDIA_ROOT>/wrapped/<token>.png - returns (token, path).
    The token is the random, unguessable part of the served URL."""
    html = render_html(template_name, context)
    token = secrets.token_urlsafe(24)
    path = generated_dir() / f"{token}.png"
    screenshot_html(html, path, width=REPORT_WIDTH if full_page else CARD_WIDTH, full_page=full_page)
    return token, path
