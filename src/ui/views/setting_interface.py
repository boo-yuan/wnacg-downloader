import asyncio
import os
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi.requests import Session
from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QWidget
from qfluentwidgets import (
    ComboBox,
    DoubleSpinBox,
    EditableComboBox,
    ExpandLayout,
    LineEdit,
    PrimaryPushSettingCard,
    PushSettingCard,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
    SpinBox,
    SwitchButton,
    setFont,
)
from qfluentwidgets import FluentIcon as FIF

from core.config import ProxyMode, cfg
from core.updater import Updater


class LineEditSettingCard(SettingCard):
    """ Custom setting card for line edit input """
    def __init__(self, icon, title, content, parent=None):
        super().__init__(icon, title, content, parent)
        self.lineEdit = LineEdit(self)
        self.lineEdit.setClearButtonEnabled(True)
        self.lineEdit.setFixedWidth(280)
        self.hBoxLayout.addWidget(self.lineEdit, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

class MySwitchSettingCard(SettingCard):
    """ Custom setting card for switch button """
    def __init__(self, icon, title, content, parent=None):
        super().__init__(icon, title, content, parent)
        self.switchButton = SwitchButton(self)
        self.hBoxLayout.addWidget(self.switchButton, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)
        
    def setChecked(self, isChecked: bool):
        self.switchButton.setChecked(isChecked)
        
    @property
    def checkedChanged(self):
        return self.switchButton.checkedChanged

class ComboBoxSettingCard(SettingCard):
    """ Custom setting card for combo box input """
    def __init__(self, icon, title, content, texts, parent=None):
        super().__init__(icon, title, content, parent)
        self.comboBox = ComboBox(self)
        self.comboBox.setFixedWidth(280)
        self.comboBox.addItems(texts)
        self.hBoxLayout.addWidget(self.comboBox, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

class EditableComboBoxSettingCard(SettingCard):
    """ Custom setting card for editable combo box input """
    def __init__(self, icon, title, content, texts, parent=None):
        super().__init__(icon, title, content, parent)
        self.comboBox = EditableComboBox(self)
        self.comboBox.setFixedWidth(280)
        self.comboBox.addItems(texts)
        self.hBoxLayout.addWidget(self.comboBox, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

class SpinBoxSettingCard(SettingCard):
    def __init__(self, icon, title, content, parent=None):
        super().__init__(icon, title, content, parent)
        self.spinBox = SpinBox(self)
        setFont(self.spinBox)
        self.hBoxLayout.addWidget(self.spinBox, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

class DoubleSpinBoxSettingCard(SettingCard):
    def __init__(self, icon, title, content, parent=None):
        super().__init__(icon, title, content, parent)
        self.spinBox = DoubleSpinBox(self)
        setFont(self.spinBox)
        self.hBoxLayout.addWidget(self.spinBox, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

class DomainFetchWorker(QThread):
    finished_signal = Signal(list)
    
    def run(self):
        try:
            domains = self.fetch()
            self.finished_signal.emit(domains)
        except Exception:
            pass

    def fetch(self):
        domains = []
        try:
            kwargs = {
                "impersonate": "chrome120",
                "timeout": 10
            }
            if cfg.proxy_mode == "custom":
                kwargs["proxies"] = cfg.curl_cffi_proxies
            elif cfg.proxy_mode == "direct":
                kwargs["trust_env"] = False
            else:
                kwargs["trust_env"] = True
                
            with Session(**kwargs) as s:
                r = s.get("https://wnacg01.link/")
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, 'html.parser')
                    lis = soup.select('.content-top ul li')
                    if len(lis) > 3:
                        lis = lis[2:-1]
                        
                    for li in lis:
                        a = li.find('a')
                        if a:
                            text = a.text.replace('\xa0', ' ').strip()
                        else:
                            text = li.text.replace('\xa0', ' ').strip()
                            
                        text = text.replace("https://", "").replace("http://", "").replace("/", "").strip()
                        if text and text not in domains:
                            domains.append(text)
        except Exception:
            pass
        return domains

class UpdateCheckWorker(QThread):
    finished_signal = Signal(dict)
    
    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(Updater.check_update())
            loop.close()
            self.finished_signal.emit(result)
        except Exception as e:
            self.finished_signal.emit({"error": str(e)})


class BaseSettingInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)
        
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea, .QScrollArea > QWidget > QWidget { background: transparent; }")


class NetworkSettingInterface(BaseSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("NetworkSettingInterface")
        self._init_proxy_settings()
        self._init_system_settings()

    def _init_proxy_settings(self):
        self.proxyGroup = SettingCardGroup("网络与代理设置", self.scrollWidget)
        
        self.proxyModeCard = ComboBoxSettingCard(
            icon=FIF.GLOBE,
            title="代理模式",
            content="选择软件使用的代理类型",
            texts=["系统代理", "直连", "自定义代理"],
            parent=self.proxyGroup
        )
        mode_map = {ProxyMode.SYSTEM: 0, ProxyMode.DIRECT: 1, ProxyMode.CUSTOM: 2}
        self.proxyModeCard.comboBox.setCurrentIndex(mode_map.get(cfg.proxy_mode, 0))
        self.proxyModeCard.comboBox.currentIndexChanged.connect(self._on_proxy_mode_changed)

        self.customProxyCard = LineEditSettingCard(
            icon=FIF.LINK,
            title="自定义代理地址",
            content="例如: http://127.0.0.1:7890",
            parent=self.proxyGroup
        )
        self.customProxyCard.lineEdit.setText(cfg.custom_proxy)
        self.customProxyCard.lineEdit.textChanged.connect(self._on_custom_proxy_changed)
        
        self.proxyGroup.addSettingCard(self.proxyModeCard)
        self.proxyGroup.addSettingCard(self.customProxyCard)
        
        self.expandLayout.addWidget(self.proxyGroup)
        
    def _init_system_settings(self):
        self.concurrentGroup = SettingCardGroup("网络与并发控制", self.scrollWidget)
        
        self.maxTasksCard = SpinBoxSettingCard(
            icon=FIF.CLOUD_DOWNLOAD,
            title="最大并行任务数",
            content="同时下载的漫画数量（建议 1-5）",
            parent=self.concurrentGroup
        )
        self.maxTasksCard.spinBox.setRange(1, 10)
        self.maxTasksCard.spinBox.setValue(cfg.max_concurrent_tasks)
        self.maxTasksCard.spinBox.valueChanged.connect(self._on_max_tasks_changed)
        
        self.globalConnectionsCard = SpinBoxSettingCard(
            icon=FIF.IOT,
            title="全局最大并发连接数",
            content="控制底层的最高并发网络连接数（建议 4-16）",
            parent=self.concurrentGroup
        )
        self.globalConnectionsCard.spinBox.setRange(1, 32)
        self.globalConnectionsCard.spinBox.setValue(cfg.global_max_connections)
        self.globalConnectionsCard.spinBox.valueChanged.connect(self._on_global_connections_changed)
        
        self.downloadDelayCard = DoubleSpinBoxSettingCard(
            icon=FIF.HISTORY,
            title="图片下载间隔时间",
            content="每张图片下载完毕后的延迟时间(秒)，缓解服务器压力",
            parent=self.concurrentGroup
        )
        self.downloadDelayCard.spinBox.setRange(0.0, 10.0)
        self.downloadDelayCard.spinBox.setSingleStep(0.5)
        self.downloadDelayCard.spinBox.setValue(cfg.download_delay)
        self.downloadDelayCard.spinBox.valueChanged.connect(self._on_download_delay_changed)
        
        self.globalSpeedLimitCard = SpinBoxSettingCard(
            icon=FIF.SPEED_OFF,
            title="全局下载限速 (KB/s)",
            content="设置为 0 表示不限制下载速度",
            parent=self.concurrentGroup
        )
        self.globalSpeedLimitCard.spinBox.setRange(0, 999999)
        self.globalSpeedLimitCard.spinBox.setSingleStep(100)
        self.globalSpeedLimitCard.spinBox.setValue(cfg.global_speed_limit)
        self.globalSpeedLimitCard.spinBox.valueChanged.connect(self._on_global_speed_limit_changed)
        
        self.concurrentGroup.addSettingCard(self.maxTasksCard)
        self.concurrentGroup.addSettingCard(self.globalConnectionsCard)
        self.concurrentGroup.addSettingCard(self.downloadDelayCard)
        self.concurrentGroup.addSettingCard(self.globalSpeedLimitCard)
        self.expandLayout.addWidget(self.concurrentGroup)

    def _on_proxy_mode_changed(self, index: int):
        modes = [ProxyMode.SYSTEM, ProxyMode.DIRECT, ProxyMode.CUSTOM]
        cfg.proxy_mode = modes[index]
        cfg.save()
        
    def _on_custom_proxy_changed(self, text: str):
        cfg.custom_proxy = text
        cfg.save()

    def _on_max_tasks_changed(self, value: int):
        cfg.max_concurrent_tasks = value
        cfg.save()
        
    def _on_global_connections_changed(self, value: int):
        cfg.global_max_connections = value
        cfg.save()
        
    def _on_download_delay_changed(self, value: float):
        cfg.download_delay = value
        cfg.save()
        
    def _on_global_speed_limit_changed(self, value: int):
        cfg.global_speed_limit = value
        cfg.save()


class DownloadSettingInterface(BaseSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("DownloadSettingInterface")
        self._init_download_settings()

    def _init_download_settings(self):
        self.sysGroup = SettingCardGroup("下载与存储设置", self.scrollWidget)
        
        self.downloadDirCard = PushSettingCard(
            text="选择文件夹",
            icon=FIF.FOLDER,
            title="下载保存目录",
            content=str(Path(cfg.download_dir).absolute()),
            parent=self.sysGroup
        )
        self.downloadDirCard.clicked.connect(self._on_download_dir_clicked)
        
        self.downloadNamingCard = ComboBoxSettingCard(
            icon=FIF.EDIT,
            title="图像命名规则",
            content="选择下载后的图片保存名称",
            texts=["使用原始名称", "按顺序重命名 (001, 002)"],
            parent=self.sysGroup
        )
        naming_map = {"original": 0, "sequential": 1}
        self.downloadNamingCard.comboBox.setCurrentIndex(naming_map.get(cfg.download_naming, 0))
        self.downloadNamingCard.comboBox.currentIndexChanged.connect(self._on_download_naming_changed)

        self.downloadFormatCard = ComboBoxSettingCard(
            icon=FIF.PHOTO,
            title="图像保存格式",
            content="下载后转换保存的图像格式",
            texts=["原始格式", "JPG", "PNG", "WEBP"],
            parent=self.sysGroup
        )
        format_map = {"original": 0, "jpg": 1, "png": 2, "webp": 3}
        self.downloadFormatCard.comboBox.setCurrentIndex(format_map.get(cfg.download_format, 1))
        self.downloadFormatCard.comboBox.currentIndexChanged.connect(self._on_download_format_changed)
        
        self.sysGroup.addSettingCard(self.downloadDirCard)
        self.sysGroup.addSettingCard(self.downloadNamingCard)
        self.sysGroup.addSettingCard(self.downloadFormatCard)
        
        self.packZipCard = MySwitchSettingCard(
            icon=FIF.ZIP_FOLDER,
            title="自动打包为 ZIP",
            content="下载完成后自动将图片打包为一个 ZIP 文件",
            parent=self.sysGroup
        )
        self.packZipCard.setChecked(cfg.pack_to_zip)
        self.packZipCard.checkedChanged.connect(self._on_pack_zip_changed)
        
        self.deleteOriginalCard = MySwitchSettingCard(
            icon=FIF.DELETE,
            title="打包后删除原文件",
            content="打包 ZIP 完成后自动删除原始图片文件夹",
            parent=self.sysGroup
        )
        self.deleteOriginalCard.setChecked(cfg.delete_original_after_pack)
        self.deleteOriginalCard.checkedChanged.connect(self._on_delete_original_changed)

        self.sysGroup.addSettingCard(self.packZipCard)
        self.sysGroup.addSettingCard(self.deleteOriginalCard)
        
        self.autoStartCard = MySwitchSettingCard(
            icon=FIF.PLAY,
            title="添加任务后立即下载",
            content="关闭后任务将会保持为等待状态，需手动开始",
            parent=self.sysGroup
        )
        self.autoStartCard.setChecked(cfg.auto_start_download)
        self.autoStartCard.checkedChanged.connect(self._on_auto_start_changed)
        
        self.sysGroup.addSettingCard(self.autoStartCard)
        
        self.domainCard = EditableComboBoxSettingCard(
            icon=FIF.GLOBE,
            title="站点主域名",
            content="常被墙可随时更换，默认支持 www.wnacg.com 和 www.wnacg.ru",
            texts=["www.wnacg.com", "www.wnacg.ru"],
            parent=self.sysGroup
        )
        self.domainCard.comboBox.setText(cfg.domain)
        self.domainCard.comboBox.textChanged.connect(self._on_domain_changed)
        
        self.fetchDomainCard = PushSettingCard(
            text="获取",
            icon=FIF.SYNC,
            title="获取最新备用域名",
            content="从发布页 (wnacg01.link) 获取最新防屏蔽域名并添加到下拉列表中",
            parent=self.sysGroup
        )
        self.fetchDomainCard.clicked.connect(self._fetch_latest_domains)
        
        self.sysGroup.addSettingCard(self.domainCard)
        self.sysGroup.addSettingCard(self.fetchDomainCard)
        self.expandLayout.addWidget(self.sysGroup)

    def _on_download_naming_changed(self, index: int):
        modes = ["original", "sequential"]
        cfg.download_naming = modes[index]
        cfg.save()
        
    def _on_download_format_changed(self, index: int):
        formats = ["original", "jpg", "png", "webp"]
        cfg.download_format = formats[index]
        cfg.save()
        
    def _on_auto_start_changed(self, checked: bool):
        cfg.auto_start_download = checked
        cfg.save()
        
    def _on_pack_zip_changed(self, checked: bool):
        cfg.pack_to_zip = checked
        cfg.save()
        
    def _on_delete_original_changed(self, checked: bool):
        cfg.delete_original_after_pack = checked
        cfg.save()
        
    def _on_download_dir_clicked(self):
        directory = QFileDialog.getExistingDirectory(self, "选择下载保存目录", cfg.download_dir)
        if directory:
            cfg.download_dir = directory
            cfg.save()
            self.downloadDirCard.setContent(str(Path(directory).absolute()))
            
    def _on_domain_changed(self, text: str):
        cfg.domain = text
        cfg.save()
        
        from core.crawler import WnacgCrawler
        WnacgCrawler._active_domain = None
        
    def _fetch_latest_domains(self):
        self.fetchDomainCard.button.setText("获取中...")
        self.fetchDomainCard.button.setEnabled(False)
        self.fetch_worker = DomainFetchWorker(self)
        self.fetch_worker.finished_signal.connect(self._on_domains_fetched)
        self.fetch_worker.finished.connect(self.fetch_worker.deleteLater)
        self.fetch_worker.start()
        
    def _on_domains_fetched(self, domains):
        self.fetchDomainCard.button.setText("获取")
        self.fetchDomainCard.button.setEnabled(True)
        
        if not domains:
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.error("获取失败", "无法从发布页解析到最新域名，请检查网络", parent=self.window(), position=InfoBarPosition.TOP)
            return
            
        existing = [self.domainCard.comboBox.itemText(i) for i in range(self.domainCard.comboBox.count())]
        added = 0
        for d in domains:
            if d not in existing:
                self.domainCard.comboBox.addItem(d)
                existing.append(d)
                added += 1
                
        from core.crawler import WnacgCrawler
        WnacgCrawler._mirrors = existing
                
        from qfluentwidgets import InfoBar, InfoBarPosition
        if added > 0:
            InfoBar.success("获取成功", f"已成功添加 {added} 个最新域名到下拉列表中", parent=self.window(), position=InfoBarPosition.TOP)
            self.domainCard.comboBox.setCurrentIndex(self.domainCard.comboBox.count() - 1)
        else:
            InfoBar.warning("已是最新", "发布页中的最新域名已全部存在于列表中", parent=self.window(), position=InfoBarPosition.TOP)


class AboutSettingInterface(BaseSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("AboutSettingInterface")
        self._init_about_settings()

    def _init_about_settings(self):
        self.aboutGroup = SettingCardGroup("关于与系统", self.scrollWidget)
        
        self.logCard = PushSettingCard(
            text="查看日志",
            icon=FIF.DOCUMENT,
            title="程序运行日志",
            content="记录了程序的错误和重点信息",
            parent=self.aboutGroup
        )
        self.logCard.clicked.connect(self._open_log_file)
        
        import os

        from PySide6.QtGui import QIcon
        icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "resource", "icon.png"))
        
        self.helpCard = PushSettingCard(
            text="前往 GitHub",
            icon=QIcon(icon_path) if os.path.exists(icon_path) else FIF.GITHUB,
            title="WNACG Downloader",
            content="一款采用 Fluent 设计语言构建的高性能、跨平台 WNACG 漫画离线下载工具",
            parent=self.aboutGroup
        )
        self.helpCard.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/boo-yuan/wnacg-downloader")))
        
        self.updateCard = PrimaryPushSettingCard(
            text="检查更新",
            icon=FIF.UPDATE,
            title="检查新版本",
            content="一键从 GitHub 拉取最新版本更新",
            parent=self.aboutGroup
        )
        self.updateCard.clicked.connect(self._check_update)
        
        self.aboutCard = SettingCard(
            icon=FIF.INFO,
            title="当前版本",
            content="v1.0.0 (Release) | 本程序全权由 Antigravity 2.0 与 Gemini 3.1 Pro 强力驱动开发",
            parent=self.aboutGroup
        )
        
        self.closeToTrayCard = MySwitchSettingCard(
            icon=FIF.MINIMIZE,
            title="关闭窗口时最小化到系统托盘",
            content="开启后点击关闭按钮程序将后台运行下载任务",
            parent=self.aboutGroup
        )
        self.closeToTrayCard.setChecked(cfg.close_to_tray)
        self.closeToTrayCard.checkedChanged.connect(self._on_close_to_tray_changed)
        
        self.aboutGroup.addSettingCard(self.closeToTrayCard)
        self.aboutGroup.addSettingCard(self.logCard)
        self.aboutGroup.addSettingCard(self.helpCard)
        self.aboutGroup.addSettingCard(self.updateCard)
        self.aboutGroup.addSettingCard(self.aboutCard)
        self.expandLayout.addWidget(self.aboutGroup)

    def _open_log_file(self):
        log_path = Path("app.log").absolute()
        if not log_path.exists():
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("暂无日志记录\n")
            os.startfile(log_path)
        else:
            os.startfile(log_path)
            
    def _on_close_to_tray_changed(self, checked: bool):
        cfg.close_to_tray = checked
        cfg.save()

    def _check_update(self):
        self.updateCard.button.setText("检查中...")
        self.updateCard.button.setEnabled(False)
        self.updateWorker = UpdateCheckWorker(self)
        self.updateWorker.finished_signal.connect(self._on_update_checked)
        self.updateWorker.start()

    def _on_update_checked(self, result: dict):
        self.updateCard.button.setText("检查更新")
        self.updateCard.button.setEnabled(True)
        
        from qfluentwidgets import MessageBox
        if "error" in result:
            w = MessageBox("检查更新失败", f"无法连接到更新服务器：\n{result['error']}", self.window())
            w.exec()
            return
            
        if result.get("has_update"):
            w = MessageBox(f"发现新版本 {result.get('latest_version')}", f"更新日志：\n{result.get('release_notes')}\n\n是否立即下载更新？（已启用免翻墙加速）", self.window())
            if w.exec():
                url = result.get('download_url')
                if url:
                    from PySide6.QtCore import QUrl
                    from PySide6.QtGui import QDesktopServices
                    QDesktopServices.openUrl(QUrl(url))
        else:
            w = MessageBox("已是最新版本", f"当前版本 {Updater.CURRENT_VERSION} 已是最新，无需更新。", self.window())
            w.exec()
