from curl_cffi.requests import AsyncSession, Session
from bs4 import BeautifulSoup
from typing import List, Tuple
from core.models import Comic
from core.config import cfg
from core.logger import logger
import urllib.parse
import re

class WnacgCrawler:
    
    @staticmethod
    def get_sync_client() -> Session:
        kwargs = {
            "impersonate": "chrome",
            "verify": False,
            "timeout": 15.0,
        }
        if cfg.proxy_mode == "custom":
            kwargs["proxies"] = cfg.curl_cffi_proxies
        elif cfg.proxy_mode == "direct":
            kwargs["trust_env"] = False
        else: # system
            kwargs["trust_env"] = True
            
        return Session(**kwargs)
        
    @staticmethod
    def get_client() -> AsyncSession:
        kwargs = {
            "impersonate": "chrome",
            "verify": False,
            "timeout": 15.0,
        }
        
        if cfg.proxy_mode == "custom":
            kwargs["proxies"] = cfg.curl_cffi_proxies
        elif cfg.proxy_mode == "direct":
            kwargs["trust_env"] = False
        else: # system
            kwargs["trust_env"] = True
            
        return AsyncSession(**kwargs)
        
    _active_domain = None
    _mirrors = ["www.wnacg.ru", "www.wnacg.org", "www.wnacg.net", "www.wnacg.com"]

    @classmethod
    def get_base_url(cls) -> str:
        if cls._active_domain:
            return f"https://{cls._active_domain}"
        return f"https://{cfg.domain}"
        
    @classmethod
    def switch_domain(cls):
        if not cls._active_domain:
            cls._active_domain = cfg.domain
        if cls._active_domain in cls._mirrors:
            idx = cls._mirrors.index(cls._active_domain)
            cls._active_domain = cls._mirrors[(idx + 1) % len(cls._mirrors)]
        else:
            cls._active_domain = cls._mirrors[0]
        logger.warning(f"Switched active domain to {cls._active_domain}")

    @classmethod
    def fetch_sync(cls, client, path: str, **kwargs):
        for attempt in range(4):
            base_url = cls.get_base_url()
            url = f"{base_url}{path}"
            try:
                resp = client.get(url, **kwargs)
                resp.raise_for_status()
                return resp, base_url
            except Exception as e:
                logger.warning(f"Fetch failed on {base_url}: {e}")
                cls.switch_domain()
        raise Exception("All mirror domains failed")

    @classmethod
    async def fetch(cls, client, path: str, **kwargs):
        for attempt in range(4):
            base_url = cls.get_base_url()
            url = f"{base_url}{path}"
            try:
                resp = await client.get(url, **kwargs)
                resp.raise_for_status()
                return resp, base_url
            except Exception as e:
                logger.warning(f"Fetch failed on {base_url}: {e}")
                cls.switch_domain()
        raise Exception("All mirror domains failed")

    @classmethod
    def search_sync(cls, keyword: str, page: int = 1) -> Tuple[List[Comic], int]:
        """
        同步版本的搜索逻辑，用于后台独立线程，避免多个 asyncio 事件循环导致 curl_cffi 崩溃
        """
        encoded_kw = urllib.parse.quote(keyword)
        
        results = []
        with cls.get_sync_client() as client:
            resp, base_url = cls.fetch_sync(client, f"/search/index.php?q={encoded_kw}&m=&syn=yes&f=_all&s=create_time_DESC&p={page}")
            soup = BeautifulSoup(resp.text, 'html.parser')
            items = soup.select('.gallary_item')
            for item in items:
                title_elem = item.select_one('.title a')
                if not title_elem:
                    continue
                title = title_elem.text.strip()
                link = title_elem.get('href', '')
                
                img_elem = item.select_one('img')
                cover_url = img_elem.get('src', '') if img_elem else ''
                if cover_url.startswith('//'):
                    cover_url = 'https:' + cover_url
                    
                aid = ""
                if "aid-" in link:
                    aid = link.split("aid-")[1].split(".html")[0]
                    
                pic_count = ""
                date = ""
                info_col = item.select_one('.info_col')
                if info_col:
                    text = info_col.text.strip()
                    m_pic = re.search(r'(\d+)', text.split('\n')[0] if '\n' in text else text)
                    if m_pic:
                        pic_count = f"{m_pic.group(1)}图"
                    m_date = re.search(r'(\d{4}-\d{2}-\d{2})', text)
                    if m_date:
                        date = m_date.group(1)
                    
                results.append(Comic(
                    aid=aid,
                    title=title,
                    cover_url=cover_url,
                    url=link,
                    pic_count=pic_count,
                    date=date
                ))
                
            max_page = 1
            paginator = soup.select_one('.f_left.paginator')
            if paginator:
                for el in paginator.find_all(['a', 'span']):
                    text = el.text.strip()
                    if text.isdigit():
                        max_page = max(max_page, int(text))
                        
            return results, max_page

    @classmethod
    async def search(cls, keyword: str, page: int = 1) -> Tuple[List[Comic], int]:
        """
        根据关键字和页码搜索漫画，返回 (结果列表, 当前请求页数（或固定1作为占位）)
        """
        encoded_kw = urllib.parse.quote(keyword)
        
        results = []
        async with cls.get_client() as client:
            resp, base_url = await cls.fetch(client, f"/search/index.php?q={encoded_kw}&m=&syn=yes&f=_all&s=create_time_DESC&p={page}")
            soup = BeautifulSoup(resp.text, 'html.parser')
            items = soup.select('.gallary_item')
            for item in items:
                title_elem = item.select_one('.title a')
                if not title_elem:
                    continue
                title = title_elem.text.strip()
                link = title_elem.get('href', '')
                
                img_elem = item.select_one('img')
                cover_url = img_elem.get('src', '') if img_elem else ''
                if cover_url.startswith('//'):
                    cover_url = 'https:' + cover_url
                    
                aid = ""
                if "aid-" in link:
                    aid = link.split("aid-")[1].split(".html")[0]
                    
                pic_count = ""
                date = ""
                info_col = item.select_one('.info_col')
                if info_col:
                    text = info_col.text.strip()
                    m_pic = re.search(r'(\d+)', text.split('\n')[0] if '\n' in text else text)
                    if m_pic:
                        pic_count = f"{m_pic.group(1)}图"
                    m_date = re.search(r'(\d{4}-\d{2}-\d{2})', text)
                    if m_date:
                        date = m_date.group(1)
                    
                results.append(Comic(
                    aid=aid,
                    title=title,
                    cover_url=cover_url,
                    url=link,
                    pic_count=pic_count,
                    date=date
                ))
                
            max_page = 1
            paginator = soup.select_one('.f_left.paginator')
            if paginator:
                for el in paginator.find_all(['a', 'span']):
                    text = el.text.strip()
                    if text.isdigit():
                        max_page = max(max_page, int(text))
                        
            return results, max_page

    @classmethod
    async def get_image_view_links(cls, aid: str) -> List[str]:
        """
        获取文章页中所有的浏览页(photos-view-id)链接。自动解析分页并抓取所有缩略图链接。
        """
        import asyncio
        view_links = []
        
        async with cls.get_client() as client:
            try:
                resp, base_url = await cls.fetch(client, f"/photos-index-page-1-aid-{aid}.html")
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                # 解析第一页
                pics = soup.select('.pic_box a')
                for pic in pics:
                    href = pic.get('href')
                    if href:
                        if href.startswith('/'):
                            view_links.append(f"{base_url}{href}")
                        else:
                            view_links.append(href)
                            
                # 寻找最大页码
                max_page = 1
                paginator = soup.select_one('.f_left.paginator')
                if paginator:
                    for el in paginator.find_all(['a', 'span']):
                        text = el.text.strip()
                        if text.isdigit():
                            max_page = max(max_page, int(text))
                            
                if max_page > 1:
                    sem = asyncio.Semaphore(3) # 限制并发并发抓取页码的连接数
                    async def fetch_page(page_num: int):
                        async with sem:
                            try:
                                r, _ = await cls.fetch(client, f"/photos-index-page-{page_num}-aid-{aid}.html", timeout=15.0)
                                p_soup = BeautifulSoup(r.text, 'html.parser')
                                p_pics = p_soup.select('.pic_box a')
                                links = []
                                for pic in p_pics:
                                    href = pic.get('href')
                                    if href:
                                        if href.startswith('/'):
                                            links.append(f"{base_url}{href}")
                                        else:
                                            links.append(href)
                                return page_num, links
                            except Exception as e:
                                logger.error(f"Failed to fetch page {page_num} of {aid}: {e}")
                        return page_num, []

                    tasks = [fetch_page(p) for p in range(2, max_page + 1)]
                    results = await asyncio.gather(*tasks)
                    
                    # 按页码顺序拼装
                    results.sort(key=lambda x: x[0])
                    for _, links in results:
                        view_links.extend(links)
                        
            except Exception as e:
                logger.error(f"Failed to get image view links: {e}")
                
        return view_links

    @classmethod
    async def get_raw_image_url(cls, view_url: str, client: AsyncSession = None) -> str:
        """
        进入浏览页解析真正的原图 URL
        """
        async def fetch_img(c):
            try:
                from urllib.parse import urlparse
                path = urlparse(view_url).path
                resp, _ = await cls.fetch(c, path, allow_redirects=True, timeout=15.0)
                soup = BeautifulSoup(resp.text, 'html.parser')
                img_elem = soup.select_one('#picarea')
                if img_elem:
                    src = img_elem.get('src', '')
                    if src.startswith('//'):
                        src = 'https:' + src
                    return src
            except Exception as e:
                logger.error(f"Failed to get raw image url from {view_url}: {e}")
            return ""

        if client:
            return await fetch_img(client)
        else:
            async with cls.get_client() as c:
                return await fetch_img(c)
