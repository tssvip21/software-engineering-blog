"""Decode untrusted JSON with portable text and a bounded nesting depth."""
import json
import math


class InvalidJSON(ValueError):
    """The request cannot be represented safely by the supported databases."""


def _reject_constant(value):
    raise InvalidJSON("Non-finite JSON number")


def load_json(value):
    try:
        result = json.loads(value, parse_constant=_reject_constant)
        pending = [(result, 0)]
        while pending:
            item, depth = pending.pop()
            if depth > 32:
                raise InvalidJSON("JSON nesting is too deep")
            if isinstance(item, dict):
                pending.extend((key, depth + 1) for key in item)
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
            elif isinstance(item, str):
                item.encode("utf-8")
                if "\0" in item:
                    raise InvalidJSON("Null characters are not supported")
            elif isinstance(item, float) and not math.isfinite(item):
                raise InvalidJSON("Non-finite JSON number")
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidJSON("Invalid JSON input") from exc
