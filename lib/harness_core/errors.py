from __future__ import annotations


class HarnessError(Exception):
    pass


class TamperError(HarnessError):
    pass


class SchemaError(HarnessError):
    pass


class Denied(HarnessError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
