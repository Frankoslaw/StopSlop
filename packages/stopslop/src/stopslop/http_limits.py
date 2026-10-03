"""Bound request parsing before allocating or decoding an entire body."""
import json


def decode_body(chunks, limit):
    from .policy import PolicyError
    body = bytearray()
    for chunk in chunks:
        if len(body) + len(chunk) > limit:
            raise PolicyError("payload_too_large", 413)
        body.extend(chunk)
    try:
        return json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        raise PolicyError("invalid_json", 400) from None


async def request_payload(request, limit):
    from .policy import PolicyError
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise PolicyError("payload_too_large", 413)
        body.extend(chunk)
    return decode_body([body], limit)
