<div align="center">

# 📚 WNACG Downloader

**稳定、可恢复的 Windows 桌面画廊下载管理器**

![Version](https://img.shields.io/badge/version-1.3.0-2563eb.svg)
![Python](https://img.shields.io/badge/Python-3.12%20%7C%203.13-3776ab.svg)
![Platform](https://img.shields.io/badge/platform-Windows-0078d4.svg)
![License](https://img.shields.io/badge/license-MIT-7c3aed.svg)

</div>

WNACG Downloader 基于 PySide6 构建，支持画廊搜索与 URL 解析、可恢复任务、批量控制、代理、全局并发与限速、图片格式转换，以及经过完整性校验的原子 ZIP 打包。

当前最新版本：`1.3.0`

## ✨ 核心功能

- 🔍 **灵活搜索**：支持关键词、`aid:编号` 和完整画廊 URL。
- ⏯️ **任务恢复**：支持多任务下载、暂停、恢复、失败重试和启动后进度恢复。
- 🖼️ **格式转换**：支持原文件、JPG、PNG、WebP，以及原始名称或顺序编号。
- 📦 **安全打包**：可选择打包为 ZIP，归档通过完整性检查后才替换最终文件。
- 🌐 **代理支持**：支持系统代理、直连和自定义 HTTP(S)/SOCKS 代理。
- 🚦 **资源控制**：支持全局连接数、任务并发数、请求间隔和下载速度限制。
- 🖥️ **桌面体验**：支持系统托盘、完成通知、批量选择和打开下载目录。
- 🎨 **主题切换**：默认跟随 Windows 系统，也可固定为浅色或深色模式。
- 🔄 **缺失恢复**：完成记录对应文件丢失时标记为 `MISSING`，可直接重新下载。

## 🖼️ 界面预览

<table>
  <tr>
    <td width="50%" align="center"><img src="assets/001.png" alt="搜索与画廊界面"><br><b>搜索与画廊</b></td>
    <td width="50%" align="center"><img src="assets/002.png" alt="下载任务界面"><br><b>下载任务</b></td>
  </tr>
  <tr>
    <td width="50%" align="center"><img src="assets/003.png" alt="设置界面"><br><b>应用设置</b></td>
    <td width="50%" align="center"><img src="assets/004.png" alt="空状态界面"><br><b>空状态</b></td>
  </tr>
  <tr>
    <td width="50%" align="center"><img src="assets/005.png" alt="退出确认界面"><br><b>退出确认</b></td>
    <td width="50%" align="center"><img src="assets/006.png" alt="任务控制界面"><br><b>任务控制</b></td>
  </tr>
</table>

## 🚀 快速开始

### 🪟 Windows 单文件程序

1. 从 [Releases](../../releases/latest) 下载 `WNACG-Downloader.exe`。
2. 将程序放到合适的位置并直接运行，无需安装 Python。
3. 首次启动后按需调整下载目录、代理、并发和输出格式。

默认下载目录为 `%USERPROFILE%\Downloads\wnacg`。

### 🧑‍💻 从源码运行

需要 Windows、Python 3.12 或 3.13，以及 [uv](https://docs.astral.sh/uv/)。项目只使用 `pyproject.toml` 和 `uv.lock` 管理依赖。

```powershell
git clone https://github.com/boo-yuan/wnacg-downloader.git
cd wnacg-downloader
uv sync --locked
uv run --locked python -m wnacg
```

也可以执行 `run.bat`。脚本会按照锁文件同步运行时依赖，然后启动应用。

### 🔎 支持的搜索输入

```text
关键词
aid:123456
https://www.wnacg.com/photos-index-aid-123456.html
```

## ⚙️ 数据与配置

| 项目 | 默认位置或行为 |
|---|---|
| 应用数据 | `%LOCALAPPDATA%\wnacg-downloader` |
| 下载目录 | `%USERPROFILE%\Downloads\wnacg` |
| 配置 | 应用数据目录中的 `config.json` |
| 任务记录 | 应用数据目录中的 SQLite 数据库 |
| 日志 | 应用数据目录中的 JSON Lines 日志 |
| 数据目录覆盖 | 环境变量 `WNACG_DATA_DIR` |

其他 `WNACG_` 前缀的配置环境变量会在运行时优先于配置文件，但不会意外覆盖已经持久化的用户值。

## 🛡️ 安全与数据完整性

- 所有 HTTP 请求保持 TLS 证书验证；直连模式还会校验 DNS 结果、重定向地址和实际连接对端，阻止访问本机或私网地址。
- 更新功能只读取 GitHub 官方发布元数据并打开官方发布页，不会自动下载或执行程序。
- HTML、封面、单图、画廊图片数、分页数、整本字节数和图片解码像素均设有硬上限，并在写入时持续检查磁盘余量。
- 图片先写入临时文件，经内容类型、完整解码和像素预算检查后再原子替换。
- 下载目录只使用漫画标题命名；同名或已有目录自动追加 `(2)`、`(3)`，不会混入 `aid`，也不会互相覆盖。
- 文件清单只记录程序拥有的文件。打包、重下和取消清理不会处理用户自行放入任务目录的普通文件。
- 删除前重新验证下载根目录、任务直属目录、符号链接和 Windows junction，降低路径伪造或误删风险。

> [!IMPORTANT]
> 这些保护用于降低风险，但不能保证目标站点始终可用，也不能保证任何使用方式不会触发网站的访问限制。请合理设置并发和请求间隔，并遵守目标站点规则及所在地法律。

## 📦 本地构建 EXE

GitHub CI 不构建或发布 EXE。发布者需要在 Windows 本地手动构建：

```powershell
.\build.bat
```

成功后生成 `dist\WNACG-Downloader.exe`。发布前应执行烟雾测试并计算 SHA-256：

```powershell
$process = Start-Process -FilePath '.\dist\WNACG-Downloader.exe' -ArgumentList '--smoke-test' -Wait -PassThru
if ($process.ExitCode -ne 0) { exit $process.ExitCode }
Get-FileHash -Algorithm SHA256 .\dist\WNACG-Downloader.exe
```

## 🧪 开发与验证

```powershell
uv sync --locked --group dev
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest --cov --cov-report=term-missing
uv run --locked python -m wnacg --smoke-test
uv audit --locked --no-dev
```

质量门禁覆盖整个 `wnacg` 包，包括入口和 Qt UI；分支覆盖率不得低于 55%。当前回归集为 106 项测试，最近一次全包分支覆盖率为 63.63%。

GitHub CI 在 Python 3.12 和 3.13 上执行 Ruff、严格 Pyright、pytest、源码烟雾测试、依赖审计和 CycloneDX SBOM 导出验证。

## 🧱 架构边界

| 层 | 职责 |
|---|---|
| `domain` | 领域模型、状态机和不可变任务选项 |
| `application` | 下载编排、安全路径、资源限额、文件事务和持久化端口 |
| `infrastructure` | SQLite、HTTP、配置、日志和更新检查适配器 |
| `ui` | Qt 界面及平台集成，通过构造参数接收下载器和仓储端口 |

入口是唯一组合根：单实例锁成功后显式初始化路径、日志、配置和数据库，再构造下载器并注入 UI。导入模块不会迁移数据库或启动下载线程。

## 📝 版本记录

<details open>
<summary><strong>v1.3.0 · 当前最新版</strong></summary>

本版本重点解决大队列下载、页面切换和后台 worker 生命周期引发的卡顿或原生闪退，同时完善主题体验、域名发现、任务状态展示、目录命名与队列顺序。

- 修复域名发布页布局和命名规则变化导致的空结果，支持当前 `wn+数字` 域名并提供多发布页回退。
- 新增“跟随系统 / 浅色模式 / 深色模式”三态外观设置，选择即时生效并持久化；设置功能区背景、卡片、状态色、分页、空状态、弹窗和悬浮按钮统一适配明暗主题。
- 搜索等待页升级为主题自适应动态加载状态，以旋转轨道、呼吸光效和循环省略号持续反馈请求进度，页面隐藏后自动停止动画。
- Windows 下载线程改用原生 Selector 事件循环；下载、搜索、封面和设置 worker 的信号统一回到 GUI 主线程，并在退出时有界收尾，降低切页和持续下载时的原生闪退风险。
- 单本漫画改用固定数量的图片 worker，不再按图片总数一次性创建异步任务；所有流式 HTTP 响应在成功、失败和取消路径都确定释放。
- 下载列表每页最多保留 100 个任务卡片，徽标与状态统计使用 SQL `COUNT`，连续状态变化合并刷新，避免大队列持续堆积控件和反复全量读取。
- 数据库列表、分页界面和实际调度共享同一顺序：正在下载置顶，未完成任务按加入顺序 FIFO，新任务不会插队，已完成任务沉底。
- 搜索卡片实时区分已加入队列、已下载、本地文件缺失和下载失败；过期查询与封面结果不会再写入已经销毁或复用的卡片。
- 直链列表会过滤站点混入的 `shoucang.jpg` 收藏按钮资源，图片数准确时不再误触发逐页回退请求。
- 漫画目录改为纯标题命名，同名任务使用 `(2)`、`(3)` 后缀安全避让；只迁移尚未产生文件的旧任务路径，不移动用户已有下载内容。
- GitHub CI 专注代码质量、测试、依赖审计和 SBOM；Windows 单文件 EXE 改为使用锁文件在本地手动构建与验证。
- 回归集增至 106 项，Ruff、严格 Pyright、源码烟雾测试和 55% 全包分支覆盖率门槛均通过，最近一次覆盖率为 63.63%。

</details>

<details>
<summary><strong>v1.2.0</strong></summary>

第三个公开版本，完成针对恢复能力、并发一致性、网络与文件安全的系统性整改：引入原子任务领取和进度持久化、不可变任务选项快照、`MISSING` 恢复状态、网络与资源硬上限、文件所有权清单、安全删除校验，以及损坏数据库和配置的隔离恢复；同时完成分层架构重构，并建立 Ruff、严格 Pyright、pytest、覆盖率、烟雾测试、依赖审计和 SBOM 门禁。

</details>

<details>
<summary><strong>v1.1.0</strong></summary>

第二个公开版本，重点优化下载队列调度和长时间运行时的资源回收，修复画廊页面转义内容导致的图片数量解析问题，并完善完成通知、暂停提示、双击打开下载位置、右键菜单和设置界面联动等交互体验。

</details>

<details>
<summary><strong>v1.0.0</strong></summary>

首个公开稳定版本，提供 Windows 单文件程序、图形化搜索与下载队列、暂停/恢复、系统托盘、完成通知、代理、并发与速度限制、图片转换和 ZIP 打包等基础能力，并建立 SQLite 任务持久化与日志轮转机制。

</details>

完整的审查与整改映射见 [docs/issue-remediation.md](docs/issue-remediation.md)。

## ⚠️ 免责声明

本项目仅用于个人学习与技术研究。使用者应遵守目标站点条款、版权规定及所在地法律法规，不得将本程序用于侵权、商业滥用或其他非法用途。因使用本项目产生的风险和责任由使用者自行承担。

## 📄 许可证

本项目采用 [MIT License](LICENSE)。
