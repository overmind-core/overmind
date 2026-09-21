class InputValidationError(ValueError):
    """An authored client diagnostic, never a wrapped exception or provider response."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail
