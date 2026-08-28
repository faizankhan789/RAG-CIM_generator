"""One-off script: pre-generate a static demo CIM HTML for every design template.

Run manually whenever core/templates.py changes:
    python3 scripts/generate_template_previews.py

Outputs one HTML file per template to previews/<template_id>.html, served by
server.py's GET /template-preview/{template_id} endpoint. Uses real Claude
calls (small cost) so previews match production output exactly.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from core.llm import generate_cim_html
from core.templates import TEMPLATES

DEMO_LISTING_NAME = "Anchor & Vine Hospitality Group"
DEMO_ASKING_PRICE = "$8,500,000"

DEMO_FINDINGS = """\
## I. Executive Summary
- Anchor & Vine Hospitality Group operates a boutique collection of 3 waterfront hotels (212 total keys) across the Gulf Coast, founded in 2011.
- Key investment highlights: 92% average occupancy, repeat guest rate of 41%, award-winning F&B program contributing 34% of total revenue.
- Financial performance: FY2025 revenue of $14,200,000, Adjusted EBITDA of $3,950,000 (27.8% margin).
- Asking price: $8,500,000. Seller open to a 90-day transition consulting period.

## II. Company Overview
- Founded in 2011 by hospitality operators Maria Chen and David Okoye; grown from 1 property to 3 over 14 years.
- Ownership structure: 100% privately held, no outside investors.
- Values: guest-first service, sustainable sourcing, community partnership.
- Management team: Maria Chen (CEO, 14 yrs), David Okoye (COO, 14 yrs), Priya Anand (CFO, 6 yrs), Marcus Webb (VP Operations, 4 yrs).
- Location and facilities: 3 freehold waterfront properties in Clearwater, Sarasota, and Naples, FL — combined 212 keys, all renovated within the last 5 years.
- Products and services: rooms, 4 on-site restaurants, event/wedding venues, spa services at 2 properties.
- Competitive advantages: prime waterfront locations, long-tenured GMs at each property, strong online reputation (4.7/5 average across 6,200+ reviews).
- Market position: #2 independent boutique operator in the region by RevPAR.
- SWOT: Strengths — brand reputation, occupancy; Weaknesses — limited group/conference space; Opportunities — 4th property acquisition pipeline; Threats — new branded competitor entering Sarasota in 2027.

## III. Financial Information
- FY2023 revenue: $11,800,000. FY2024 revenue: $12,900,000. FY2025 revenue: $14,200,000.
- FY2025 Adjusted EBITDA: $3,950,000 (27.8% margin), up from $3,180,000 in FY2024.
- Revenue breakdown: Rooms $8,900,000 (62.7%), F&B $4,830,000 (34.0%), Spa & Other $470,000 (3.3%).
- RevPAR: $186. ADR: $202. Occupancy: 92%.
- Cost structure: Labor 31% of revenue, COGS (F&B) 28% of F&B revenue, Rent/Occupancy N/A (freehold).
- Debt structure: $2,100,000 outstanding mortgage across 2 properties at 5.4% fixed, maturing 2031.

## IV. Operations
- Technology and equipment: cloud-based PMS (Cloudbeds), centralized revenue management system across all 3 properties.
- Supply chain: F&B sourced from 6 regional vendors under annual contracts; no single vendor exceeds 18% of COGS.
- Quality certifications: AAA Four Diamond rating at 2 of 3 properties.
- Scalability: standardized SOPs and training program allow new-property onboarding within 90 days.

## V. Marketing and Sales
- Target market: leisure travelers (68%), small weddings/events (22%), corporate/bleisure (10%).
- Marketing channels: direct website (38% of bookings), OTAs (35%), repeat/referral (27%).
- Marketing & sales budget: $620,000 annually (4.4% of revenue).
- Branding: unified "Anchor & Vine" brand across all 3 properties since 2019 rebrand.
- Customer acquisition cost: $46 per booked reservation (blended).

## VI. Legal and Regulatory
- Legal structure: single Delaware LLC holding company with 3 property-level subsidiaries.
- Permits and licenses: all liquor, food service, and event licenses current at all locations.
- Insurance: $15,000,000 combined general liability + property coverage.
- Litigation: no pending or threatened litigation.

## VII. Human Resources
- Headcount: 168 employees (94 full-time, 74 part-time) across 3 properties.
- Benefits: health insurance for full-time staff, 401(k) with 3% match, F&B staff meal program.
- Employee turnover rate: 24% annually (below hospitality industry average of ~35%).
- Management succession: VP Operations identified as internal successor to COO.

## VIII. Growth Opportunities
- Expansion plans: identified 2 acquisition targets (4th and 5th properties) in adjacent Gulf Coast markets.
- New product development: expansion of spa services to the Clearwater property (currently only 2 of 3 have spas).
- Strategic partnerships: in discussion with a regional wedding-planning network for preferred-venue status.
- Market penetration: opportunity to grow corporate/bleisure segment from 10% to an estimated 18-20% of bookings via a dedicated sales hire.

## IX. Risks
- Competition: new branded hotel entering Sarasota market in 2027.
- Financial risks: F&B costs exposed to regional food cost inflation.
- Operational risks: hurricane/weather exposure typical of Gulf Coast coastal properties (fully insured).
- Key person risk: founder-led management team; succession plan in early stages for CEO/COO roles.

## X. Appendix
- Historical financial statements available for FY2021-FY2025 upon signed NDA.
- Third-party market study (regional hospitality demand, 2025) available upon request.
- Guest review data and reputation scorecards available upon request.
"""


async def _main() -> None:
    os.makedirs("previews", exist_ok=True)
    for template_id in TEMPLATES:
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
    asyncio.run(_main())
