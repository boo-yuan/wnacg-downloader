"""TLS-verified WNACG HTTP adapter and HTML parsers."""

import asyncio
import re
import time
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession, Response, Session

from wnacg.domain.models import Comic
from wnacg.infrastructure.config import ProxyMode, cfg
from wnacg.infrastructure.logger import logger


class CrawlError(RuntimeError):
    """Raised when a complete, trustworthy crawl result cannot be produced."""


class WnacgCrawler:
    """Fetch and parse gallery metadata without retaining mutable mirror state."""

    @staticmethod
    def _session_options() -> dict[str, Any]:
        options: dict[str, Any] = {"impersonate": "chrome", "timeout": 15.0}
        match cfg.proxy_mode:
            case ProxyMode.CUSTOM:
                options["proxies"] = cfg.curl_cffi_proxies
            case ProxyMode.DIRECT:
                options["trust_env"] = False
            case ProxyMode.SYSTEM:
                options["trust_env"] = True
        return options

    @classmethod
    def get_sync_client(cls) -> Session[Response]:
        return Session[Response](**cls._session_options())

    @classmethod
    def get_client(cls) -> AsyncSession[Response]:
        return AsyncSession[Response](**cls._session_options())

    @staticmethod
    def _mirrors() -> list[str]:
        return list(dict.fromkeys([cfg.domain, *cfg.backup_domains]))

    @classmethod
    def fetch_sync(
        cls,
        client: Session[Response],
        path: str,
        **kwargs: Any,
    ) -> tuple[Response, str]:
        failures: list[str] = []
        for attempt, domain in enumerate(cls._mirrors()):
            base_url = f"https://{domain}"
            try:
                response = client.get(f"{base_url}{path}", **kwargs)
                response.raise_for_status()
                return response, base_url
            except Exception as error:
                failures.append(f"{domain}: {error}")
                logger.warning("Mirror request failed", domain=domain, path=path, error=str(error))
                if attempt + 1 < len(cls._mirrors()):
                    time.sleep(min(2**attempt, 4))
        raise CrawlError(f"All mirrors failed for {path}: {'; '.join(failures)}")

    @classmethod
    async def fetch(
        cls,
        client: AsyncSession[Response],
        path: str,
        **kwargs: Any,
    ) -> tuple[Response, str]:
        failures: list[str] = []
        mirrors = cls._mirrors()
        for attempt, domain in enumerate(mirrors):
            base_url = f"https://{domain}"
            try:
                response = await client.get(f"{base_url}{path}", **kwargs)
                response.raise_for_status()
                return response, base_url
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
            response, _base_url = cls.fetch_sync(client, path)
        return cls._parse_search_html(response.text)

    @classmethod
    async def search(cls, keyword: str, page: int = 1) -> tuple[list[Comic], int]:
        direct_aid = cls.gallery_aid(keyword)
        if direct_aid is not None:
            return [await asyncio.to_thread(cls.get_comic_sync, direct_aid)], 1
        encoded_keyword = urllib.parse.quote(keyword)
        path = f"/search/index.php?q={encoded_keyword}&m=&syn=yes&f=_all&s=create_time_DESC&p={page}"
        async with cls.get_client() as client:
            response, _base_url = await cls.fetch(client, path)
        return await asyncio.to_thread(cls._parse_search_html, response.text)

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
            response, base_url = cls.fetch_sync(client, f"/photos-index-page-1-aid-{aid}.html")
        soup = BeautifulSoup(response.text, "html.parser")
        heading = soup.select_one("h2") or soup.select_one("title")
        title = heading.get_text(" ", strip=True) if heading else f"Gallery {aid}"
        title = re.sub(r"\s*[-|_]\s*紳士漫畫.*$", "", title).strip() or f"Gallery {aid}"
        count_match = re.search(r"(?:共|總共|总共)?\s*(\d+)\s*[张張Pp]", soup.get_text(" ", strip=True))
        cover = soup.select_one(".pic_box img")
        cover_url = urllib.parse.urljoin(base_url, str(cover.get("src", ""))) if cover else ""
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
    async def get_all_raw_urls(cls, aid: str) -> list[str]:
        async with cls.get_client() as client:
            response, base_url = await cls.fetch(client, f"/photos-gallery-aid-{aid}.html")
        return await asyncio.to_thread(cls._parse_gallery_urls, response.text, base_url)

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
    async def get_image_view_links(cls, aid: str) -> list[str]:
        async with cls.get_client() as client:
            first_response, base_url = await cls.fetch(client, f"/photos-index-page-1-aid-{aid}.html")
            first_links, max_page = await asyncio.to_thread(cls._parse_view_page, first_response.text, base_url)

            async def fetch_page(page_number: int) -> tuple[int, list[str]]:
                response, _ = await cls.fetch(
                    client,
                    f"/photos-index-page-{page_number}-aid-{aid}.html",
                    timeout=15.0,
                )
                links, _ignored = await asyncio.to_thread(cls._parse_view_page, response.text, base_url)
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
        path = parsed.path if parsed.path.startswith("/") else f"/{parsed.path}"

        async def fetch_url(active_client: AsyncSession[Response]) -> str:
            response, _ = await cls.fetch(active_client, path, allow_redirects=True, timeout=15.0)
            return await asyncio.to_thread(cls._parse_raw_url, response.text)

        if client is not None:
            return await fetch_url(client)
        async with cls.get_client() as active_client:
            return await fetch_url(active_client)
