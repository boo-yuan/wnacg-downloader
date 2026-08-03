# WNACG Downloader

基于 PySide6 的桌面漫画下载管理器。程序会保存可恢复的任务进度，支持代理、并发限制、图片格式转换和原子 ZIP 打包。

## 安装与运行

需要 Python 3.12 或 3.13，以及 [uv](https://docs.astral.sh/uv/)。项目只使用 `pyproject.toml` 和 `uv.lock` 管理依赖：

```powershell
uv sync --locked --group dev
uv run --locked python -m wnacg
```

也可以运行 `run.bat`。搜索框支持关键词、`aid:编号` 或完整画廊 URL。

## 数据与安全

- 配置、SQLite 任务库和 JSON Lines 日志默认位于系统应用数据目录；可用 `WNACG_DATA_DIR` 覆盖。
- 下载目录使用“标题 `[aid]`”命名，避免同名画廊互相覆盖。
- 取消任务只会删除数据库记录中保存的下载根目录直属子目录；修改设置不会改变旧任务的删除目标。
- 网络请求保持 TLS 验证。更新检查只访问 GitHub 官方 API，并打开官方发布页，不自动执行下载内容。
- 图片先写临时文件并验证后再替换；ZIP 完整写入并校验后才替换最终文件。
- 所有响应体、单图、画廊图片数、任务总字节数和解码像素数均有硬限制；下载前还会检查磁盘余量。
- 重定向目标和解析后的地址会执行 HTTPS、公网地址及允许域校验，防止请求落入本机或私网。
- 任务目录中的用户文件不会被打包或清理；程序只管理清单中明确记录的图片文件。

## 开发与验证

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest --cov
uv run --locked python -m wnacg --smoke-test
uv audit --locked --no-dev
```

测试门禁统计整个 `wnacg` 包（包括入口和 Qt UI），要求分支覆盖率不低于 55%；当前完整测试覆盖率约为 57%。`build.bat` 使用锁定的纯运行时与 build 依赖组生成 PyInstaller 单文件程序；`run.bat` 不安装开发依赖。提交前可执行 `uv run --locked pre-commit run --all-files`。

CI 在 Python 3.12/3.13 上执行 Ruff、严格 Pyright、pytest、源码烟雾测试，并单独完成 PyInstaller 构建/EXE 烟雾测试、OSV 依赖审计和 CycloneDX SBOM 导出验证。第三方 Actions 使用完整提交 SHA 固定。

## 模块边界

- `domain`：经过验证的领域模型、状态和任务选项值对象。
- `application`：下载编排、安全路径、资源限额、图片事务和持久化端口。
- `infrastructure`：SQLite、HTTP、配置、日志和更新检查适配器。
- `ui`：Qt 界面及平台集成，通过构造参数接收下载器和仓储端口，不承载下载完整性规则。

入口是唯一组合根：它显式初始化路径、日志、配置和数据库，再构造下载器并注入 UI；导入模块不会创建目录、迁移数据或实例化下载线程。
