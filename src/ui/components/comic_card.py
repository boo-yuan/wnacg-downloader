from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QImage, QAction, QContextMenuEvent, QColor
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QMenu
from qfluentwidgets import PrimaryPushButton, CardWidget, CaptionLabel, FluentIcon as FIF
from core.models import Comic, TaskStatus
import core.db as db
from ui.components.cover_manager import cover_manager

class ComicCard(CardWidget):
    downloadClicked = Signal(Comic)

    def __init__(self, comic: Comic, parent=None):
        super().__init__(parent)
        self.comic = comic
        self.setFixedWidth(220)
        
        self.vbox = QVBoxLayout(self)
        self.vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.vbox.setSpacing(6)
        
        self._is_selected = False
        
        # 封面图
        self.coverLabel = QLabel(self)
        self.coverLabel.setFixedSize(196, 250)
        self.coverLabel.setScaledContents(True)
        self.coverLabel.setStyleSheet("background-color: rgba(0,0,0,0.05); border-radius: 8px;")
        
        # 标题 (完整显示，自适应高度)
        self.titleLabel = QLabel(comic.title, self)
        self.titleLabel.setWordWrap(True)
        # 强制设置最小高度，防止在布局自适应时与封面发生浮动重叠
        font_metrics = self.titleLabel.fontMetrics()
        rect = font_metrics.boundingRect(0, 0, 196, 9999, Qt.TextFlag.TextWordWrap, comic.title)
        self.titleLabel.setMinimumHeight(rect.height() + 5)
        
        # 信息行
        self.infoLayout = QHBoxLayout()
        self.infoLayout.setContentsMargins(0, 0, 0, 0)
        self.picCountLabel = CaptionLabel(comic.pic_count, self)
        self.picCountLabel.setTextColor(QColor('#009faa'))
        self.dateLabel = CaptionLabel(comic.date, self)
        self.dateLabel.setTextColor(QColor('#888888'))
        self.infoLayout.addWidget(self.picCountLabel)
        self.infoLayout.addStretch(1)
        self.infoLayout.addWidget(self.dateLabel)
        
        # 一键下载按钮
        self.downloadBtn = PrimaryPushButton("一键下载", self)
        self.downloadBtn.clicked.connect(self._on_download_clicked)
        self.update_download_state()
        
        self.vbox.addWidget(self.coverLabel)
        self.vbox.addWidget(self.titleLabel)
        self.vbox.addLayout(self.infoLayout)
        self.vbox.addWidget(self.downloadBtn)
        
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

    def update_download_state(self):
        task = db.get_task_by_aid(self.comic.aid)
        if task:
            if task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING, TaskStatus.PAUSED):
                self.downloadBtn.setText("已在队列")
                self.downloadBtn.setEnabled(False)
            elif task.status == TaskStatus.COMPLETED:
                self.downloadBtn.setText("已下载")
                self.downloadBtn.setEnabled(False)
            else: # FAILED or CANCELED
                self.downloadBtn.setText("重新下载")
                self.downloadBtn.setEnabled(True)
        else:
            self.downloadBtn.setText("一键下载")
            self.downloadBtn.setEnabled(True)

    def setSelected(self, selected: bool):
        if self._is_selected == selected:
            return
        self._is_selected = selected
        if selected:
            self.setStyleSheet("ComicCard { border: 2px solid #009faa; background-color: rgba(0, 159, 170, 0.1); border-radius: 8px; }")
        else:
            self.setStyleSheet("")
            
    def contextMenuEvent(self, event: QContextMenuEvent):
        # Notify parent to handle context menu so we can do bulk actions on all selected items
        self.parent().customContextMenuRequested.emit(self.mapToParent(event.pos()))

    def _on_download_clicked(self):
        self.downloadBtn.setText("已添加队列")
        self.downloadBtn.setEnabled(False)
        self.downloadClicked.emit(self.comic)
