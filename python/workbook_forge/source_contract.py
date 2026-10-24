"""Structured source compiler diagnostics, separate from Excel cell errors."""

import json

MAX_ARTIFACT_BYTES = 1024 * 1024


class SourceDiagnostic(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def manifest_bytes(manifest: dict) -> bytes:
    """Exact compact bundle encoding, shared by producers and consumers."""
    payload = json.dumps(manifest, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise SourceDiagnostic("resource_limit", "manifest exceeds 1 MiB")
    return payload
