from pydantic import BaseModel, Field


class CptHcpcsCode(BaseModel):
    model_config = {"populate_by_name": True, "extra": "allow", "coerce_numbers_to_str": True}

    hcpc_code_id: str | None = Field(None, alias="hcpcCodeId")
    code: str | None = None
    description: str | None = None
    article_id: str | None = Field(None, alias="articleId")
    article_version: str | None = Field(None, alias="articleVersion")


class Icd10Code(BaseModel):
    model_config = {"populate_by_name": True, "extra": "allow", "coerce_numbers_to_str": True}

    icd10_code_id: str | None = Field(None, alias="icd10CodeId")
    code: str | None = None
    description: str | None = None
    article_id: str | None = Field(None, alias="articleId")


class ModifierCode(BaseModel):
    model_config = {"populate_by_name": True, "extra": "allow", "coerce_numbers_to_str": True}

    modifier_code: str | None = Field(None, alias="modifierCode")
    description: str | None = None
    article_id: str | None = Field(None, alias="articleId")


class CandidateArticle(BaseModel):
    """An article that maps to the queried CPT code, with resolution metadata."""

    article_id: str
    display_id: str | None = None
    title: str | None = None
    article_type: str | None = Field(None, description="CMS article type (1/4 = policy article, 5 = response to comments, 6 = billing and coding)")
    status: str | None = None
    eff_date: str | None = None
    end_date: str | None = None
    ncd_id: str | None = Field(None, description="Official CMS NCD link for this article, if any")
    governing_article_id: str | None = Field(
        None,
        description="For companion articles: the actual coverage policy article this one references",
    )
    icd10_covered: bool | None = Field(
        None,
        description="True/False if an icd10 was queried: whether this article's covered ICD-10 list contains it",
    )
    in_jurisdiction: bool | None = Field(
        None,
        description="True/False if a state/contractor was queried: whether this article applies in that jurisdiction",
    )
    role: str | None = Field(
        None,
        description="In qualified_articles: 'local' (applies in the queried jurisdiction) or 'governing-policy' (national policy the companions reference)",
    )
    cms_url: str | None = Field(
        None,
        description="Direct link to the article's page in the CMS Medicare Coverage Database",
    )


class LCDCoverageResponse(BaseModel):
    """Unified response containing all LCD coverage data for a CPT code."""

    cpt_code_queried: str = Field(..., description="The CPT/HCPCS code that was looked up")
    article_id: str = Field(..., description="LCD article ID governing this CPT code")
    cpt_hcpcs_codes: list[CptHcpcsCode] = Field(
        default_factory=list,
        description="All CPT/HCPCS codes covered under this LCD article",
    )
    icd10_covered_codes: list[Icd10Code] = Field(
        default_factory=list,
        description="ICD-10 diagnosis codes that establish medical necessity",
    )
    modifier_codes: list[ModifierCode] = Field(
        default_factory=list,
        description="CPT/HCPCS modifier codes applicable to this article",
    )
    total_cpt_codes: int = Field(..., description="Count of CPT/HCPCS codes returned")
    total_icd10_codes: int = Field(..., description="Count of ICD-10 codes returned")
    total_modifier_codes: int = Field(..., description="Count of modifier codes returned")
    icd10_queried: str | None = Field(
        None,
        description="The ICD-10 diagnosis code used to disambiguate, if provided",
    )
    article_selection: str | None = Field(
        None,
        description="How the article_id was resolved (e.g. icd10-exact-match, governing-policy-article)",
    )
    coverage_source: str | None = Field(
        None,
        description="The official coverage source when CMS links this code to an NCD (e.g. 'NCD 158, NCD Manual §250.3 — ...'). NCD criteria live outside the MCD article database.",
    )
    jurisdiction: str | None = Field(
        None,
        description="Jurisdiction label applied to the lookup (e.g. 'jurisdiction: New York'). 'national (all jurisdictions)' when no state/contractor was given.",
    )
    excluded_articles: list[str] = Field(
        default_factory=list,
        description="Articles that map to the CPT code but do NOT apply in the queried jurisdiction — do not cite these.",
    )
    candidate_articles: list[CandidateArticle] = Field(
        default_factory=list,
        description="All articles mapping to the CPT code, when more than one — pick by the patient's diagnosis or pass article_id explicitly",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "cpt_code_queried": "82306",
                "article_id": "52399",
                "cpt_hcpcs_codes": [
                    {"code": "82306", "description": "Vitamin D; 25 hydroxy"}
                ],
                "icd10_covered_codes": [
                    {"code": "E55.9", "description": "Vitamin D deficiency, unspecified"}
                ],
                "modifier_codes": [],
                "total_cpt_codes": 1,
                "total_icd10_codes": 47,
                "total_modifier_codes": 0,
            }
        }
    }


class DXCodeSource(BaseModel):
    article_id: str
    group: str | None = Field(None, description="ICD-10 covered group (1 = supports medical necessity)")


class DXCode(BaseModel):
    code: str
    description: str | None = None
    sources: list[DXCodeSource] = Field(
        default_factory=list,
        description="Every article tied to the J code that covers this dx",
    )


class DXCheckResponse(BaseModel):
    """Result of verifying one diagnosis against a J code's coverage articles (or one article)."""

    cpt_code: str | None = Field(None, description="The J/HCPCS code, when a J-code lookup was performed")
    icd10_queried: str
    supported: bool = Field(..., description="True if at least one article tied to the J code covers this dx")
    code_known: bool = Field(..., description="True if the dx (or a prefix) exists in the CMS ICD-10 index at all")
    supporting_articles: list[CandidateArticle] = Field(
        default_factory=list, description="Articles whose covered list contains the dx, with citation metadata"
    )
    noncovered_in: list[str] = Field(
        default_factory=[],
        description="Articles that explicitly list this dx as NOT covered — strong denial signal",
    )
    jurisdiction: str | None = Field(
        None,
        description="Scope the verdict was evaluated against — a state/contractor label, or the article number in article mode",
    )
    article_mode: bool = Field(False, description="True when the check was run against a specific article instead of a J code")
    verdict: str = Field(..., description="Human-readable conclusion for the PA workflow")


class DXCodesResponse(BaseModel):
    """All ICD-10 codes supported by any coverage document tied to a J code."""

    cpt_code: str
    total_dx_codes: int
    dx_codes: list[DXCode] = Field(
        default_factory=list,
        description="Union of covered ICD-10 codes across all articles tied to the J code, with source attribution",
    )
    candidate_articles: list[CandidateArticle] = Field(
        default_factory=list,
        description="The articles tied to this J code (for citation/audit)",
    )
    jurisdiction: str | None = Field(
        None,
        description="Jurisdiction label the dx union was scoped to (e.g. 'jurisdiction: New York')",
    )
    qualified_articles: list[CandidateArticle] = Field(
        default_factory=list,
        description="The exact Billing & Coding (type 6) articles the dx list was built from — cite these",
    )
    scope_note: str | None = Field(
        None,
        description="Present when no type 6 article existed in scope and policy articles were used instead",
    )


class ArticleResponse(BaseModel):
    """Full local lookup of one LCD article (search by article number)."""

    article_id: str
    display_id: str | None = None
    title: str | None = None
    article_type: str | None = Field(None, description="CMS article type (1/4 = policy, 5 = response to comments, 6 = billing and coding)")
    status: str | None = None
    eff_date: str | None = None
    end_date: str | None = None
    ncd_id: str | None = None
    ncd_section: str | None = None
    ncd_title: str | None = None
    governing_article_id: str | None = None
    contractor_names: list[str] = Field(default_factory=list, description="MAC/contractors this article applies to")
    states: list[str] = Field(default_factory=list, description="State abbreviations served by the article's contractors")
    hcpc_codes: list[str] = Field(default_factory=list, description="J/HCPCS/CPT codes mapped to this article")
    state_in_scope: bool | None = Field(
        None,
        description="True/False if a state was provided: whether the article applies in that state (None = state unknown)",
    )
    total_dx_codes: int
    dx_codes: list[DXCode] = Field(
        default_factory=list,
        description="Every ICD-10 code this article lists as covered, with group attribution",
    )


class TokenStatusResponse(BaseModel):
    has_token: bool
    expires_at: str | None
    minutes_remaining: float | None
    is_valid: bool


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
