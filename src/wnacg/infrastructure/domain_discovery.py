"""Official WNACG mirror discovery with bounded network and parsing rules."""

import re
from collections.abc import Iterable
from typing import cast
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from wnacg.infrastructure.config import AppConfig, ProxyMode, cfg
from wnacg.infrastructure.crawler import WnacgCrawler
from wnacg.infrastructure.http_streams import close_stream_response
from wnacg.infrastructure.logger import logger
from wnacg.infrastructure.network_safety import (
    ensure_expected_content_type,
    ensure_public_https_url_sync,
    ensure_public_peer_address,
    read_limited_chunks,
)

DISCOVERY_URLS = (
    "https://www.wnlink.ru/",
    "https://wnacg01.link/",
    "https://wnacg02.link/",
)
_DISCOVERY_HOSTS = frozenset(
    {
        "wnlink.ru",
        "www.wnlink.ru",
        "wn01.link",
        "www.wn01.link",
        "wnacg01.link",
        "www.wnacg01.link",
        "wnacg02.link",
        "www.wnacg02.link",
    }
)
_MIRROR_DOMAIN_PATTERN = re.compile(
    r"(?<![a-z0-9.-])(?:www\.)?(?:wnacg[a-z0-9-]*|wn\d+)\.[a-z]{2,63}(?![a-z0-9.-])",
    re.IGNORECASE,
)


class DomainDiscoveryError(RuntimeError):
    """Raised when no official publication page yields a usable mirror."""


def _candidate_domains(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []

    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if isinstance(href, str):
            hostname = urlsplit(href).hostname
            if hostname is not None:
                candidates.append(hostname)
        candidates.extend(_MIRROR_DOMAIN_PATTERN.findall(anchor.get_text(" ", strip=True)))

    candidates.extend(_MIRROR_DOMAIN_PATTERN.findall(soup.get_text(" ", strip=True)))
    return candidates


def extract_official_domains(html: str) -> list[str]:
    """Extract syntactically valid mirror hosts without relying on page layout."""
    domains: list[str] = []
    for candidate in _candidate_domains(html):
        normalized = candidate.lower().rstrip(".")
        if normalized in _DISCOVERY_HOSTS or _MIRROR_DOMAIN_PATTERN.fullmatch(normalized) is None:
            continue
        try:
            domain = AppConfig.validate_domain(normalized)
        except ValueError:
            continue
        if domain not in domains:
            domains.append(domain)
    return domains


def discover_official_domains() -> list[str]:
    """Fetch official publication pages in order and return the first valid mirror list."""
    allowed_source_hosts = set(_DISCOVERY_HOSTS)
    with WnacgCrawler.get_sync_client() as client:
        for discovery_url in DISCOVERY_URLS:
            try:
                request_url = ensure_public_https_url_sync(discovery_url, allowed_hosts=allowed_source_hosts)
                response = client.get(request_url, timeout=15.0, stream=True)
                try:
                    if cfg.proxy_mode is ProxyMode.DIRECT:
                        ensure_public_peer_address(response.primary_ip)
                    response.raise_for_status()
                    ensure_public_https_url_sync(str(response.url), allowed_hosts=allowed_source_hosts)
                    ensure_expected_content_type(response.headers, {"text/html", "application/xhtml+xml"})
                    chunks = cast(
                        Iterable[bytes],
                        response.iter_content(chunk_size=64 * 1024),  # pyright: ignore[reportUnknownMemberType]
                    )
                    html = read_limited_chunks(chunks, cfg.max_html_bytes).decode(
                        response.encoding or "utf-8",
                        errors="replace",
                    )
                finally:
                    close_stream_response(response)
                domains = extract_official_domains(html)
                if domains:
                    return domains
                logger.warning("Official domain page contained no recognized mirrors", url=discovery_url)
            except Exception as error:
                logger.warning("Official domain page failed", url=discovery_url, error=str(error))

    raise DomainDiscoveryError("官方发布页暂时不可用，或页面中没有可识别的漫画域名；当前列表未更改")
