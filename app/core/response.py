"""
app/core/response.py
~~~~~~~~~~~~~~~~~~~~
Custom FastAPI response class that pretty-prints JSON output.
"""
import json
from typing import Any

from fastapi.responses import JSONResponse


class PrettyJSONResponse(JSONResponse):
    """JSONResponse with indent=4 so the raw HTTP response is human-readable."""

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=False, indent=4).encode("utf-8")
