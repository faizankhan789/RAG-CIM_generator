"""Domain models for the CIM extraction pipeline."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FileType(str, Enum):
    PDF = "pdf"
    SPREADSHEET = "spreadsheet"
    IMAGE = "image"
    PPT = "ppt"
    UNKNOWN = "unknown"


class FileItem(BaseModel):
    url: str = Field(default="")
    label: str = Field(default="")
    file_type: FileType = Field(default=FileType.UNKNOWN)
    content: str = Field(default="", description="Base64-encoded file content — skips download when set")


class ExtractionError(BaseModel):
    url: str
    stage: str
    message: str


class ExtractedContent(BaseModel):
    source_url: str
    file_type: FileType
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# CIM Section Models
# ---------------------------------------------------------------------------

class ExecutiveSummary(BaseModel):
    business_overview: str | None = None
    investment_highlights: list[str] = Field(default_factory=list)
    financial_performance_summary: str | None = None
    asking_price_and_terms: str | None = None


class CompanyOverview(BaseModel):
    history_and_background: str | None = None
    ownership_structure: str | None = None
    values: str | None = None
    management_team: list[dict[str, str]] = Field(default_factory=list)
    organizational_chart: str | None = None
    culture: str | None = None
    vision_and_mission: str | None = None
    location_and_facilities: str | None = None
    products_and_services: str | None = None
    competitive_advantages: str | None = None
    market_position: str | None = None
    social_responsibility: str | None = None
    swot_analysis: dict[str, list[str]] = Field(default_factory=dict)


class FinancialInformation(BaseModel):
    historical_financials: str | None = None
    adjusted_ebitda: str | None = None
    revenue_streams: str | None = None
    kpis: list[str] = Field(default_factory=list)
    tax_information: str | None = None
    financial_ratios: str | None = None
    projections_and_forecasts: str | None = None
    cost_structure: str | None = None
    capitalization_table: str | None = None
    debt_structure: str | None = None


class Operations(BaseModel):
    manufacturing_processes: str | None = None
    supply_chain_and_logistics: str | None = None
    key_suppliers_and_customers: str | None = None
    quality_certifications: str | None = None
    technology_and_equipment: str | None = None
    technology_infrastructure_security: str | None = None
    research_and_development: str | None = None
    intellectual_property: str | None = None
    scalability_and_growth: str | None = None


class MarketingAndSales(BaseModel):
    target_market: str | None = None
    marketing_strategies: str | None = None
    marketing_sales_budgets: str | None = None
    sales_pipeline: str | None = None
    customer_churn_rate: str | None = None
    sales_processes_and_channels: str | None = None
    branding_and_advertising: str | None = None
    customer_acquisition_costs: str | None = None
    customer_relationship_management: str | None = None


class LegalAndRegulatory(BaseModel):
    legal_structure_and_compliance: str | None = None
    permits_and_licenses: str | None = None
    data_privacy_and_security: str | None = None
    insurance_coverage: str | None = None
    ip_strategy: str | None = None
    contracts_and_agreements: str | None = None
    environmental_regulations: str | None = None
    international_compliance: str | None = None
    litigation_and_disputes: str | None = None


class HumanResources(BaseModel):
    employee_demographics_and_compensation: str | None = None
    benefits_and_training: str | None = None
    management_succession_plan: str | None = None
    key_employee_retention: str | None = None
    employee_turnover_rate: str | None = None
    labor_relations_and_unions: str | None = None


class GrowthOpportunities(BaseModel):
    expansion_plans: str | None = None
    new_product_development: str | None = None
    international_expansion: str | None = None
    strategic_partnerships: str | None = None
    market_penetration_and_diversification: str | None = None
    joint_ventures_and_alliances: str | None = None
    franchise_opportunities: str | None = None
    mergers_and_acquisitions: str | None = None


class Risks(BaseModel):
    industry_and_market_risks: str | None = None
    competition: str | None = None
    financial_risks: str | None = None
    reputation_and_brand_risks: str | None = None
    technology_risks: str | None = None
    political_and_economic_risks: str | None = None
    esg_risks: str | None = None
    operational_risks: str | None = None
    risk_assessment_matrix: str | None = None
    legal_and_regulatory_risks: str | None = None


class Appendix(BaseModel):
    detailed_financial_statements: str | None = None
    market_research_data: str | None = None
    appraisals_and_valuations: str | None = None
    legal_documents: str | None = None
    customer_testimonials: list[str] = Field(default_factory=list)
    industry_awards: list[str] = Field(default_factory=list)
    customer_journey_map: str | None = None
    competitive_analysis_matrix: str | None = None
    value_chain_analysis: str | None = None
    product_lifecycle_analysis: str | None = None
    market_segmentation_map: str | None = None


class CIMOutput(BaseModel):
    executive_summary: ExecutiveSummary = Field(default_factory=ExecutiveSummary)
    company_overview: CompanyOverview = Field(default_factory=CompanyOverview)
    financial_information: FinancialInformation = Field(default_factory=FinancialInformation)
    operations: Operations = Field(default_factory=Operations)
    marketing_and_sales: MarketingAndSales = Field(default_factory=MarketingAndSales)
    legal_and_regulatory: LegalAndRegulatory = Field(default_factory=LegalAndRegulatory)
    human_resources: HumanResources = Field(default_factory=HumanResources)
    growth_opportunities: GrowthOpportunities = Field(default_factory=GrowthOpportunities)
    risks: Risks = Field(default_factory=Risks)
    appendix: Appendix = Field(default_factory=Appendix)
    errors: list[ExtractionError] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
