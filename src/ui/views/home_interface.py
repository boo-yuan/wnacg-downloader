import asyncio
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel
from qfluentwidgets import SearchLineEdit, FlowLayout, PushButton, InfoBar, InfoBarPosition, CommandBar
from core.crawler import WnacgCrawler
from core.downloader import downloader_manager
from core.models import Comic
from ui.components.comic_card import ComicCard
from ui.components.cover_manager import cover_manager
from ui.components.selectable_container import SelectableContainer
from PySide6.QtGui import QAction, QShortcut, QKeySequence
from PySide6.QtWidgets import QMenu

class SearchWorker(QThread):
    result_signal = Signal(str, list, int, int) # keyword, results, total_pages, page
    error_signal = Signal(str, str, int) # keyword, error_message, page
    
    def __init__(self, keyword, page, delay=0.0):
        super().__init__()
        self.keyword = keyword
        self.page = page
        self.delay = delay
        
    def run(self):
        try:
            if self.delay > 0:
                import time
                time.sleep(self.delay)
            results, total_pages = WnacgCrawler.search_sync(self.keyword, self.page)
            self.result_signal.emit(self.keyword, results, total_pages, self.page)
        except Exception as e:
            self.error_signal.emit(self.keyword, str(e), self.page)

class HomeInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("HomeInterface")
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(24, 24, 24, 24)
        
        self._search_cache = {}
        self._preloading_pages = set()
        self.workers = {}
        
        # 顶部搜索栏
        self.searchBar = SearchLineEdit(self)
        self.searchBar.setPlaceholderText("输入漫画名称进行搜索...")
        self.searchBar.setFixedWidth(400)
        self.searchBar.searchSignal.connect(self.do_search)
        self.searchBar.returnPressed.connect(lambda: self.do_search(self.searchBar.text()))
        
        topLayout = QHBoxLayout()
        topLayout.addWidget(self.searchBar, 0, Qt.AlignmentFlag.AlignCenter)
        self.vbox.addLayout(topLayout)
        
        # 中间滚动区域与流式布局 (展示卡片)
        self.scrollArea = QScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        self.scrollWidget = SelectableContainer()
        self.scrollWidget.setStyleSheet("background: transparent;")
        self.scrollWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scrollWidget.customContextMenuRequested.connect(self._show_context_menu)
        
        self.flowLayout = FlowLayout(self.scrollWidget, needAni=True)
        self.scrollArea.setWidget(self.scrollWidget)
        
        # Shortcut for Ctrl+A
        self.shortcut_select_all = QShortcut(QKeySequence("Ctrl+A"), self)
        self.shortcut_select_all.activated.connect(self.scrollWidget.select_all)
        
        self.vbox.addWidget(self.scrollArea)
        downloader_manager.signals.task_added.connect(self._on_task_state_changed)
        downloader_manager.signals.task_status_changed.connect(self._on_task_state_changed)
        self._init_bottom_layout()
        
    def _on_task_state_changed(self, *args):
        for i in range(self.flowLayout.count()):
            item = self.flowLayout.itemAt(i)
            widget = item.widget() if hasattr(item, 'widget') else item
            if widget and hasattr(widget, 'update_download_state'):
                widget.update_download_state()
        
    def _init_bottom_layout(self):
        self.bottomWidget = QWidget(self)
        self.paginationLayout = QHBoxLayout(self.bottomWidget)
        self.paginationLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.vbox.addWidget(self.bottomWidget)
        self.bottomWidget.setVisible(False)
        
        self.current_keyword = ""
        self.current_page = 1
        self.total_pages = 1
        self.worker = None

    def _update_pagination(self):
        # Clear existing
        while self.paginationLayout.count():
            item = self.paginationLayout.takeAt(0)
            w = item.widget()
            if w: w.deleteLater()
            
        if self.total_pages <= 1:
            self.bottomWidget.setVisible(False)
            return
            
        self.bottomWidget.setVisible(True)
        
        pages_to_show = set()
        pages_to_show.add(1)
        pages_to_show.add(self.total_pages)
        for i in range(max(1, self.current_page - 2), min(self.total_pages, self.current_page + 2) + 1):
            pages_to_show.add(i)
            
        sorted_pages = sorted(list(pages_to_show))
        
        prevBtn = PushButton("<", self.bottomWidget)
        prevBtn.setEnabled(self.current_page > 1)
        prevBtn.clicked.connect(self.prev_page)
        self.paginationLayout.addWidget(prevBtn)
        
        last_p = 0
        visible_pages = []
        for p in sorted_pages:
            if p - last_p > 1:
                dots = QLabel("...", self.bottomWidget)
                dots.setAlignment(Qt.AlignmentFlag.AlignCenter)
                dots.setFixedWidth(24)
                self.paginationLayout.addWidget(dots)
                
            text = str(p)
            btn = PushButton(text, self.bottomWidget)
            w = max(36, 20 + len(text) * 8)
            btn.setFixedSize(w, 32)
            
            if p == self.current_page:
                btn.setStyleSheet("background-color: #009faa; color: white; border: none; border-radius: 4px;")
            else:
                visible_pages.append(p)
                
            btn.clicked.connect(lambda checked=False, page=p: self.go_to_page(page))
            self.paginationLayout.addWidget(btn)
            last_p = p
            
        nextBtn = PushButton(">", self.bottomWidget)
        nextBtn.setEnabled(self.current_page < self.total_pages)
        nextBtn.clicked.connect(self.next_page)
        self.paginationLayout.addWidget(nextBtn)
        
        for p in visible_pages:
            self._preload_page(p)

    def _preload_page(self, page):
        cache_key = (self.current_keyword, page)
        if cache_key in self._search_cache or cache_key in self._preloading_pages:
            return
            
        self._preloading_pages.add(cache_key)
        import random
        delay = random.uniform(1.0, 2.5)
        w = SearchWorker(self.current_keyword, page, delay)
        w.result_signal.connect(self._on_preload_result)
        w.error_signal.connect(self._on_preload_error)
        w.finished.connect(w.deleteLater)
        w.finished.connect(lambda k=cache_key: self.workers.pop(k, None))
        self.workers[cache_key] = w
        w.start()

    def _on_preload_result(self, keyword, results, total_pages, page):
        if keyword != self.current_keyword:
            return
            
        if not results and page > 1:
            total_pages = min(total_pages, page - 1)
            self.total_pages = min(self.total_pages, page - 1)
            self._update_pagination()
            
        cache_key = (self.current_keyword, page)
        self._search_cache[cache_key] = (results, total_pages)
        if cache_key in self._preloading_pages:
            self._preloading_pages.remove(cache_key)
            
    def _on_preload_error(self, keyword, err_msg, page):
        if keyword != self.current_keyword:
            return
            
        cache_key = (self.current_keyword, page)
        if cache_key in self._preloading_pages:
            self._preloading_pages.remove(cache_key)

    def do_search(self, keyword: str):
        if not keyword.strip(): return
        self.current_keyword = keyword
        self.current_page = 1
        self._search_cache.clear()
        self._load_data()
        
    def go_to_page(self, page):
        self.current_page = page
        self._load_data()
        
    def prev_page(self):
        if self.current_page > 1:
            self.current_page -= 1
            self._load_data()
            
    def next_page(self):
        if not self.current_keyword: return
        self.current_page += 1
        self._load_data()
        
    def _load_data(self):
        self.searchBar.setEnabled(False)
        self.bottomWidget.setEnabled(False)
        
        while self.flowLayout.count():
            item = self.flowLayout.takeAt(0)
            widget = item.widget() if hasattr(item, 'widget') else item
            if widget:
                widget.deleteLater()
                
        cache_key = (self.current_keyword, self.current_page)
        if cache_key in self._search_cache:
            results, total_pages = self._search_cache[cache_key]
            self._on_search_result(self.current_keyword, results, total_pages, self.current_page)
            return
        elif cache_key in self.workers:
            # wait for preload to finish
            w = self.workers[cache_key]
            w.result_signal.disconnect()
            w.error_signal.disconnect()
            w.result_signal.connect(self._on_search_result)
            w.error_signal.connect(self._on_search_error)
            if cache_key in self._preloading_pages:
                self._preloading_pages.remove(cache_key)
        else:
            self.worker = SearchWorker(self.current_keyword, self.current_page)
            self.worker.result_signal.connect(self._on_search_result)
            self.worker.error_signal.connect(self._on_search_error)
            self.worker.finished.connect(self.worker.deleteLater)
            # No need to add to workers dict because it's stored in self.worker
            self.worker.start()

    def _on_search_result(self, keyword, results, total_pages, page):
        if keyword != self.current_keyword:
            return
            
        self.worker = None
        self.searchBar.setEnabled(True)
        self.bottomWidget.setEnabled(True)
        
        if not results and page > 1:
            total_pages = min(total_pages, page - 1)
            
        # update cache
        cache_key = (self.current_keyword, page)
        self._search_cache[cache_key] = (results, total_pages)
        
        # If user rapidly clicked away, ignore this result rendering
        if page != self.current_page:
            return
            
        self.total_pages = total_pages
        self._update_pagination()
        
        if not results:
            InfoBar.warning("搜索结束", "未能找到相关漫画或已经是最后一页", parent=self, position=InfoBarPosition.TOP_RIGHT)
            
        for comic in results:
            card = ComicCard(comic, self.scrollWidget)
            card.downloadClicked.connect(self._on_download_clicked)
            self.flowLayout.addWidget(card)
            
    def _on_search_error(self, keyword, err_msg, page):
        if keyword != self.current_keyword:
            return
            
        self.worker = None
        if page != self.current_page: return
        self.searchBar.setEnabled(True)
        self.bottomWidget.setEnabled(True)
        InfoBar.error("搜索失败", f"网络请求失败：{err_msg}", parent=self, position=InfoBarPosition.TOP_RIGHT)

    def _on_download_clicked(self, comic: Comic):
        downloader_manager.add_task(comic)
        InfoBar.success("已加入下载队列", f"正在后台处理: {comic.title}", parent=self, position=InfoBarPosition.TOP_RIGHT)

    def _show_context_menu(self, pos):
        selected_items = self.scrollWidget.get_selected_items()
        if not selected_items:
            return
            
        menu = QMenu(self)
        
        action_add = QAction(f"加入任务队列 ({len(selected_items)}项)", self)
        action_add.triggered.connect(lambda: self._bulk_download(selected_items))
        menu.addAction(action_add)
        
        action_deselect = QAction("取消选中", self)
        action_deselect.triggered.connect(self.scrollWidget.clear_selection)
        menu.addAction(action_deselect)
        
        # Determine global pos from scroll widget pos
        global_pos = self.scrollWidget.mapToGlobal(pos)
        menu.exec(global_pos)
        
    def _bulk_download(self, selected_items):
        for card in selected_items:
            if hasattr(card, 'comic') and card.downloadBtn.isEnabled():
                card.downloadBtn.setText("已添加队列")
                card.downloadBtn.setEnabled(False)
                downloader_manager.add_task(card.comic)
        
        self.scrollWidget.clear_selection()
        InfoBar.success("批量操作成功", f"已将 {len(selected_items)} 部漫画加入下载队列", parent=self, position=InfoBarPosition.TOP_RIGHT)

