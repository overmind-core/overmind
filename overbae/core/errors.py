class InputValidationError(ValueError):
    """An authored client diagnostic, never a wrapped exception or provider response."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class DatasetError(ValueError):
    """A rule the caller broke, phrased for the user. ``code`` names it for the API."""

    def __init__(self, detail: str, *, code: str = "dataset_rule") -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
