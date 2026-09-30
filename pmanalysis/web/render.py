"""Template rendering helper (Starlette 0.40+ API)."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import Response

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


def render(request: Request, name: str, **context) -> Response:
    return TEMPLATES.TemplateResponse(request, name, context)
