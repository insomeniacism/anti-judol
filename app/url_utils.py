"""Strict URL validation and competition-compliant deduplication normalizer."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_URL_LENGTH = 300

# Valid hostname label: alphanumeric or hyphen, cannot start/end with hyphen
_HOSTNAME_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
# TLD must be alphabetic (at least 2 chars) or punycode xn--...
_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})$")


class InvalidURLError(ValueError):
    """Raised when a URL violates competition validation rules."""


@dataclass(frozen=True, slots=True)
class NormalizedURL:
    """Validated and normalized URL pair."""

    original_url: str
    normalized_url: str
    domain: str


def _is_ip_address(host: str) -> bool:
    """Return True if host is an IPv4 or IPv6 literal."""
    cleaned = host.strip("[]")
    try:
        ipaddress.ip_address(cleaned)
        return True
    except ValueError:
        return False


def _validate_hostname(host: str) -> None:
    """Validate that host is a proper public domain name (not localhost or IP)."""
    if not host:
        raise InvalidURLError("Empty hostname")

    host_lower = host.lower().rstrip(".")
    if not host_lower:
        raise InvalidURLError("Empty hostname")

    if host_lower == "localhost" or host_lower.endswith(".localhost"):
        raise InvalidURLError(f"Localhost is not allowed: {host}")

    if _is_ip_address(host_lower):
        raise InvalidURLError(f"IP addresses are not allowed: {host}")

    if "." not in host_lower:
        raise InvalidURLError(f"Hostname must have a TLD: {host}")

    if len(host_lower) > 253:
        raise InvalidURLError(f"Hostname too long: {host}")

    labels = host_lower.split(".")
    for label in labels:
        if not _HOSTNAME_LABEL_RE.match(label):
            raise InvalidURLError(f"Invalid hostname label '{label}' in {host}")

    if not _TLD_RE.match(labels[-1]):
        raise InvalidURLError(f"Invalid TLD '{labels[-1]}' in {host}")


def validate_and_normalize(raw_url: str) -> NormalizedURL:
    """Validate a URL against competition rules and produce its canonical key.

    Competition Validation Rules:
    - Only `http` or `https` scheme.
    - No port specified.
    - No username or password.
    - Not an IP address and not `localhost`.
    - Maximum length of 300 characters.

    Competition Anti-Duplicate Normalization Rules:
    - Strip `http://` or `https://`.
    - Strip leading `www.` on the domain.
    - Lowercase the domain name (distinct subdomains remain distinct).
    - Strip query string (`?...`) and fragment (`#...`).
    - Strip trailing slash(es) (`/`).
    - Path is preserved and IS case-sensitive.
    """
    if not isinstance(raw_url, str):
        raise InvalidURLError("URL must be a string")

    url = raw_url.strip()
    if not url:
        raise InvalidURLError("URL cannot be empty")

    if len(url) > MAX_URL_LENGTH:
        raise InvalidURLError(f"URL exceeds {MAX_URL_LENGTH} characters ({len(url)})")

    if any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise InvalidURLError("URL contains whitespace or control characters")

    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise InvalidURLError(f"Unsupported scheme '{parsed.scheme}'; only http/https allowed")

    if not parsed.netloc:
        raise InvalidURLError("Missing network location (domain)")

    # Reject username/password (userinfo)
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise InvalidURLError("Username/password in URL is not allowed")

    # Reject IPv6 literals and any explicit port (e.g. :8080, :443, or trailing :)
    if "[" in parsed.netloc or "]" in parsed.netloc:
        raise InvalidURLError("IPv6 addresses are not allowed")

    if ":" in parsed.netloc:
        raise InvalidURLError("Ports in URL are not allowed")

    hostname = (parsed.hostname or "").lower().rstrip(".")
    _validate_hostname(hostname)

    # Strip leading 'www.' only when there is a domain + TLD remaining (e.g. www.toko.id -> toko.id)
    domain = hostname[4:] if hostname.startswith("www.") and hostname.count(".") >= 2 else hostname

    # Preserve path case-sensitively, strip trailing '/'
    path = parsed.path.rstrip("/")

    normalized = f"{domain}{path}"

    return NormalizedURL(
        original_url=url,
        normalized_url=normalized,
        domain=domain,
    )


def domain_to_url(domain_or_url: str, default_scheme: str = "https") -> str:
    """Ensure a raw domain or URL string has an http/https scheme."""
    value = domain_or_url.strip()
    if value.lower().startswith(("http://", "https://")):
        return value
    # Strip wildcard prefix from crt.sh (e.g. *.slotgacor.com -> slotgacor.com)
    if value.startswith("*."):
        value = value[2:]
    return f"{default_scheme}://{value}"
