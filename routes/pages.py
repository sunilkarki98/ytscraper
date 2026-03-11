"""
Page serving routes: /, /health, /favicon.
"""
import os

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/health")
async def health():
    """Health check for monitoring (UptimeRobot, etc.)."""
    return {"status": "ok", "service": "youtube-email-scraper-pro"}


def _render_html(filename: str) -> str:
    html_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", filename)
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Inject Supabase config dynamically via meta tags
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_ANON_KEY", "")
    meta_tags = f'\n    <meta name="supabase-url" content="{supabase_url}">\n    <meta name="supabase-anon-key" content="{supabase_key}">\n'
    return html.replace("</head>", f"{meta_tags}</head>")


@router.get("/")
async def serve_landing():
    html = _render_html("index.html")
    return HTMLResponse(content=html)


@router.get("/dashboard")
async def serve_dashboard():
    html = _render_html("dashboard.html")
    return HTMLResponse(content=html)


@router.get("/favicon.ico", include_in_schema=False)
async def serve_favicon():
    return Response(status_code=204)
