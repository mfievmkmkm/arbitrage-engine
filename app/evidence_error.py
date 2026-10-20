"""Structured public evidence; never retain arbitrary HTTP bodies or secrets."""


class EvidenceError(ValueError):
    def __init__(self, reason, **diagnostics):
        super().__init__(reason)
        self.diagnostics = diagnostics
