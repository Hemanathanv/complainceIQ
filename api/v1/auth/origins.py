"""Origin matching for browser requests made to this API's own host."""

from urllib.parse import urlsplit

from fastapi import Request


def is_same_origin(request: Request, origin: str) -> bool:
    parsed = urlsplit(origin)
    return parsed.scheme == request.url.scheme and parsed.netloc == request.headers.get("host")
