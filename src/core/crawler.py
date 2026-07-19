from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from typing import List, Tuple
from core.models import Comic
from core.config import cfg
from core.logger import logger
import urllib.parse

class WnacgCrawler:
    
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
                    
                results.append(Comic(
                    aid=aid,
                    title=title,
                    cover_url=cover_url,
                    url=link
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
        获取文章页中所有的浏览页(photos-view-id)链接。为了演示稳定版，先只抓第一页缩略图。
        """
        base_url = f"https://{cfg.domain}"
        url = f"{base_url}/photos-index-page-1-aid-{aid}.html"
        view_links = []
        async with cls.get_client() as client:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')
                pics = soup.select('.pic_box a')
                for pic in pics:
                    href = pic.get('href')
                    if href:
                        # 兼容相对路径
                        if href.startswith('/'):
                            view_links.append(f"{base_url}{href}")
                        else:
                            view_links.append(href)
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
