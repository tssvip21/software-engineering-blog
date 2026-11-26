import logging
import re

PRIVATE_QUERY_VALUE = re.compile(
    r'([?&](?:code|state|token|id_token|access_token|refresh_token|client_secret|code_verifier)=)[^&\s"]+',
    re.IGNORECASE,
)


class RedactQuery(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        message = PRIVATE_QUERY_VALUE.sub(r"\1[redacted]", message)
        record.msg, record.args = message, ()
        return True
