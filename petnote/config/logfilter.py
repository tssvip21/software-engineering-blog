import logging
import re

class RedactQuery(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        message = re.sub(r"([?&](?:code|state|token|id_token|access_token)=)[^&\s\"]+", r"\1[redacted]", message, flags=re.I)
        record.msg, record.args = message, ()
        return True
