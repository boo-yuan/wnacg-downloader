from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QImage, QAction, QContextMenuEvent, QColor
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QMenu
from qfluentwidgets import PrimaryPushButton, ElevatedCardWidget, CaptionLabel, ThemeColor
from core.models import Comic, TaskStatus
import core.db as db
from ui.components.cover_manager import cover_manager

class ComicCard(ElevatedCardWidget):
    downloadClicked = Signal(Comic)

    def __init__(self, comic: Comic, parent=None):
        super().__init__(parent)
        self.comic = comic
        self.setFixedWidth(220)
        
        self.vbox = QVBoxLayout(self)
        self.vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.vbox.setSpacing(6)
        self.vbox.setContentsMargins(12, 12, 12, 12)
        
        self._is_selected = False
        
        # 封面图
        self.coverLabel = QLabel(self)
        self.coverLabel.setFixedSize(196, 250)
        self.coverLabel.setScaledContents(True)
        self.coverLabel.setStyleSheet("background-color: rgba(0,0,0,0.05); border-radius: 8px;")
        
        # 标题 (完整显示，自适应高度)
        self.titleLabel = QLabel(comic.title, self)
        self.titleLabel.setWordWrap(True)
        self.titleLabel.setFixedWidth(196)
        # 强制设置固定高度，防止在布局自适应时与封面发生浮动重叠
        font_metrics = self.titleLabel.fontMetrics()
        rect = font_metrics.boundingRect(0, 0, 196, 9999, Qt.TextFlag.TextWordWrap, comic.title)
        title_h = rect.height() + 5
        self.titleLabel.setFixedHeight(title_h)
        
        # 信息行
        self.infoLayout = QHBoxLayout()
        self.infoLayout.setContentsMargins(0, 0, 0, 0)
        self.picCountLabel = CaptionLabel(comic.pic_count, self)
        self.picCountLabel.setFixedHeight(20)
        self.picCountLabel.setTextColor(ThemeColor.PRIMARY.color())
        self.dateLabel = CaptionLabel(comic.date, self)
        self.dateLabel.setFixedHeight(20)
        self.dateLabel.setTextColor(QColor('#888888'))
        self.infoLayout.addWidget(self.picCountLabel)
        self.infoLayout.addStretch(1)
        self.infoLayout.addWidget(self.dateLabel)
        
        # 一键下载按钮 / 打开文件夹按钮
        from qfluentwidgets import PushButton, FluentIcon as FIF
        self.downloadBtn = PrimaryPushButton("一键下载", self)
        self.downloadBtn.setFixedHeight(32)
        self.downloadBtn.clicked.connect(self._on_download_clicked)
        
        self.openBtn = PushButton(FIF.FOLDER, "打开文件夹", self)
        self.openBtn.setFixedHeight(32)
        self.openBtn.clicked.connect(self._on_open_clicked)
        self.openBtn.setVisible(False)
        
        self.vbox.addWidget(self.coverLabel)
        self.vbox.addWidget(self.titleLabel)
        self.vbox.addLayout(self.infoLayout)
        self.vbox.addWidget(self.downloadBtn)
        self.vbox.addWidget(self.openBtn)
        
        total_h = 12 + 250 + 6 + title_h + 6 + 20 + 6 + 32 + 12
        self.setFixedHeight(total_h)
        
        self.update_download_state()
        
        self.loader = None
        if self.comic.cover_url:
            self._load_cover()
            
    def _load_cover(self):
        cover_manager.load(self.comic.cover_url, self._set_cover)
        
    def _set_cover(self, url: str, img: QImage):
        if url != self.comic.cover_url:
            return
        try:
            if not img.isNull():
                self.coverLabel.setPixmap(QPixmap.fromImage(img))
        except RuntimeError:
            pass

    def _get_save_path(self):
        from core.config import cfg
        from pathlib import Path
        name = self.comic.title
        invalid_chars = '<>:"/\\|?*'
        for c in invalid_chars:
            name = name.replace(c, '')
        name = name.strip().rstrip('.')
        return Path(cfg.download_dir) / name

    def update_download_state(self):
        task = db.get_task_by_aid(self.comic.aid)
        save_path = self._get_save_path()
        
        is_downloaded_on_disk = False
        if save_path.exists():
            try:
                import re
                m = re.search(r'(\d+)', self.comic.pic_count) if self.comic.pic_count else None
                if m:
                    expected_count = int(m.group(1))
                    actual_count = sum(1 for f in save_path.iterdir() if f.is_file())
                    if actual_count >= expected_count and expected_count > 0:
                        is_downloaded_on_disk = True
                else:
                    is_downloaded_on_disk = any(save_path.iterdir())
            except Exception:
                pass
                
        if not is_downloaded_on_disk and save_path.with_suffix('.zip').exists():
            is_downloaded_on_disk = True
        
        state = "download"
        if task:
            if task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING, TaskStatus.PAUSED):
                state = "queued"
            elif task.status == TaskStatus.COMPLETED:
                state = "downloaded"
            else:
                if is_downloaded_on_disk:
                    state = "downloaded"
                else:
                    state = "download"
        else:
            if is_downloaded_on_disk:
                state = "downloaded"
            else:
                state = "download"
                
        if state == "queued":
            self.downloadBtn.setVisible(True)
            self.openBtn.setVisible(False)
            self.downloadBtn.setText("已在队列")
            self.downloadBtn.setEnabled(False)
        elif state == "downloaded":
            self.downloadBtn.setVisible(False)
            self.openBtn.setVisible(True)
        else:
            self.downloadBtn.setVisible(True)
            self.openBtn.setVisible(False)
            self.downloadBtn.setText("一键下载" if not task else "重新下载")
            self.downloadBtn.setEnabled(True)

    def _on_open_clicked(self):
        import os, platform
        from pathlib import Path
        path = self._get_save_path()
        
        target_path = path
        if not path.exists() and path.with_suffix('.zip').exists():
            target_path = path.with_suffix('.zip')
            
        if not target_path.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path = target_path.parent
            
        if platform.system() == 'Windows':
            if target_path.is_file():
                import subprocess
                subprocess.run(['explorer', '/select,', str(target_path)])
            else:
                os.startfile(str(target_path))
        elif platform.system() == 'Darwin':
            import subprocess
            subprocess.run(['open', '-R' if target_path.is_file() else '', str(target_path)])
        else:
            import subprocess
            subprocess.run(['xdg-open', str(target_path.parent if target_path.is_file() else target_path)])

    def setSelected(self, selected: bool):
        if self._is_selected == selected:
            return
        self._is_selected = selected
        if selected:
            primary = ThemeColor.PRIMARY.color().name()
            self.setStyleSheet(f"ComicCard {{ border: 2px solid {primary}; background-color: {primary}1A; border-radius: 8px; }}")
        else:
            self.setStyleSheet("")
            
    def contextMenuEvent(self, event: QContextMenuEvent):
        # Notify parent to handle context menu so we can do bulk actions on all selected items
        self.parent().customContextMenuRequested.emit(self.mapToParent(event.pos()))

    def _on_download_clicked(self):
        self.downloadBtn.setText("已添加队列")
        self.downloadBtn.setEnabled(False)
        self.downloadClicked.emit(self.comic)
