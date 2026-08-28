"""One-off: regenerate previews for the 4 non-classic templates after a template.py edit."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from core.llm import generate_cim_html
from scripts.generate_template_previews import DEMO_LISTING_NAME, DEMO_ASKING_PRICE, DEMO_FINDINGS


async def main() -> None:
    for template_id in ["minimalist", "editorial", "luxury", "startup"]:
        print(f"Generating preview for '{template_id}'...")
        html = await generate_cim_html(
            all_findings=[DEMO_FINDINGS],
            listing_xml="",
            listing_name=DEMO_LISTING_NAME,
            asking_price=DEMO_ASKING_PRICE,
            all_images=[],
            template_id=template_id,
        )
        out_path = os.path.join("previews", f"{template_id}.html")
        with open(out_path, "w") as f:
            f.write(html)
        print(f"  -> wrote {out_path} ({len(html)/1024:.1f} KB)")


if __name__ == "__main__":
    asyncio.run(main())
