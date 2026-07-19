from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QFileDialog
from qfluentwidgets import (SettingCardGroup, SettingCard, LineEdit, ComboBox, PushSettingCard,
                            EditableComboBox, FluentIcon as FIF, ScrollArea, ExpandLayout,
                            SwitchButton)
import os
import asyncio
from pathlib import Path
from bs4 import BeautifulSoup
from curl_cffi.requests import Session
from core.config import cfg, ProxyMode

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
                    for a in soup.find_all('a'):
                        text = a.text.replace('\xa0', ' ').strip()
                        if text.startswith("www.") and text not in domains and "google" not in text:
                            domains.append(text)
        except Exception:
            pass
        return domains

class SettingInterface(ScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("SettingInterface")
        
        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)
        
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea, .QScrollArea > QWidget > QWidget { background: transparent; }")
        
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
        self.sysGroup = SettingCardGroup("下载与系统设置", self.scrollWidget)
        
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
        
        self.autoStartCard = MySwitchSettingCard(
            icon=FIF.PLAY,
            title="添加任务后立即下载",
            content="关闭后任务将会保持为等待状态，需手动开始",
            parent=self.sysGroup
        )
        self.autoStartCard.setChecked(cfg.auto_start_download)
        self.autoStartCard.checkedChanged.connect(self._on_auto_start_changed)
        
        self.sysGroup.addSettingCard(self.autoStartCard)
        
        self.logCard = PushSettingCard(
            text="查看日志",
            icon=FIF.DOCUMENT,
            title="程序运行日志",
            content="记录了程序的错误和重点信息",
            parent=self.sysGroup
        )
        self.logCard.clicked.connect(self._open_log_file)
        
        self.domainCard = EditableComboBoxSettingCard(
            icon=FIF.GLOBE,
            title="站点域名",
            content="常被墙可随时更换 (正在从发布页获取最新域名...)",
            texts=["www.wnacg.ru", "www.wnacg.com"],
            parent=self.sysGroup
        )
        self.domainCard.comboBox.setText(cfg.domain)
        self.domainCard.comboBox.textChanged.connect(self._on_domain_changed)
        
        self.sysGroup.addSettingCard(self.domainCard)
        self.sysGroup.addSettingCard(self.logCard)
        self.expandLayout.addWidget(self.sysGroup)
        
        # 启动后台域名抓取
        self.fetch_worker = DomainFetchWorker(self)
        self.fetch_worker.finished_signal.connect(self._on_domains_fetched)
        self.fetch_worker.finished.connect(self.fetch_worker.deleteLater)
        self.fetch_worker.start()
        
    def _on_domains_fetched(self, domains):
        self.domainCard.setContent("常被墙可随时更换 (获取最新域名请访问发布页 https://wnacg01.link/)")
        existing = [self.domainCard.comboBox.itemText(i) for i in range(self.domainCard.comboBox.count())]
        for d in domains:
            if d not in existing:
                self.domainCard.comboBox.addItem(d)
        
    def _open_log_file(self):
        log_path = Path("app.log").absolute()
        if not log_path.exists():
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("暂无日志记录\n")
        os.startfile(log_path)
        
    def _on_proxy_mode_changed(self, index: int):
        modes = [ProxyMode.SYSTEM, ProxyMode.DIRECT, ProxyMode.CUSTOM]
        cfg.proxy_mode = modes[index]
        cfg.save()
        
    def _on_custom_proxy_changed(self, text: str):
        cfg.custom_proxy = text
        cfg.save()
        
    def _on_domain_changed(self, text: str):
        cfg.domain = text
        cfg.save()
        
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
        
    def _on_download_dir_clicked(self):
        directory = QFileDialog.getExistingDirectory(self, "选择下载保存目录", cfg.download_dir)
        if directory:
            cfg.download_dir = directory
            cfg.save()
            self.downloadDirCard.setContent(str(Path(directory).absolute()))
