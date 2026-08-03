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
    InfoBar,
    InfoBarPosition,
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
            self.finished_signal.emit({"has_update": False, "error": str(e)})

class NetworkTestWorker(QThread):
    finished_signal = Signal(bool, str)
    
    def run(self):
        try:
            import time
            from curl_cffi.requests import Session
            
            kwargs = {
                "impersonate": "chrome120",
                "timeout": 15
            }
            if cfg.proxy_mode == "custom":
                kwargs["proxies"] = cfg.curl_cffi_proxies
            elif cfg.proxy_mode == "direct":
                kwargs["trust_env"] = False
            else:
                kwargs["trust_env"] = True
                
            start_time = time.time()
            with Session(**kwargs) as s:
                domain = cfg.domain if cfg.domain.startswith('http') else f"https://{cfg.domain}"
                r = s.get(domain)
                
            elapsed = time.time() - start_time
            if r.status_code == 200:
                self.finished_signal.emit(True, f"连接成功！耗时: {elapsed:.2f}s")
            else:
                self.finished_signal.emit(False, f"HTTP状态码异常: {r.status_code}")
        except Exception as e:
            self.finished_signal.emit(False, f"连接失败: {str(e)}")

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
        self.proxyGroup = SettingCardGroup("网络与代理", self.scrollWidget)
        
        self.proxyModeCard = ComboBoxSettingCard(
            icon=FIF.GLOBE,
            title="代理模式",
            content="选择连接网络的方式",
            texts=["跟随系统代理", "直接连接", "手动配置代理"],
            parent=self.proxyGroup
        )
        mode_map = {ProxyMode.SYSTEM: 0, ProxyMode.DIRECT: 1, ProxyMode.CUSTOM: 2}
        self.proxyModeCard.comboBox.setCurrentIndex(mode_map.get(cfg.proxy_mode, 0))
        self.proxyModeCard.comboBox.currentIndexChanged.connect(self._on_proxy_mode_changed)

        self.customProxyCard = LineEditSettingCard(
            icon=FIF.LINK,
            title="手动代理地址",
            content="填写代理服务器地址（如：http://127.0.0.1:7890）",
            parent=self.proxyGroup
        )
        self.customProxyCard.lineEdit.setText(cfg.custom_proxy)
        self.customProxyCard.lineEdit.textChanged.connect(self._on_custom_proxy_changed)
        
        self.testNetworkCard = PushSettingCard(
            text="开始测试",
            icon=FIF.SEND,
            title="测试网络连接",
            content="检查当前网络能否顺利访问漫画网站",
            parent=self.proxyGroup
        )
        self.testNetworkCard.clicked.connect(self._test_network)
        
        self.domainCard = EditableComboBoxSettingCard(
            icon=FIF.GLOBE,
            title="WNACG 主域名",
            content="如果无法访问，请尝试切换至下拉列表中的其他域名",
            texts=cfg.backup_domains,
            parent=self.proxyGroup
        )
        self.domainCard.comboBox.setText(cfg.domain)
        self.domainCard.comboBox.textChanged.connect(self._on_domain_changed)
        if hasattr(self.domainCard.comboBox, 'currentTextChanged'):
            self.domainCard.comboBox.currentTextChanged.connect(self._on_domain_changed)
        
        self.fetchDomainCard = PushSettingCard(
            text="获取",
            icon=FIF.SYNC,
            title="获取最新域名",
            content="自动拉取官方最新的防屏蔽域名，并加入到上方列表",
            parent=self.proxyGroup
        )
        self.fetchDomainCard.clicked.connect(self._fetch_latest_domains)
        
        self.proxyGroup.addSettingCard(self.domainCard)
        self.proxyGroup.addSettingCard(self.fetchDomainCard)
        self.proxyGroup.addSettingCard(self.proxyModeCard)
        self.proxyGroup.addSettingCard(self.customProxyCard)
        self.proxyGroup.addSettingCard(self.testNetworkCard)
        
        self.expandLayout.addWidget(self.proxyGroup)
        
    def _test_network(self):
        self.testNetworkCard.button.setEnabled(False)
        self.testNetworkCard.button.setText("测试中...")
        
        self.test_worker = NetworkTestWorker()
        self.test_worker.finished_signal.connect(self._on_test_network_finished)
        self.test_worker.start()
        
    def _on_test_network_finished(self, success: bool, msg: str):
        self.testNetworkCard.button.setEnabled(True)
        self.testNetworkCard.button.setText("开始测试")
        if success:
            InfoBar.success("⚡ 网络测试通过", "连接非常顺畅，这套代理或域名没问题！", parent=self.window(), position=InfoBarPosition.TOP_RIGHT)
        else:
            InfoBar.error("❌ 无法连接到网站", "可能是代理没配对，或者当前的 WNACG 域名失效了", parent=self.window(), position=InfoBarPosition.TOP_RIGHT)
        
    def _init_system_settings(self):
        self.concurrentGroup = SettingCardGroup("下载性能调节", self.scrollWidget)
        
        self.maxTasksCard = SpinBoxSettingCard(
            icon=FIF.CLOUD_DOWNLOAD,
            title="同时下载的任务数",
            content="允许同时下载多少部漫画（推荐 1-5，避免卡顿）",
            parent=self.concurrentGroup
        )
        self.maxTasksCard.spinBox.setRange(1, 10)
        self.maxTasksCard.spinBox.setValue(cfg.max_concurrent_tasks)
        self.maxTasksCard.spinBox.valueChanged.connect(self._on_max_tasks_changed)
        
        self.globalConnectionsCard = SpinBoxSettingCard(
            icon=FIF.IOT,
            title="图片并发下载数",
            content="所有任务加起来最多同时下载多少张图片（推荐 4-16）",
            parent=self.concurrentGroup
        )
        self.globalConnectionsCard.spinBox.setRange(1, 32)
        self.globalConnectionsCard.spinBox.setValue(cfg.global_max_connections)
        self.globalConnectionsCard.spinBox.valueChanged.connect(self._on_global_connections_changed)
        
        self.downloadDelayCard = DoubleSpinBoxSettingCard(
            icon=FIF.HISTORY,
            title="下载防封禁延迟",
            content="每下载完一张图片后暂停几秒，防止被网站拉黑封禁",
            parent=self.concurrentGroup
        )
        self.downloadDelayCard.spinBox.setRange(0.0, 10.0)
        self.downloadDelayCard.spinBox.setSingleStep(0.5)
        self.downloadDelayCard.spinBox.setValue(cfg.download_delay)
        self.downloadDelayCard.spinBox.valueChanged.connect(self._on_download_delay_changed)
        
        self.globalSpeedLimitCard = SpinBoxSettingCard(
            icon=FIF.SPEED_OFF,
            title="最高下载速度限制 (KB/s)",
            content="填 0 表示完全不限速",
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
            InfoBar.error("❌ 获取失败", "无法从发布页获取数据，请检查网络", parent=self.window(), position=InfoBarPosition.TOP)
            return
            
        existing = [self.domainCard.comboBox.itemText(i) for i in range(self.domainCard.comboBox.count())]
        added = 0
        for d in domains:
            if d not in existing:
                self.domainCard.comboBox.addItem(d)
                existing.append(d)
                added += 1
                
        if added > 0:
            cfg.backup_domains = existing
            cfg.save()
            
        from core.crawler import WnacgCrawler
        WnacgCrawler._mirrors = existing
                
        if added > 0:
            InfoBar.success("📡 域名拉取成功", f"最新的防屏蔽地址已装填，新增 {added} 个域名", parent=self.window(), position=InfoBarPosition.TOP)
        else:
            InfoBar.warning("⚡ 提示", "当前备用域名列表已经是最新，无需更新", parent=self.window(), position=InfoBarPosition.TOP)


class DownloadSettingInterface(BaseSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("DownloadSettingInterface")
        self._init_download_settings()

    def _init_download_settings(self):
        self.sysGroup = SettingCardGroup("下载与存储", self.scrollWidget)
        
        self.downloadDirCard = PushSettingCard(
            text="选择文件夹",
            icon=FIF.FOLDER,
            title="保存到电脑的位置",
            content=str(Path(cfg.download_dir).absolute()),
            parent=self.sysGroup
        )
        self.downloadDirCard.clicked.connect(self._on_download_dir_clicked)
        
        self.downloadNamingCard = ComboBoxSettingCard(
            icon=FIF.EDIT,
            title="图片命名规则",
            content="选择保存图片时的文件名格式",
            texts=["原始名称", "顺序数字 (001, 002...)"],
            parent=self.sysGroup
        )
        naming_map = {"original": 0, "sequential": 1}
        self.downloadNamingCard.comboBox.setCurrentIndex(naming_map.get(cfg.download_naming, 0))
        self.downloadNamingCard.comboBox.currentIndexChanged.connect(self._on_download_naming_changed)

        self.downloadFormatCard = ComboBoxSettingCard(
            icon=FIF.PHOTO,
            title="强制转换图片格式",
            content="将下载的图片统一转为指定格式（选“原始格式”则不转换）",
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
            title="下载后打包为 ZIP",
            content="漫画下载完毕后，自动将其压缩成一个 ZIP 压缩包",
            parent=self.sysGroup
        )
        self.packZipCard.setChecked(cfg.pack_to_zip)
        self.packZipCard.checkedChanged.connect(self._on_pack_zip_changed)
        
        self.deleteOriginalCard = MySwitchSettingCard(
            icon=FIF.DELETE,
            title="打包后清理图片文件夹",
            content="ZIP 创建成功后，自动删除原本的散图文件夹，节省空间",
            parent=self.sysGroup
        )
        self.deleteOriginalCard.setChecked(cfg.delete_original_after_pack)
        self.deleteOriginalCard.checkedChanged.connect(self._on_delete_original_changed)
        self.deleteOriginalCard.setEnabled(cfg.pack_to_zip)

        self.sysGroup.addSettingCard(self.packZipCard)
        self.sysGroup.addSettingCard(self.deleteOriginalCard)
        
        self.autoStartCard = MySwitchSettingCard(
            icon=FIF.PLAY,
            title="添加漫画后自动开始",
            content="关闭此项后，新添加的漫画默认处于暂停状态，需手动点击开始",
            parent=self.sysGroup
        )
        self.autoStartCard.setChecked(cfg.auto_start_download)
        self.autoStartCard.checkedChanged.connect(self._on_auto_start_changed)
        
        self.sysGroup.addSettingCard(self.autoStartCard)
        self.expandLayout.addWidget(self.sysGroup)
        
        # 行为与提示设置
        self.promptGroup = SettingCardGroup("弹窗与提醒", self.scrollWidget)
        
        self.showCancelPromptCard = MySwitchSettingCard(
            icon=FIF.CANCEL,
            title="取消下载时二次确认",
            content="弹出确认框防止手滑点错。若之前勾选过“不再提示”，可在此重新打开",
            parent=self.promptGroup
        )
        self.showCancelPromptCard.setChecked(cfg.show_cancel_prompt)
        self.showCancelPromptCard.checkedChanged.connect(self._on_show_cancel_prompt_changed)
        
        self.deleteFilesOnCancelCard = MySwitchSettingCard(
            icon=FIF.DELETE,
            title="取消下载时清除已下载文件",
            content="自动删除下到一半的残缺图片和文件夹，保持硬盘干净",
            parent=self.promptGroup
        )
        self.deleteFilesOnCancelCard.setChecked(cfg.delete_files_on_cancel)
        self.deleteFilesOnCancelCard.checkedChanged.connect(self._on_delete_files_on_cancel_changed)
        
        self.promptGroup.addSettingCard(self.showCancelPromptCard)
        self.promptGroup.addSettingCard(self.deleteFilesOnCancelCard)
        self.expandLayout.addWidget(self.promptGroup)

    def _on_show_cancel_prompt_changed(self, checked: bool):
        cfg.show_cancel_prompt = checked
        cfg.save()
        
    def _on_delete_files_on_cancel_changed(self, checked: bool):
        cfg.delete_files_on_cancel = checked
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
        
    def _on_pack_zip_changed(self, checked: bool):
        cfg.pack_to_zip = checked
        cfg.save()
        self.deleteOriginalCard.setEnabled(checked)
        
    def _on_delete_original_changed(self, checked: bool):
        cfg.delete_original_after_pack = checked
        cfg.save()
        
    def _on_download_dir_clicked(self):
        directory = QFileDialog.getExistingDirectory(self, "选择下载保存目录", cfg.download_dir)
        if directory:
            cfg.download_dir = directory
            cfg.save()
            self.downloadDirCard.setContent(str(Path(directory).absolute()))
            



class AboutSettingInterface(BaseSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("AboutSettingInterface")
        self._init_about_settings()

    def _init_about_settings(self):
        self.aboutGroup = SettingCardGroup("系统与关于", self.scrollWidget)
        
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
        
        self.closeActionCard = ComboBoxSettingCard(
            icon=FIF.CLOSE,
            title="关闭主窗口时的行为",
            content="点击右上角关闭(X)按钮时，程序的响应方式",
            texts=["每次询问我 (弹出二次确认)", "最小化到系统托盘 (保持后台运行)", "直接彻底退出程序"],
            parent=self.aboutGroup
        )
        if cfg.show_close_prompt:
            idx = 0
        elif cfg.close_to_tray:
            idx = 1
        else:
            idx = 2
        self.closeActionCard.comboBox.setCurrentIndex(idx)
        self.closeActionCard.comboBox.currentIndexChanged.connect(self._on_close_action_changed)
        
        self.aboutGroup.addSettingCard(self.closeActionCard)
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
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_path)))
            
    def _on_close_action_changed(self, index: int):
        if index == 0:
            cfg.show_close_prompt = True
        elif index == 1:
            cfg.show_close_prompt = False
            cfg.close_to_tray = True
        elif index == 2:
            cfg.show_close_prompt = False
            cfg.close_to_tray = False
        cfg.save()

    def _check_update(self):
        self.updateCard.button.setText("检查中...")
        self.updateCard.button.setEnabled(False)
        self.updateWorker = UpdateCheckWorker(self)
        self.updateWorker.finished_signal.connect(self._on_update_checked)
        self.updateWorker.finished.connect(self.updateWorker.deleteLater)
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
