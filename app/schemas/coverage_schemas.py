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


class TokenStatusResponse(BaseModel):
    has_token: bool
    expires_at: str | None
    minutes_remaining: float | None
    is_valid: bool


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
