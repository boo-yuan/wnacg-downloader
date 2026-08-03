"""TLS-verified WNACG HTTP adapter and HTML parsers."""

import asyncio
import contextlib
import re
import time
import urllib.parse
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterable
from contextlib import AbstractAsyncContextManager
from typing import TypedDict, Unpack, cast

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession, Response, Session

from wnacg.domain.models import Comic
from wnacg.infrastructure.config import ProxyMode, cfg
from wnacg.infrastructure.logger import logger
from wnacg.infrastructure.network_safety import (
    ensure_expected_content_type,
    ensure_public_https_url,
    ensure_public_https_url_sync,
    read_limited_async_chunks,
    read_limited_chunks,
    validate_public_https_url,
)

type ConnectionSlot = Callable[[], AbstractAsyncContextManager[None]]

_HTML_CONTENT_TYPES = {"text/html", "application/xhtml+xml"}


class FetchOptions(TypedDict, total=False):
    """Typed options accepted by bounded HTML fetches."""

    timeout: float
    allow_redirects: bool


class CrawlError(RuntimeError):
    """Raised when a complete, trustworthy crawl result cannot be produced."""


class WnacgCrawler:
    """Fetch and parse gallery metadata without retaining mutable mirror state."""

    @classmethod
    def get_sync_client(cls) -> Session[Response]:
        match cfg.proxy_mode:
            case ProxyMode.CUSTOM:
                return Session[Response](impersonate="chrome", timeout=15.0, proxy=cfg.custom_proxy)
            case ProxyMode.DIRECT:
                return Session[Response](impersonate="chrome", timeout=15.0, trust_env=False)
            case ProxyMode.SYSTEM:
                return Session[Response](impersonate="chrome", timeout=15.0, trust_env=True)

    @classmethod
    def get_client(cls) -> AsyncSession[Response]:
        match cfg.proxy_mode:
            case ProxyMode.CUSTOM:
                return AsyncSession[Response](impersonate="chrome", timeout=15.0, proxy=cfg.custom_proxy)
            case ProxyMode.DIRECT:
                return AsyncSession[Response](impersonate="chrome", timeout=15.0, trust_env=False)
            case ProxyMode.SYSTEM:
                return AsyncSession[Response](impersonate="chrome", timeout=15.0, trust_env=True)

    @staticmethod
    def _mirrors() -> list[str]:
        return list(dict.fromkeys([cfg.domain, *cfg.backup_domains]))

    @classmethod
    def fetch_text_sync(
        cls,
        client: Session[Response],
        path: str,
        **kwargs: Unpack[FetchOptions],
    ) -> tuple[str, str]:
        failures: list[str] = []
        for attempt, domain in enumerate(cls._mirrors()):
            base_url = f"https://{domain}"
            try:
                request_url = ensure_public_https_url_sync(
                    f"{base_url}{path}",
                    allowed_hosts=set(cls._mirrors()),
                )
                response = client.get(request_url, stream=True, **kwargs)
                response.raise_for_status()
                ensure_public_https_url_sync(str(response.url), allowed_hosts=set(cls._mirrors()))
                ensure_expected_content_type(response.headers, _HTML_CONTENT_TYPES)
                content_length = int(response.headers.get("content-length", "0") or 0)
                if content_length > cfg.max_html_bytes:
                    raise ValueError(f"HTML response exceeds {cfg.max_html_bytes} bytes")
                chunks = cast(
                    Iterable[bytes],
                    response.iter_content(chunk_size=64 * 1024),  # pyright: ignore[reportUnknownMemberType]
                )
                content = read_limited_chunks(chunks, cfg.max_html_bytes)
                return content.decode(response.encoding or "utf-8", errors="replace"), base_url
            except Exception as error:
                failures.append(f"{domain}: {error}")
                logger.warning("Mirror request failed", domain=domain, path=path, error=str(error))
                if attempt + 1 < len(cls._mirrors()):
                    time.sleep(min(2**attempt, 4))
        raise CrawlError(f"All mirrors failed for {path}: {'; '.join(failures)}")

    @classmethod
    async def fetch_text(
        cls,
        client: AsyncSession[Response],
        path: str,
        **kwargs: Unpack[FetchOptions],
    ) -> tuple[str, str]:
        failures: list[str] = []
        mirrors = cls._mirrors()
        for attempt, domain in enumerate(mirrors):
            base_url = f"https://{domain}"
            try:
                request_url = await ensure_public_https_url(
                    f"{base_url}{path}",
                    allowed_hosts=set(mirrors),
                )
                response = await client.get(request_url, stream=True, **kwargs)
                response.raise_for_status()
                await ensure_public_https_url(str(response.url), allowed_hosts=set(mirrors))
                ensure_expected_content_type(response.headers, _HTML_CONTENT_TYPES)
                content_length = int(response.headers.get("content-length", "0") or 0)
                if content_length > cfg.max_html_bytes:
                    raise ValueError(f"HTML response exceeds {cfg.max_html_bytes} bytes")
                chunks = cast(
                    AsyncIterator[bytes],
                    response.aiter_content(chunk_size=64 * 1024),  # pyright: ignore[reportUnknownMemberType]
                )
                content = await read_limited_async_chunks(chunks, cfg.max_html_bytes)
                return content.decode(response.encoding or "utf-8", errors="replace"), base_url
            except Exception as error:
                failures.append(f"{domain}: {error}")
                logger.warning("Mirror request failed", domain=domain, path=path, error=str(error))
                if attempt + 1 < len(mirrors):
                    await asyncio.sleep(min(2**attempt, 4))
        raise CrawlError(f"All mirrors failed for {path}: {'; '.join(failures)}")

    @staticmethod
    def _parse_search_html(html: str) -> tuple[list[Comic], int]:
        soup = BeautifulSoup(html, "html.parser")
        results: list[Comic] = []
        for item in soup.select(".gallary_item"):
            title_element = item.select_one(".title a")
            if title_element is None:
                continue
            link = str(title_element.get("href", ""))
            aid_match = re.search(r"aid-([A-Za-z0-9_-]+)", link)
            if aid_match is None:
                continue
            image_element = item.select_one("img")
            cover_url = str(image_element.get("src", "")) if image_element else ""
            if cover_url.startswith("//"):
                cover_url = f"https:{cover_url}"
            if cover_url:
                with contextlib.suppress(ValueError):
                    cover_url = validate_public_https_url(cover_url)
                if not cover_url.startswith("https://"):
                    cover_url = ""
            info = item.select_one(".info_col")
            info_text = info.get_text(" ", strip=True) if info else ""
            picture_match = re.search(r"(\d+)\s*[张張Pp]", info_text)
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", info_text)
            results.append(
                Comic(
                    aid=aid_match.group(1),
                    title=title_element.get_text(strip=True),
                    cover_url=cover_url,
                    url=link,
                    pic_count=f"{picture_match.group(1)}图" if picture_match else "",
                    date=date_match.group(1) if date_match else "",
                )
            )

        max_page = 1
        paginator = soup.select_one(".f_left.paginator")
        if paginator:
            for element in paginator.find_all(["a", "span"]):
                text = element.get_text(strip=True)
                if text.isdigit():
                    max_page = max(max_page, int(text))
        return results, max_page

    @classmethod
    def search_sync(cls, keyword: str, page: int = 1) -> tuple[list[Comic], int]:
        direct_aid = cls.gallery_aid(keyword)
        if direct_aid is not None:
            return [cls.get_comic_sync(direct_aid)], 1
        encoded_keyword = urllib.parse.quote(keyword)
        path = f"/search/index.php?q={encoded_keyword}&m=&syn=yes&f=_all&s=create_time_DESC&p={page}"
        with cls.get_sync_client() as client:
            html, _base_url = cls.fetch_text_sync(client, path)
        return cls._parse_search_html(html)

    @classmethod
    async def search(cls, keyword: str, page: int = 1) -> tuple[list[Comic], int]:
        direct_aid = cls.gallery_aid(keyword)
        if direct_aid is not None:
            return [await asyncio.to_thread(cls.get_comic_sync, direct_aid)], 1
        encoded_keyword = urllib.parse.quote(keyword)
        path = f"/search/index.php?q={encoded_keyword}&m=&syn=yes&f=_all&s=create_time_DESC&p={page}"
        async with cls.get_client() as client:
            html, _base_url = await cls.fetch_text(client, path)
        return await asyncio.to_thread(cls._parse_search_html, html)

    @staticmethod
    def gallery_aid(value: str) -> str | None:
        """Extract a gallery identifier from a direct gallery URL or plain identifier."""
        stripped = value.strip()
        explicit = re.fullmatch(r"aid:\s*([A-Za-z0-9_-]+)", stripped, re.IGNORECASE)
        if explicit:
            return explicit.group(1)
        parsed = urllib.parse.urlsplit(stripped)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        match = re.search(r"(?:aid-|[?&]aid=)([A-Za-z0-9_-]+)", stripped)
        return match.group(1) if match else None

    @classmethod
    def get_comic_sync(cls, aid: str) -> Comic:
        """Resolve enough metadata to enqueue a directly supplied gallery URL."""
        with cls.get_sync_client() as client:
            html, base_url = cls.fetch_text_sync(client, f"/photos-index-page-1-aid-{aid}.html")
        soup = BeautifulSoup(html, "html.parser")
        heading = soup.select_one("h2") or soup.select_one("title")
        title = heading.get_text(" ", strip=True) if heading else f"Gallery {aid}"
        title = re.sub(r"\s*[-|_]\s*紳士漫畫.*$", "", title).strip() or f"Gallery {aid}"
        count_match = re.search(r"(?:共|總共|总共)?\s*(\d+)\s*[张張Pp]", soup.get_text(" ", strip=True))
        cover = soup.select_one(".pic_box img")
        cover_url = urllib.parse.urljoin(base_url, str(cover.get("src", ""))) if cover else ""
        if cover_url:
            cover_url = validate_public_https_url(cover_url)
        return Comic(
            aid=aid,
            title=title,
            cover_url=cover_url,
            url=f"{base_url}/photos-index-aid-{aid}.html",
            pic_count=f"{count_match.group(1)}图" if count_match else "",
        )

    @staticmethod
    def expected_count(pic_count: str) -> int | None:
        match = re.search(r"\d+", pic_count)
        return int(match.group()) if match else None

    @staticmethod
    def _parse_gallery_urls(html: str, base_url: str) -> list[str]:
        match = re.search(r"var\s+imglist\s*=\s*(\[.*?\]);", html, re.DOTALL)
        if match is None:
            return []
        urls = re.findall(r"url\s*:\s*(?:fast_img_host\+)?\\*['\"](.*?)\\*['\"]", match.group(1))
        return [f"https:{url}" if url.startswith("//") else urllib.parse.urljoin(base_url, url) for url in urls]

    @classmethod
    async def get_all_raw_urls(cls, aid: str, connection_slot: ConnectionSlot | None = None) -> list[str]:
        async with cls.get_client() as client:
            if connection_slot is None:
                html, base_url = await cls.fetch_text(client, f"/photos-gallery-aid-{aid}.html")
            else:
                async with connection_slot():
                    html, base_url = await cls.fetch_text(client, f"/photos-gallery-aid-{aid}.html")
        urls = await asyncio.to_thread(cls._parse_gallery_urls, html, base_url)
        return [validate_public_https_url(url) for url in urls]

    @staticmethod
    def _parse_view_page(html: str, base_url: str) -> tuple[list[str], int]:
        soup = BeautifulSoup(html, "html.parser")
        links = [
            urllib.parse.urljoin(base_url, str(anchor.get("href")))
            for anchor in soup.select(".pic_box a")
            if anchor.get("href")
        ]
        max_page = 1
        paginator = soup.select_one(".f_left.paginator")
        if paginator:
            for element in paginator.find_all(["a", "span"]):
                text = element.get_text(strip=True)
                if text.isdigit():
                    max_page = max(max_page, int(text))
        return links, max_page

    @classmethod
    async def get_image_view_links(cls, aid: str, connection_slot: ConnectionSlot | None = None) -> list[str]:
        local_semaphore = asyncio.Semaphore(min(cfg.global_max_connections, 8))

        @contextlib.asynccontextmanager
        async def local_slot() -> AsyncGenerator[None]:
            async with local_semaphore:
                yield

        slot = connection_slot or local_slot
        async with cls.get_client() as client:
            async with slot():
                first_html, base_url = await cls.fetch_text(client, f"/photos-index-page-1-aid-{aid}.html")
            first_links, max_page = await asyncio.to_thread(cls._parse_view_page, first_html, base_url)
            if max_page > cfg.max_gallery_images:
                raise CrawlError(f"Gallery page count exceeds configured limit: {max_page}")

            async def fetch_page(page_number: int) -> tuple[int, list[str]]:
                async with slot():
                    html, _ = await cls.fetch_text(
                        client,
                        f"/photos-index-page-{page_number}-aid-{aid}.html",
                        timeout=15.0,
                    )
                links, _ignored = await asyncio.to_thread(cls._parse_view_page, html, base_url)
                if not links:
                    raise CrawlError(f"Gallery {aid} page {page_number} contained no image links")
                return page_number, links

            page_results: list[tuple[int, list[str]]] = []
            async with asyncio.TaskGroup() as task_group:
                tasks = [task_group.create_task(fetch_page(page)) for page in range(2, max_page + 1)]
            page_results.extend(task.result() for task in tasks)

        ordered_links = list(first_links)
        for _page, links in sorted(page_results):
            ordered_links.extend(links)
        deduplicated = list(dict.fromkeys(ordered_links))
        if not deduplicated:
            raise CrawlError(f"Gallery {aid} contained no image links")
        if len(deduplicated) > cfg.max_gallery_images:
            raise CrawlError(f"Gallery image count exceeds configured limit: {len(deduplicated)}")
        return deduplicated

    @staticmethod
    def _parse_raw_url(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        image = soup.select_one("#picarea")
        if image is None:
            return ""
        source = str(image.get("src", ""))
        return f"https:{source}" if source.startswith("//") else source

    @classmethod
    async def get_raw_image_url(
        cls,
        view_url: str,
        client: AsyncSession[Response] | None = None,
    ) -> str:
        parsed = urllib.parse.urlparse(view_url)
        validate_public_https_url(view_url, allowed_hosts=set(cls._mirrors()))
        path = parsed.path if parsed.path.startswith("/") else f"/{parsed.path}"

        async def fetch_url(active_client: AsyncSession[Response]) -> str:
            html, _ = await cls.fetch_text(active_client, path, allow_redirects=True, timeout=15.0)
            raw_url = await asyncio.to_thread(cls._parse_raw_url, html)
            return validate_public_https_url(raw_url) if raw_url else ""

        if client is not None:
            return await fetch_url(client)
        async with cls.get_client() as active_client:
            return await fetch_url(active_client)
