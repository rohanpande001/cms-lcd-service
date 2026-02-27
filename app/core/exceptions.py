class CMSAPIException(Exception):
    def __init__(self, message: str, status_code: int = 502, details: dict = None):
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


class TokenRefreshError(CMSAPIException):
    def __init__(self, message: str, details: dict = None):
        super().__init__(message=message, status_code=503, details=details)


class ArticleNotFoundError(CMSAPIException):
    def __init__(self, cpt_code: str):
        super().__init__(
            message=f"No LCD article found governing CPT code '{cpt_code}'. "
                    f"Provide 'article_id' directly if known.",
            status_code=404,
        )
        self.cpt_code = cpt_code
