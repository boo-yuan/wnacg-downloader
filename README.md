<div align="center">

# 🤖 WNACG Downloader (v1.0.0)

**一个现代化、高性能、且优雅的 WNACG 漫画极速下载器**

![Python Version](https://img.shields.io/badge/Python-3.11%2B-blue.svg)
![PySide6](https://img.shields.io/badge/UI-PySide6-brightgreen.svg)
![QFluentWidgets](https://img.shields.io/badge/Design-QFluentWidgets-orange.svg)
![License](https://img.shields.io/badge/License-MIT-purple.svg)

</div>

## 🎉 简介 (Introduction)

WNACG Downloader 不仅仅是一个美观的漫画下载管理器，它更是专门针对 WNACG 特殊的网络环境而重金属打造的**全自动化引擎**。

*   **小白福音：解压即用**。通过 GitHub Actions 自动化打包，无需配置任何 Python 环境。
*   **一键极速热更新**：深度集成了国内公益镜像源（如 kkgithub），完全无需代理，一键就能满速拉取最新版本！
*   **优雅如丝的交互体验**：基于 QFluentWidgets 打造的 Windows 11 亚克力风格界面，支持鼠标框选、Shift 连选、右键批量管理。

---

## 🚀 硬核底层架构 (Hardcore Technical Architecture)

为了打造工业级的稳定性，我们在底层实现了一系列“黑科技”，从根本上解决了爬虫经常遇到的封 IP、半截图、卡死等顽疾：

### 1. 🌐 全局令牌桶限流 (Global Token Bucket Pacer)
告别了传统爬虫粗暴的“并发齐射”。底层引入全局发牌器，所有并发请求会排队通过一个极窄的时间阀门。配合内置的 0.7x~1.3x 随机抖动（Jitter），你的每一次下载请求都会像真正的人类在浏览一样自然错开，彻底瓦解 Cloudflare 等高级 WAF 的高频流量探针。

### 2. 🛡️ 深度防撕裂校验 (Deep Image Validation)
抛弃了仅靠检测文件头的弱校验。现在，下载器在每一张图片落地后，都会强制通过 PIL.Image.load() 试探其二进制流末尾（EOF）。任何因为网络闪断导致截断的“半身图”、“灰底图”，都会被立刻揪出并打回重下，**确保你本地画廊里的每一张老婆都是 100% 完整的**。

### 3. 🛤️ 智能多轨域名容灾 (Multi-Domain Fallback Routing)
永远不用担心 wnacg.ru 突然被墙或遭受 DNS 污染。系统内置了动态备用域名阵列，当某一次请求遭遇连接超时，引擎会无感、静默地自动切换到存活的最优备用节点（.org, .net, .com）继续重试。

### 4. ⚡ 极限并发与无锁持久化
*   **SQLite WAL 日志模式**：解除了传统数据库的读写死锁。多协程在海量插入下载进度的同时，前端 UI 依然能维持毫秒级的极速刷新。
*   **连接池共享复用 (Connection Pooling)**：全局共享同一个 curl_cffi 底层会话，彻底砍掉了昂贵的 TLS 握手开销。

---

## 📸 界面预览 (Screenshots)

*（上传您的精美截图替换此区域）*

## 📦 极速上手 (Quick Start)

### 选项一：普通玩家 (免环境直装版)
1. 前往右侧的 [Releases 页面](../../releases/latest)。
2. 下载 WNACG-Downloader-Windows.zip。
3. 解压并直接双击运行 WNACG-Downloader.exe 即可畅快使用！

### 选项二：极客玩家 (源码运行)
推荐使用现代化的 Python 包管理器 [uv](https://github.com/astral-sh/uv) 来运行本项目，以获得极速的依赖解析体验。

`ash
# 1. 克隆代码仓库
git clone https://github.com/your_github_username/wnacg-downloader.git
cd wnacg-downloader

# 2. 极速安装并运行
uv run src/main.py
`

## ⌨️ 高效交互指南 (Interactive Tips)
*   鼠标框选 / Shift 连选 / Ctrl+A 全选：均已在列表页原生支持，方便您进行批量的暂停或删除。
*   双击卡片：在下载任务列表中，直接双击卡片中心区域即可快速“暂停 / 恢复”任务。

## 📄 开源协议 (License)
本项目遵循 MIT 开源协议。详情请参阅 [LICENSE](LICENSE) 文件。
