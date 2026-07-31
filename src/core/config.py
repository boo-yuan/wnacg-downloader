import json
from enum import Enum
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings

CONFIG_FILE = Path("data/config.json")
CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

class ProxyMode(str, Enum):
    DIRECT = "direct"
    SYSTEM = "system"
    CUSTOM = "custom"

class AppConfig(BaseSettings):
    proxy_mode: ProxyMode = Field(default=ProxyMode.SYSTEM, description="默认代理模式")
    custom_proxy: str = Field(default="http://127.0.0.1:7890", description="自定义代理地址")
    download_dir: str = Field(default="downloads", description="默认下载保存路径")
    domain: str = Field(default="www.wnacg.ru", description="WNACG主域名")
    download_naming: str = Field(default="original", description="下载命名方式：original / sequential")
    download_format: str = Field(default="jpg", description="下载格式：original / jpg / png / webp")
    auto_start_download: bool = Field(default=True, description="加入下载队列后是否立即下载")
    max_concurrent_tasks: int = Field(default=2, description="同时下载的最大任务数")
    global_max_connections: int = Field(default=8, description="全局最大并发下载连接数")
    download_delay: float = Field(default=1.0, description="每张图片下载间的延迟(秒)")
    pack_to_zip: bool = Field(default=True, description="下载完成后是否自动打包为ZIP")
    delete_original_after_pack: bool = Field(default=True, description="打包完成后是否删除原文件夹")

    @property
    def curl_cffi_proxies(self) -> dict | None:
        """根据当前模式返回给 curl_cffi 的 proxies 参数"""
        if self.proxy_mode == ProxyMode.CUSTOM:
            return {"http": self.custom_proxy, "https": self.custom_proxy}
        return None  # DIRECT 和 SYSTEM 可以在外部逻辑中通过不传递 proxies 来处理

    def save(self) -> None:
        """持久化保存配置到本地 JSON 文件"""
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(self.model_dump_json(indent=4))

def load_config() -> AppConfig:
    data = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
            
    c = AppConfig(**data)
    c.save()
    return c

# 全局配置实例
cfg = load_config()
