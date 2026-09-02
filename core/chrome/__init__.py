"""Registry mapping template_id -> that template's chrome render() function.

Only the 5 built-in templates live here. The PDF-upload custom_template
path never touches this package.
"""

from __future__ import annotations

from typing import Callable

from core.chrome import classic, editorial, luxury, minimalist, startup

RENDERERS: dict[str, Callable[..., str]] = {
    "classic": classic.render,
    "minimalist": minimalist.render,
    "editorial": editorial.render,
    "luxury": luxury.render,
    "startup": startup.render,
}

_MODULES = {
    "minimalist": minimalist,
    "editorial": editorial,
    "luxury": luxury,
    "startup": startup,
}


def get_renderer(template_id: str) -> Callable[..., str]:
    """Look up a chrome renderer, defaulting to classic (mirrors
    core.templates.get_template's own default-to-classic behavior)."""
    return RENDERERS.get(template_id, classic.render)


def get_palette(template_id: str, industry: str = "", brand_primary: str = "",
                 brand_accent: str = "") -> dict:
    """Look up the color palette (primary/accent/light/mid) the section-body
    components should render with — same source of truth the chrome skeleton
    itself uses, so components always match the active template. "classic" is
    industry/brand-derived; the other 4 templates carry a fixed PALETTE."""
    if template_id == "classic":
        return classic.palette_for_industry(industry, brand_primary, brand_accent)
    module = _MODULES.get(template_id)
    return module.PALETTE if module else classic.palette_for_industry(industry, brand_primary, brand_accent)
