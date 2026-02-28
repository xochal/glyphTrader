"""
Auth middleware, CORS, credential redaction in logging.
"""

import re
import logging


class CredentialRedactionFilter(logging.Filter):
    """Strip Bearer tokens and known sensitive patterns from all log output."""

    _patterns = [
        re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE),
        re.compile(r"token['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9]{20,}['\"]?", re.IGNORECASE),
    ]

    def filter(self, record):
        msg = record.getMessage()
        for p in self._patterns:
            msg = p.sub("[REDACTED]", msg)
        record.msg = msg
        record.args = ()
        return True


def setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    handler.addFilter(CredentialRedactionFilter())

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
