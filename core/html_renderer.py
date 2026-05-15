"""Render a CIMOutput model as a professional standalone HTML document."""

from __future__ import annotations

from datetime import date
from typing import Any

from models import CIMOutput


def _esc(text: Any) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _val(value: Any) -> str:
    """Return escaped value or an em-dash placeholder."""
    if value is None or value == "" or value == []:
        return '<span class="empty">—</span>'
    return _esc(value)


def _rich_field(label: str, value: Any) -> str:
    empty = value is None or value == ""
    cls = " empty-field" if empty else ""
    return f"""
    <div class="rich-field{cls}">
        <div class="rich-label">{_esc(label)}</div>
        <div class="rich-value">{_val(value)}</div>
    </div>"""


def _bullet_list(label: str, items: list) -> str:
    if items:
        lis = "".join(f"<li>{_esc(i)}</li>" for i in items if i)
        content = f"<ul class='cim-list'>{lis}</ul>" if lis else '<span class="empty">—</span>'
    else:
        content = '<span class="empty">—</span>'
    return f"""
    <div class="rich-field">
        <div class="rich-label">{_esc(label)}</div>
        <div class="rich-value">{content}</div>
    </div>"""


def _swot(analysis: dict | None) -> str:
    def quad(cls: str, title: str, items: list) -> str:
        if items:
            lis = "".join(f"<li>{_esc(i)}</li>" for i in (items or []) if i)
            inner = f"<ul>{lis}</ul>" if lis else '<span class="empty">—</span>'
        else:
            inner = '<span class="empty">—</span>'
        return f"""
        <div class="swot-quad {cls}">
            <div class="swot-title">{title}</div>
            {inner}
        </div>"""
    a = analysis or {}
    return f"""
    <div class="rich-field">
        <div class="rich-label">SWOT Analysis</div>
        <div class="rich-value no-pad">
            <div class="swot-grid">
                {quad("strengths",     "Strengths",     a.get("strengths", []))}
                {quad("weaknesses",    "Weaknesses",    a.get("weaknesses", []))}
                {quad("opportunities", "Opportunities", a.get("opportunities", []))}
                {quad("threats",       "Threats",       a.get("threats", []))}
            </div>
        </div>
    </div>"""


def _mgmt_team(team: list) -> str:
    if team:
        cards = ""
        for p in team:
            if isinstance(p, dict):
                name  = _esc(p.get("name", ""))
                title = _esc(p.get("title", ""))
                cards += f"""
                <div class="mgmt-card">
                    <div class="mgmt-avatar">{name[:1].upper() if name else "?"}</div>
                    <div class="mgmt-info">
                        <div class="mgmt-name">{name}</div>
                        <div class="mgmt-title">{title}</div>
                    </div>
                </div>"""
        content = f"<div class='mgmt-grid'>{cards}</div>" if cards else '<span class="empty">—</span>'
    else:
        content = '<span class="empty">—</span>'
    return f"""
    <div class="rich-field">
        <div class="rich-label">Management Team</div>
        <div class="rich-value no-pad">{content}</div>
    </div>"""


def _section(number: str, title: str, body: str) -> str:
    return f"""
    <section class="cim-section">
        <div class="section-header">
            <span class="section-number">{number}</span>
            <h2 class="section-title">{title}</h2>
        </div>
        <div class="section-body">
            {body}
        </div>
    </section>"""


def render_cim_html(cim: CIMOutput, listing_name: str = "", asking_price: str = "") -> str:
    d   = cim.model_dump(mode="json")
    es  = d.get("executive_summary",    {}) or {}
    co  = d.get("company_overview",     {}) or {}
    fi  = d.get("financial_information",{}) or {}
    ops = d.get("operations",           {}) or {}
    ms  = d.get("marketing_and_sales",  {}) or {}
    lr  = d.get("legal_and_regulatory", {}) or {}
    hr  = d.get("human_resources",      {}) or {}
    go  = d.get("growth_opportunities", {}) or {}
    ri  = d.get("risks",                {}) or {}
    ap  = d.get("appendix",             {}) or {}

    biz_name = _esc(listing_name or "Business")
    price    = _esc(asking_price or es.get("asking_price_and_terms") or "")
    today    = date.today().strftime("%B %d, %Y")

    # ── I. Executive Summary ─────────────────────────────────────────────────
    es_lead = ""
    if es.get("business_overview"):
        es_lead = f"<p class='lead'>{_esc(es['business_overview'])}</p>"
    es_body = es_lead + "".join([
        _bullet_list("Investment Highlights",        es.get("investment_highlights", [])),
        _rich_field("Financial Performance Summary", es.get("financial_performance_summary")),
        _rich_field("Asking Price & Terms",          es.get("asking_price_and_terms")),
    ])

    # ── II. Company Overview ─────────────────────────────────────────────────
    co_body = "".join([
        _rich_field("History & Background",    co.get("history_and_background")),
        _rich_field("Ownership Structure",     co.get("ownership_structure")),
        _rich_field("Vision & Mission",        co.get("vision_and_mission")),
        _rich_field("Core Values",             co.get("values")),
        _rich_field("Culture",                 co.get("culture")),
        _rich_field("Location & Facilities",   co.get("location_and_facilities")),
        _rich_field("Products & Services",     co.get("products_and_services")),
        _rich_field("Competitive Advantages",  co.get("competitive_advantages")),
        _rich_field("Market Position",         co.get("market_position")),
        _rich_field("Social Responsibility",   co.get("social_responsibility")),
        _rich_field("Organizational Chart",    co.get("organizational_chart")),
        _mgmt_team(co.get("management_team") or []),
        _swot(co.get("swot_analysis")),
    ])

    # ── III. Financial Information ───────────────────────────────────────────
    fi_body = "".join([
        _rich_field("Historical Financials",      fi.get("historical_financials")),
        _rich_field("Adjusted EBITDA",            fi.get("adjusted_ebitda")),
        _rich_field("Revenue Streams",            fi.get("revenue_streams")),
        _bullet_list("Key Performance Indicators",fi.get("kpis") or []),
        _rich_field("Tax Information",            fi.get("tax_information")),
        _rich_field("Financial Ratios",           fi.get("financial_ratios")),
        _rich_field("Projections & Forecasts",    fi.get("projections_and_forecasts")),
        _rich_field("Cost Structure",             fi.get("cost_structure")),
        _rich_field("Capitalization Table",       fi.get("capitalization_table")),
        _rich_field("Debt Structure",             fi.get("debt_structure")),
    ])

    # ── IV. Operations ───────────────────────────────────────────────────────
    ops_body = "".join([
        _rich_field("Manufacturing & Processes",       ops.get("manufacturing_processes")),
        _rich_field("Supply Chain & Logistics",        ops.get("supply_chain_and_logistics")),
        _rich_field("Key Suppliers & Customers",       ops.get("key_suppliers_and_customers")),
        _rich_field("Quality Certifications",          ops.get("quality_certifications")),
        _rich_field("Technology & Equipment",          ops.get("technology_and_equipment")),
        _rich_field("Technology Infrastructure",       ops.get("technology_infrastructure_security")),
        _rich_field("Research & Development",          ops.get("research_and_development")),
        _rich_field("Intellectual Property",           ops.get("intellectual_property")),
        _rich_field("Scalability & Growth",            ops.get("scalability_and_growth")),
    ])

    # ── V. Marketing & Sales ─────────────────────────────────────────────────
    ms_body = "".join([
        _rich_field("Target Market",                    ms.get("target_market")),
        _rich_field("Marketing Strategies",             ms.get("marketing_strategies")),
        _rich_field("Marketing & Sales Budgets",        ms.get("marketing_sales_budgets")),
        _rich_field("Sales Pipeline",                   ms.get("sales_pipeline")),
        _rich_field("Customer Churn Rate",              ms.get("customer_churn_rate")),
        _rich_field("Sales Processes & Channels",       ms.get("sales_processes_and_channels")),
        _rich_field("Branding & Advertising",           ms.get("branding_and_advertising")),
        _rich_field("Customer Acquisition Costs",       ms.get("customer_acquisition_costs")),
        _rich_field("Customer Relationship Management", ms.get("customer_relationship_management")),
    ])

    # ── VI. Legal & Regulatory ───────────────────────────────────────────────
    lr_body = "".join([
        _rich_field("Legal Structure & Compliance", lr.get("legal_structure_and_compliance")),
        _rich_field("Permits & Licenses",           lr.get("permits_and_licenses")),
        _rich_field("Data Privacy & Security",      lr.get("data_privacy_and_security")),
        _rich_field("Insurance Coverage",           lr.get("insurance_coverage")),
        _rich_field("IP Strategy",                  lr.get("ip_strategy")),
        _rich_field("Contracts & Agreements",       lr.get("contracts_and_agreements")),
        _rich_field("Environmental Regulations",    lr.get("environmental_regulations")),
        _rich_field("International Compliance",     lr.get("international_compliance")),
        _rich_field("Litigation & Disputes",        lr.get("litigation_and_disputes")),
    ])

    # ── VII. Human Resources ─────────────────────────────────────────────────
    hr_body = "".join([
        _rich_field("Employee Demographics & Compensation", hr.get("employee_demographics_and_compensation")),
        _rich_field("Benefits & Training",                  hr.get("benefits_and_training")),
        _rich_field("Management Succession Plan",           hr.get("management_succession_plan")),
        _rich_field("Key Employee Retention",               hr.get("key_employee_retention")),
        _rich_field("Employee Turnover Rate",               hr.get("employee_turnover_rate")),
        _rich_field("Labor Relations & Unions",             hr.get("labor_relations_and_unions")),
    ])

    # ── VIII. Growth Opportunities ───────────────────────────────────────────
    go_body = "".join([
        _rich_field("Expansion Plans",                         go.get("expansion_plans")),
        _rich_field("New Product Development",                 go.get("new_product_development")),
        _rich_field("International Expansion",                 go.get("international_expansion")),
        _rich_field("Strategic Partnerships",                  go.get("strategic_partnerships")),
        _rich_field("Market Penetration & Diversification",    go.get("market_penetration_and_diversification")),
        _rich_field("Joint Ventures & Alliances",              go.get("joint_ventures_and_alliances")),
        _rich_field("Franchise Opportunities",                 go.get("franchise_opportunities")),
        _rich_field("Mergers & Acquisitions",                  go.get("mergers_and_acquisitions")),
    ])

    # ── IX. Risks ────────────────────────────────────────────────────────────
    ri_body = "".join([
        _rich_field("Industry & Market Risks",   ri.get("industry_and_market_risks")),
        _rich_field("Competition",               ri.get("competition")),
        _rich_field("Financial Risks",           ri.get("financial_risks")),
        _rich_field("Reputation & Brand Risks",  ri.get("reputation_and_brand_risks")),
        _rich_field("Technology Risks",          ri.get("technology_risks")),
        _rich_field("Political & Economic Risks",ri.get("political_and_economic_risks")),
        _rich_field("ESG Risks",                 ri.get("esg_risks")),
        _rich_field("Operational Risks",         ri.get("operational_risks")),
        _rich_field("Risk Assessment Matrix",    ri.get("risk_assessment_matrix")),
        _rich_field("Legal & Regulatory Risks",  ri.get("legal_and_regulatory_risks")),
    ])

    # ── X. Appendix ──────────────────────────────────────────────────────────
    ap_body = "".join([
        _rich_field("Detailed Financial Statements", ap.get("detailed_financial_statements")),
        _rich_field("Market Research Data",          ap.get("market_research_data")),
        _rich_field("Appraisals & Valuations",       ap.get("appraisals_and_valuations")),
        _rich_field("Legal Documents",               ap.get("legal_documents")),
        _bullet_list("Customer Testimonials",        ap.get("customer_testimonials") or []),
        _bullet_list("Industry Awards",              ap.get("industry_awards") or []),
        _rich_field("Customer Journey Map",          ap.get("customer_journey_map")),
        _rich_field("Competitive Analysis Matrix",   ap.get("competitive_analysis_matrix")),
        _rich_field("Value Chain Analysis",          ap.get("value_chain_analysis")),
        _rich_field("Product Lifecycle Analysis",    ap.get("product_lifecycle_analysis")),
        _rich_field("Market Segmentation Map",       ap.get("market_segmentation_map")),
    ])

    sections = "".join([
        _section("I",    "Executive Summary",      es_body),
        _section("II",   "Company Overview",       co_body),
        _section("III",  "Financial Information",  fi_body),
        _section("IV",   "Operations",             ops_body),
        _section("V",    "Marketing & Sales",      ms_body),
        _section("VI",   "Legal & Regulatory",     lr_body),
        _section("VII",  "Human Resources",        hr_body),
        _section("VIII", "Growth Opportunities",   go_body),
        _section("IX",   "Risks",                  ri_body),
        _section("X",    "Appendix",               ap_body),
    ])

    price_chip = f'<div class="cover-price">Asking Price: {price}</div>' if price else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CIM — {biz_name}</title>
<style>
  :root {{
    --navy:   #1a3557;
    --teal:   #0d7c8f;
    --gold:   #c8922a;
    --light:  #f4f7fb;
    --border: #d0daea;
    --text:   #1e2b3a;
    --muted:  #5a6e84;
    --white:  #ffffff;
  }}
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 14px;
    color: var(--text);
    background: #e8edf4;
    line-height: 1.7;
  }}

  /* ── Cover ── */
  .cover {{
    background: linear-gradient(155deg, var(--navy) 0%, #0f2640 60%, #0a1e35 100%);
    color: var(--white);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    text-align: center;
    padding: 60px 40px;
    position: relative;
    overflow: hidden;
    page-break-after: always;
  }}
  .cover::before {{
    content: '';
    position: absolute; top: -80px; right: -80px;
    width: 340px; height: 340px;
    background: rgba(13,124,143,0.18); border-radius: 50%;
  }}
  .cover::after {{
    content: '';
    position: absolute; bottom: -60px; left: -60px;
    width: 260px; height: 260px;
    background: rgba(200,146,42,0.12); border-radius: 50%;
  }}
  .cover-badge {{
    display: inline-block;
    border: 1px solid rgba(200,146,42,0.6);
    color: #f0c060;
    font-size: 11px; font-weight: 700;
    letter-spacing: 0.2em; text-transform: uppercase;
    padding: 6px 18px; border-radius: 20px; margin-bottom: 32px;
  }}
  .cover-title {{
    font-size: 13px; font-weight: 600;
    letter-spacing: 0.18em; text-transform: uppercase;
    color: rgba(255,255,255,0.55); margin-bottom: 14px;
  }}
  .cover-name {{
    font-size: clamp(28px, 5vw, 52px);
    font-weight: 800; line-height: 1.15;
    margin-bottom: 20px; letter-spacing: -0.02em;
  }}
  .cover-divider {{
    width: 60px; height: 3px;
    background: var(--gold); margin: 24px auto; border-radius: 2px;
  }}
  .cover-price {{
    display: inline-block;
    background: rgba(255,255,255,0.1);
    border: 1px solid rgba(255,255,255,0.2);
    border-radius: 8px; padding: 10px 28px;
    font-size: 16px; font-weight: 600; margin-top: 10px;
  }}
  .cover-date {{
    position: absolute; bottom: 36px;
    font-size: 12px; color: rgba(255,255,255,0.4); letter-spacing: 0.08em;
  }}
  .cover-confidential {{
    position: absolute; top: 28px; right: 28px;
    background: rgba(200,146,42,0.2);
    border: 1px solid rgba(200,146,42,0.5);
    color: #f0c060; font-size: 10px; font-weight: 700;
    letter-spacing: 0.15em; text-transform: uppercase;
    padding: 4px 12px; border-radius: 4px;
  }}
  .cover-powered {{
    position: absolute; bottom: 36px; left: 40px;
    font-size: 11px; color: rgba(255,255,255,0.3);
  }}

  /* ── Page wrapper ── */
  .page {{ max-width: 960px; margin: 0 auto; background: var(--white); box-shadow: 0 4px 32px rgba(0,0,0,0.12); }}

  /* ── Disclaimer ── */
  .disclaimer-bar {{
    background: #fffbeb; border-bottom: 1px solid #fde68a;
    padding: 10px 40px; font-size: 11.5px; color: #78350f; text-align: center;
  }}

  /* ── Body ── */
  .cim-body {{ padding: 48px 52px; }}

  /* ── Section ── */
  .cim-section {{ margin-bottom: 52px; }}
  .section-header {{
    display: flex; align-items: center; gap: 14px;
    border-bottom: 2px solid var(--navy);
    padding-bottom: 10px; margin-bottom: 20px;
  }}
  .section-number {{
    background: var(--navy); color: var(--white);
    font-size: 11px; font-weight: 700;
    min-width: 32px; height: 32px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  }}
  .section-title {{ font-size: 19px; font-weight: 700; color: var(--navy); letter-spacing: -0.01em; }}

  /* ── Lead ── */
  p.lead {{
    font-size: 15px; color: var(--text); line-height: 1.8;
    margin-bottom: 16px;
    background: var(--light);
    border-left: 4px solid var(--teal);
    padding: 14px 18px; border-radius: 0 6px 6px 0;
  }}

  /* ── Rich field ── */
  .rich-field {{
    margin-bottom: 12px;
    border: 1px solid var(--border);
    border-radius: 6px; overflow: hidden;
  }}
  .rich-field.empty-field {{ opacity: 0.55; }}
  .rich-label {{
    background: var(--light); color: var(--navy);
    font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.08em;
    padding: 6px 14px; border-bottom: 1px solid var(--border);
  }}
  .rich-value {{
    padding: 11px 14px; color: var(--text);
    white-space: pre-wrap; line-height: 1.75;
  }}
  .rich-value.no-pad {{ padding: 0; }}
  span.empty {{ color: #aab4c4; font-style: italic; }}

  /* ── Lists ── */
  .cim-list {{ list-style: none; padding: 0; }}
  .cim-list li {{
    padding: 6px 0 6px 20px; position: relative;
    color: var(--text); border-bottom: 1px solid #f0f3f8;
  }}
  .cim-list li:last-child {{ border-bottom: none; }}
  .cim-list li::before {{
    content: ''; position: absolute; left: 0; top: 15px;
    width: 7px; height: 7px; border-radius: 50%; background: var(--teal);
  }}

  /* ── SWOT ── */
  .swot-grid {{
    display: grid; grid-template-columns: 1fr 1fr;
    gap: 0; border-top: 1px solid var(--border);
  }}
  .swot-quad {{ padding: 16px 18px; border: 1px solid var(--border); border-top: none; }}
  .swot-quad:nth-child(odd) {{ border-right: none; }}
  .swot-title {{ font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 10px; }}
  .swot-quad ul {{ list-style: none; padding: 0; }}
  .swot-quad ul li {{ padding: 3px 0 3px 14px; position: relative; font-size: 13px; }}
  .swot-quad ul li::before {{ content: '•'; position: absolute; left: 0; font-weight: 700; }}
  .strengths     {{ background: #f0fdf4; }} .strengths     .swot-title {{ color: #166534; }} .strengths     ul li::before {{ color: #16a34a; }}
  .weaknesses    {{ background: #fef2f2; }} .weaknesses    .swot-title {{ color: #991b1b; }} .weaknesses    ul li::before {{ color: #dc2626; }}
  .opportunities {{ background: #eff6ff; }} .opportunities .swot-title {{ color: #1e40af; }} .opportunities ul li::before {{ color: #2563eb; }}
  .threats       {{ background: #fffbeb; }} .threats       .swot-title {{ color: #92400e; }} .threats       ul li::before {{ color: #d97706; }}

  /* ── Management team ── */
  .mgmt-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 10px; padding: 12px;
  }}
  .mgmt-card {{
    display: flex; align-items: center; gap: 10px;
    border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 12px; background: var(--light);
  }}
  .mgmt-avatar {{
    width: 38px; height: 38px; background: var(--navy); color: var(--white);
    border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-size: 15px; font-weight: 700; flex-shrink: 0;
  }}
  .mgmt-name  {{ font-weight: 700; font-size: 13px; }}
  .mgmt-title {{ font-size: 11.5px; color: var(--muted); }}

  /* ── Footer ── */
  .cim-footer {{
    background: var(--navy); color: rgba(255,255,255,0.55);
    text-align: center; padding: 20px 40px;
    font-size: 11px; line-height: 1.6;
  }}
  .cim-footer strong {{ color: rgba(255,255,255,0.85); }}

  @media print {{
    body {{ background: white; }}
    .page {{ box-shadow: none; max-width: 100%; }}
    .cover {{ min-height: 100vh; page-break-after: always; }}
    .cim-section {{ page-break-inside: avoid; }}
  }}
</style>
</head>
<body>
<div class="page">

  <div class="cover">
    <div class="cover-confidential">Confidential</div>
    <div class="cover-badge">Confidential Information Memorandum</div>
    <div class="cover-title">Prepared for Qualified Buyers</div>
    <div class="cover-name">{biz_name}</div>
    <div class="cover-divider"></div>
    {price_chip}
    <div class="cover-date">Prepared: {today}</div>
    <div class="cover-powered">Generated by Vertica AI</div>
  </div>

  <div class="disclaimer-bar">
    <strong>CONFIDENTIAL:</strong> This document contains proprietary information intended solely for the named recipient(s).
    Any reproduction or distribution without written consent is strictly prohibited.
    All financial projections are estimates only and subject to independent verification.
  </div>

  <div class="cim-body">
    {sections}
  </div>

  <div class="cim-footer">
    <strong>CONFIDENTIAL INFORMATION MEMORANDUM</strong> &mdash; {biz_name}<br>
    Prepared by Vertica AI &bull; {today}<br>
    All information is believed to be accurate but is not guaranteed. Prospective buyers should conduct their own due diligence prior to completing any transaction.
  </div>

</div>
</body>
</html>"""
