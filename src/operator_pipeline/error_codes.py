"""Lightweight error type used by operator_library solvers."""


class OperatorInputError(ValueError):
    def __init__(self, code: str, **details):
        self.code = code
        self.details = details
        super().__init__(f"{code}: {details}")
