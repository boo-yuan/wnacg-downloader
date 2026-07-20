<div align="center">

# 🤖 WNACG Downloader

**一个现代化、高性能、优雅的 WNACG 漫画下载管理器**

![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![PySide6](https://img.shields.io/badge/UI-PySide6-brightgreen.svg)
![QFluentWidgets](https://img.shields.io/badge/Design-QFluentWidgets-orange.svg)
![License](https://img.shields.io/badge/License-MIT-purple.svg)

</div>

## 🌟 特性 (Features)

*   🎨 **现代化 Fluent UI 设计**：基于 `QFluentWidgets` 打造的 Windows 11 风格界面，提供极其顺滑的动画、亚克力效果与自适应瀑布流卡片布局。
*   🚀 **无视云盾的异步爬虫**：底层采用 `asyncio` 结合 `curl_cffi`，完美模拟真实 Chrome 浏览器指纹，轻松穿透 Cloudflare 的五秒盾 (5-second shield) 与人机验证。
*   🔄 **工业级任务调度**：支持多任务并行下载、断点续传、任意任务暂停/恢复。深度优化的并发控制，保护您的网络免受拥塞。
*   💾 **高可靠性数据库**：使用 SQLite 进行本地数据持久化并严格封装上下文管理，拒绝文件锁死，哪怕断电重启也能完美恢复进度。
*   ⚙️ **深度的细节自定义**：允许在设置内随意调节并发数（分别控制任务级与图片级）、请求间隔、代理模式（系统代理/直连/自定义），以及保存的图片格式。
*   🖼️ **自动图像处理**：内置 PIL 图像引擎，可自动处理带透明通道的 WEBP/PNG 图像并安全转换为纯白底色的 JPG，最大限度节约硬盘空间。

## 📸 界面预览 (Screenshots)

*等待补充界面截图...*

## 🛠️ 技术栈 (Tech Stack)

*   **GUI 框架**: [PySide6](https://doc.qt.io/qtforpython/) + [QFluentWidgets](https://qfluentwidgets.com/)
*   **爬虫核心**: [curl_cffi](https://github.com/lexiforest/curl_cffi) + [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/)
*   **异步运行库**: `asyncio`
*   **数据校验与模型**: `Pydantic` V2
*   **本地存储**: `SQLite3`

## 📦 安装与运行 (Installation & Run)

推荐使用现代化的 Python 包管理器 [uv](https://github.com/astral-sh/uv) 来运行本项目，以获得极速的依赖解析体验。

```bash
# 1. 克隆代码仓库
git clone https://github.com/boo-yuan/wnacg-downloader.git
cd wnacg-downloader

# 2. 安装依赖并运行（使用 uv）
uv run src/main.py
```

或者，如果您使用传统的 `pip`：
```bash
pip install -e .
python src/main.py
```

## ⌨️ 快捷键操作 (Shortcuts)

*   `Ctrl + A`：在搜索页和下载页中全选所有漫画卡片，方便批量下载或批量暂停。
*   `双击卡片`：在下载队列中，直接双击任意一张漫画卡片即可快速触发“暂停 / 继续下载”操作。

## 📄 开源协议 (License)

本项目遵循 MIT 开源协议。详情请参阅 [LICENSE](LICENSE) 文件。
