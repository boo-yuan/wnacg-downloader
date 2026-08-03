# WNACG Downloader

基于 PySide6 的桌面漫画下载管理器。程序会保存可恢复的任务进度，支持代理、并发限制、图片格式转换和原子 ZIP 打包。

## 安装与运行

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。项目只使用 `pyproject.toml` 和 `uv.lock` 管理依赖：

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

## 开发与验证

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pyright
uv run --locked pytest --cov
uv run --locked python -m wnacg --smoke-test
```

`build.bat` 使用锁定的 build 依赖组生成 PyInstaller 单文件程序。提交前可执行 `uv run --locked pre-commit run --all-files`。

## 模块边界

- `domain`：经过验证的领域模型、状态和任务选项值对象。
- `application`：下载编排、安全路径和文件事务。
- `infrastructure`：SQLite、HTTP、配置、日志和更新检查适配器。
- `ui`：Qt 界面及平台集成，不承载下载完整性规则。
