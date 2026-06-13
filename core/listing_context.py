def build_listing_xml(listing_data: dict) -> str:
    """Build a populated <ListingContext> XML string from a CRM listing dict.

    Accepts two formats:
    - CRM field names (e.g. 'c_listing_askingprice_c') — old direct-DB format
    - Human-readable labels (e.g. 'Asking Price') — new actionListingDetail format
    """
    import re

    def _safe(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _to_tag(label: str) -> str:
        tag = re.sub(r"[^a-zA-Z0-9]", "", label.title().replace(" ", ""))
        return tag if tag and tag[0].isalpha() else "Field" + tag

    lines = ["<ListingContext>"]
    emitted: set[str] = set()

    # Pass 1 — known CRM field names
    for field_name, tag in FIELD_LABEL_MAP.items():
        value = listing_data.get(field_name)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        lines.append(f"  <{tag}>{_safe(text)}</{tag}>")
        emitted.add(tag)

    # Pass 2 — human-readable label keys (new format from actionListingDetail)
    for key, value in listing_data.items():
        if key in FIELD_LABEL_MAP:
            continue  # already handled above
        if value is None:
            continue
        text = str(value).strip()
        if not text or text.lower() in ("none", "null", "0"):
            continue
        tag = _to_tag(str(key))
        if tag in emitted:
            continue
        lines.append(f"  <{tag}>{_safe(text)}</{tag}>")
        emitted.add(tag)

    lines.append("</ListingContext>")
    return "\n".join(lines)


FIELD_LABEL_MAP: dict[str, str] = {
    "assignedTo": "CRMUser",
    "createDate": "CreateDate",
    "lastUpdated": "LastUpdated",
    "lastActivity": "LastActivity",
    "updatedBy": "UpdatedBy",
    "name": "Name",
    "nameId": "NameID",
    "description": "Description",
    "visibility": "Visibility",
    "id": "ID",
    "c_additionalTerms": "AdditionalTerms",
    "c_assigned_user_id": "Broker",
    "c_automaticRelease": "AutomaticReleaseFiles",
    "c_businesscategories": "BusinessCategories",
    "c_Buyer": "Buyer",
    "c_cam": "CAM",
    "c_campaign_id": "CampaignID",
    "c_Commission": "Commission",
    "c_Contractdateend": "ContractDateEnd",
    "c_Contractdatestart": "ContractDateStart",
    "c_created_by": "CreatedBy",
    "c_currency_id": "Currency",
    "c_dateAgreementExpired": "DateAgreementExpired",
    "c_date_closed": "DateAgreementSigned",
    "c_date_entered": "DateEntered",
    "c_date_modified": "DateModified",
    "c_daysOpen": "DaysOpen",
    "c_description_html": "DescriptionIsHTML",
    "c_employeehealthinsurance": "EmployeeHealthInsurance",
    "c_expirationdate": "ExpirationDate",
    "c_ExpiryDate": "ExpiryDate",
    "c_ffae": "FFAndE",
    "c_ffeIncluded": "FFEIncluded",
    "c_files_folders": "FilesAndFolders",
    "c_financial_addback_interest_c": "InterestEgLineOfCredit",
    "c_financial_addback_tande_c": "TravelAndEntertainmentAddback",
    "c_financial_advertising_c": "Advertising",
    "c_financial_businessloans_c": "BusinessLoans",
    "c_financial_ccfees_c": "CreditCardFees",
    "c_financial_cellphones_c": "CellPhones",
    "c_financial_cgstotal_c": "CostOfGoodsSold",
    "c_financial_cgs_c": "CostOfGoodsSoldPercent",
    "c_financial_fixturevalue_c": "FixtureValue",
    "c_financial_fuelvehicle_c": "FuelAndVehicleExpense",
    "c_financial_grossprofit_c": "GrossProfit",
    "c_financial_grossrevenue_c": "GrossRevenue",
    "c_financial_healthins_c": "HealthInsurance",
    "c_financial_healthins_owner_c": "OwnersHealthInsuranceAddback",
    "c_financial_ins_c": "FinancialInsurance",
    "c_financial_interest_c": "Interest",
    "c_financial_inventoryval_c": "InventoryValue",
    "c_financial_leasedequip_c": "LeasedEquipment",
    "c_financial_leaseimpr_c": "LeaseImprovements",
    "c_financial_legal_acct_c": "LegalAccounting",
    "c_financial_loans_c": "Loans",
    "c_financial_monthly_expense_c": "MonthlyExpense",
    "c_financial_monthly_net_c": "MonthlyNet",
    "c_financial_monthly_other_c": "OtherMonthlyExpenses",
    "c_financial_monthly_profit_c": "MonthlyProfit",
    "c_financial_monthly_revenue_c": "MonthlyRevenue",
    "c_financial_monthly_sales_c": "MonthlySales",
    "c_financial_net_cashflow_c": "NetCashFlow",
    "c_financial_net_profit_c": "NetProfit",
    "c_financial_notedutd_c": "AreBusinessNotesUpToDate",
    "c_financial_officersalaries_c": "OfficersSalaries",
    "c_financial_officersalary_c": "OfficerSalary",
    "c_financial_other_income_c": "OtherIncome",
    "c_financial_ownercc_c": "OwnersCredirCard",
    "c_financial_ownercell_c": "OwnersCellPhone",
    "c_financial_ownerfuel_c": "OwnersFuelExpense",
    "c_financial_ownerhealthins_c": "OwnersHealthInsurance",
    "c_financial_ownerlease_c": "OwnerCarLeasePayments",
    "c_financial_payrolltaxes_c": "PayrollTaxes",
    "c_financial_payroll_c": "Payroll",
    "c_financial_postage_c": "Postage",
    "c_financial_propmaintenance_c": "PropertyMaintenance",
    "c_financial_rentincrease_c": "RentalIncrease",
    "c_financial_rent_c": "Rent",
    "c_financial_repairsmaint_c": "RepairsAndMaintenance",
    "c_financial_rubbish_c": "RubbishRemoval",
    "c_financial_sales_c": "Sales",
    "c_financial_supplies_c": "Supplies",
    "c_financial_telephone_c": "TelephoneInternet",
    "c_financial_te_c": "TravelAndEntertainment",
    "c_financial_total_expenses_c": "TotalExpenses",
    "c_financial_utilities_c": "Utilities",
    "c_financial_vehicles_c": "LeasedVehicles",
    "c_ispropertyleased": "IsPropertyLeased",
    "c_Leaseterms": "LeaseTerms",
    "c_lesssalestax": "LessSalesTax",
    "c_LicenseRequired": "LicenseRequired",
    "c_liensTotal": "LiensTotal",
    "c_listingFiles": "ListingFilesTemplate",
    "c_listingStatus": "ListingStatus",
    "c_listing_address_c": "Address",
    "c_listing_area_c": "StoreSizeSqF",
    "c_listing_askingprice_c": "AskingPrice",
    "c_listing_city_c": "City",
    "c_listing_country_c": "Country",
    "c_listing_currently_operating_c": "CurrentlyOperating",
    "c_listing_dateofsale_c": "DateOfSale",
    "c_listing_date_approved_c": "DateApproved",
    "c_listing_downpayment_c": "DownPayment",
    "c_listing_emp_ft_c": "FTEmployees",
    "c_listing_emp_pt_c": "PTEmployees",
    "c_listing_exclusive_c": "Exclusive",
    "c_listing_featured_c": "FeaturedListing",
    "c_listing_franchise_c": "Franchise",
    "c_listing_frontend_id_c": "RefID",
    "c_listing_frontend_url": "FrontendURL",
    "c_listing_homebusiness_c": "HomeBasedBusiness",
    "c_listing_hours_c": "HoursOfOperation",
    "c_listing_inventory_incl_c": "InventoryStockIncludedInPrice",
    "c_listing_leasecopy_c": "LeaseCopyAvailable",
    "c_listing_llname_c": "LandlordsName",
    "c_listing_llphone_c": "LandlordsPhone",
    "c_listing_newfranchise_c": "NewFranchise",
    "c_listing_pkgspace_c": "ParkingSpaces",
    "c_listing_postal_c": "PostalCode",
    "c_listing_reasonforselling_c": "ReasonForSelling",
    "c_listing_reavail_c": "RealEstateAvailable",
    "c_listing_region_c": "State",
    "c_listing_relocatable_c": "Relocatable",
    "c_listing_rentutd_c": "RentUpToDate",
    "c_listing_security_c": "Security",
    "c_listing_support_training_c": "SupportTraining",
    "c_listing_terms_c": "Terms",
    "c_listing_town_c": "County",
    "c_listing_yearsest_c": "YearsInBusiness",
    "c_managementWillStay": "ManagementWillStay",
    "c_MarketingSitesListingIsPosted": "MarketingSitesListingIsPostedOn",
    "c_media1": "Media1",
    "c_media2": "Media2",
    "c_media3": "Media3",
    "c_media4": "Media4",
    "c_media5": "Media5",
    "c_meta_description": "MetaDescription",
    "c_modified_user_id": "ModifiedBy",
    "c_monthlyPayroll": "MonthlyPayroll",
    "c_monthlyProfit": "MonthlyProfit",
    "c_monthly_ownerscashflow": "MonthlyOwnersCashFlow",
    "c_name_dba_c": "DBAName",
    "c_name_generic_c": "GenericName",
    "c_nda": "NDA",
    "c_no_of_views": "NoOfViews",
    "c_opportunity_type": "OpportunityType",
    "c_otherincome": "OtherIncome",
    "c_ownerscashflow": "OwnersCashFlow",
    "c_realEstateAsking": "RealEstateAsking",
    "c_realEstateEstd": "RealEstateEstd",
    "c_realEstateIncluded": "RealEstateIncluded",
    "c_realEstatePrice": "RealEstatePrice",
    "c_recentleaseholdimprovements": "LeaseholdImprovements",
    "c_sales_stage": "SalesStage",
    "c_seller": "Seller",
    "c_sellingprice": "SellingPrice",
    "c_swot_liabilities_c": "Liabilities",
    "c_swot_notes_c": "Notes",
    "c_swot_opportunities_c": "Opportunities",
    "c_swot_strengths_c": "Strengths",
    "c_swot_threats_c": "Threats",
    "c_swot_weaknesses_c": "Weaknesses",
    "c_thumbnail": "Thumbnail",
}
