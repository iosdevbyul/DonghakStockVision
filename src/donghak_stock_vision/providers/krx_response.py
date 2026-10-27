"""Bounded content decoding at the parsing boundary, never rewriting raw evidence."""

import gzip
import io
import zlib

from donghak_stock_vision.providers.base import ProviderError


def decode_response(body: bytes, encoding: str, representation: str, maximum: int) -> bytes:
    if type(maximum) is not int or not 1 <= maximum <= 2 * 1024 * 1024:
        raise ProviderError("invalid_krx_decode_budget")
    if type(body) is not bytes or len(body) > maximum:
        raise ProviderError("krx_encoded_body_budget")
    if representation not in ("wire", "httpx_decoded"):
        raise ProviderError("unknown_krx_body_representation")
    coding = encoding.strip().lower()
    if coding not in ("", "identity", "gzip"):
        raise ProviderError("unsupported_krx_content_encoding")
    if coding != "gzip" or representation == "httpx_decoded":
        return body
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
            decoded = stream.read(maximum + 1)
    except (OSError, EOFError, zlib.error):
        raise ProviderError("invalid_or_truncated_krx_gzip") from None
    if len(decoded) > maximum:
        raise ProviderError("krx_decoded_body_budget")
    return decoded
