from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # CMS API
    CMS_LCD_BASE_URL: str = "https://api.coverage.cms.gov/v1"
    CMS_LICENSE_AMA: bool = True
    CMS_LICENSE_ADA: bool = True
    CMS_LICENSE_AHA: bool = True
    TOKEN_REFRESH_BUFFER_MINUTES: int = 5
    TOKEN_EXPIRY_MINUTES: int = 60
    HTTP_TIMEOUT_SECONDS: float = 30.0

    # App
    APP_NAME: str = "CMS LCD Coverage Service"
    APP_VERSION: str = "1.0.0"
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
