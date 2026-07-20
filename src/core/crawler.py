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
        
    @classmethod
    def search_sync(cls, keyword: str, page: int = 1) -> Tuple[List[Comic], int]:
        """
        同步版本的搜索逻辑，用于后台独立线程，避免多个 asyncio 事件循环导致 curl_cffi 崩溃
        """
        encoded_kw = urllib.parse.quote(keyword)
        base_url = f"https://{cfg.domain}"
        url = f"{base_url}/search/index.php?q={encoded_kw}&m=&syn=yes&f=_all&s=create_time_DESC&p={page}"
        
        results = []
        with cls.get_sync_client() as client:
            resp = client.get(url)
            resp.raise_for_status()
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
        base_url = f"https://{cfg.domain}"
        url = f"{base_url}/search/index.php?q={encoded_kw}&m=&syn=yes&f=_all&s=create_time_DESC&p={page}"
        
        results = []
        async with cls.get_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
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
        base_url = f"https://{cfg.domain}"
        view_links = []
        
        async with cls.get_client() as client:
            try:
                first_page_url = f"{base_url}/photos-index-page-1-aid-{aid}.html"
                resp = await client.get(first_page_url)
                resp.raise_for_status()
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
                        page_url = f"{base_url}/photos-index-page-{page_num}-aid-{aid}.html"
                        async with sem:
                            for attempt in range(3):
                                try:
                                    r = await client.get(page_url, timeout=15.0)
                                    r.raise_for_status()
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
                                    if attempt == 2:
                                        logger.error(f"Failed to fetch page {page_num} of {aid}: {e}")
                                    await asyncio.sleep(1.0)
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
    async def get_raw_image_url(cls, view_url: str) -> str:
        """
        进入浏览页解析真正的原图 URL
        """
        async with cls.get_client() as client:
            try:
                resp = await client.get(view_url, allow_redirects=True)
                if resp.status_code != 200:
                    logger.warning(f"Raw image view returned status code {resp.status_code}")
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
