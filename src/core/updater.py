import asyncio
from curl_cffi.requests import AsyncSession
from core.logger import logger

class Updater:
    REPO = "your_github_username/wnacg-downloader" # 替换为您的实际 GitHub 仓库
    CURRENT_VERSION = "v1.0.0"
    API_MIRRORS = [
        "https://api.kkgithub.com/repos/{repo}/releases/latest",
        "https://api.github.com/repos/{repo}/releases/latest"
    ]
    
    @classmethod
    async def check_update(cls) -> dict:
        """
        Returns a dict: {"has_update": bool, "latest_version": str, "release_notes": str, "download_url": str}
        """
        async with AsyncSession(impersonate="chrome", verify=False) as client:
            for mirror in cls.API_MIRRORS:
                url = mirror.format(repo=cls.REPO)
                try:
                    resp = await client.get(url, timeout=10.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        latest_tag = data.get("tag_name", "")
                        body = data.get("body", "")
                        
                        assets = data.get("assets", [])
                        download_url = ""
                        for asset in assets:
                            name = asset.get("name", "")
                            if name.endswith(".zip") or name.endswith(".exe") or name == "WNACG-Downloader":
                                download_url = asset.get("browser_download_url", "")
                                break
                                
                        if not download_url and data.get("html_url"):
                            download_url = data.get("html_url")
                            
                        # 使用 kkgithub 镜像加速下载
                        if download_url and "github.com" in download_url:
                            download_url = download_url.replace("github.com", "kkgithub.com")
                            
                        has_update = (latest_tag != cls.CURRENT_VERSION)
                        
                        return {
                            "has_update": has_update and latest_tag != "",
                            "latest_version": latest_tag,
                            "release_notes": body,
                            "download_url": download_url
                        }
                except Exception as e:
                    logger.warning(f"Failed to check update via {mirror}: {e}")
                    
        return {"has_update": False, "latest_version": cls.CURRENT_VERSION, "release_notes": "", "download_url": ""}

