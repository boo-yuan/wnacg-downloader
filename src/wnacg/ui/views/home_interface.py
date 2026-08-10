"""Search, pagination, preload, and gallery-card presentation."""
# pyright: reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import time
from pathlib import Path
from typing import cast

from PySide6.QtCore import QPoint, QSemaphore, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence, QResizeEvent, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    CommandBar,
    FlowLayout,
    InfoBar,
    InfoBarPosition,
    PrimaryToolButton,
    PushButton,
    SearchLineEdit,
    qconfig,
    setFont,
)
from qfluentwidgets import FluentIcon as FIF

from wnacg.application.downloader import DownloaderWorker
from wnacg.application.ports import TaskRepository
from wnacg.domain.models import Comic, DownloadTask
from wnacg.infrastructure.crawler import WnacgCrawler
from wnacg.ui.card_state_coordinator import CardStateCoordinator
from wnacg.ui.components.comic_card import ComicCard
from wnacg.ui.components.cover_manager import CoverManagerClass
from wnacg.ui.components.loading_state import AnimatedLoadingState
from wnacg.ui.components.selectable_container import SelectableContainer
from wnacg.ui.theme import (
    accent_text_style,
    active_page_button_style,
    muted_text_style,
    round_accent_button_style,
)
from wnacg.ui.worker_lifecycle import stop_qthread


class SearchWorker(QThread):
    result_signal = Signal(str, list, int, int)  # keyword, results, total_pages, page
    error_signal = Signal(str, str, int)  # keyword, error_message, page
    _network_slots = QSemaphore(4)

    def __init__(self, keyword: str, page: int, delay: float = 0.0) -> None:
        super().__init__()
        self.keyword = keyword
        self.page = page
        self.delay = delay

    def run(self) -> None:
        if not self._network_slots.tryAcquire(1):
            self.error_signal.emit(self.keyword, "搜索任务过多，请稍后重试", self.page)
            return
        try:
            if self.delay > 0:
                remaining_milliseconds = int(self.delay * 1_000)
                while remaining_milliseconds > 0 and not self.isInterruptionRequested():
                    interval = min(100, remaining_milliseconds)
                    self.msleep(interval)
                    remaining_milliseconds -= interval
            if self.isInterruptionRequested():
                return
            results, total_pages = WnacgCrawler.search_sync(self.keyword, self.page)
            if not self.isInterruptionRequested():
                self.result_signal.emit(self.keyword, results, total_pages, self.page)
        except Exception as e:
            self.error_signal.emit(self.keyword, str(e), self.page)
        finally:
            self._network_slots.release(1)


class HomeInterface(QWidget):
    def __init__(
        self,
        downloader: DownloaderWorker,
        repository: TaskRepository,
        cover_manager: CoverManagerClass,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent=parent)
        self._downloader = downloader
        self._repository = repository
        self._cover_manager = cover_manager
        self._state_coordinator = CardStateCoordinator(repository, self)
        self._state_coordinator.signals.finished.connect(
            self._apply_card_states,
            Qt.ConnectionType.QueuedConnection,
        )
        self.setObjectName("HomeInterface")
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(24, 24, 24, 24)

        self._search_cache = {}
        self._preloading_pages = set()
        self.workers = {}
        self.card_map = {}
        self._old_workers = []
        self._pagination_buttons: list[PushButton] = []
        self._pagination_dots: list[QLabel] = []

        # Spacer for vertical centering (top)
        self.topSpacerWidget = QWidget()
        self.vbox.addWidget(self.topSpacerWidget, 1)

        # Hero header
        self.heroWidget = QWidget(self)
        heroLayout = QVBoxLayout(self.heroWidget)
        heroLayout.setContentsMargins(0, 0, 0, 0)
        heroLayout.setSpacing(12)

        from PySide6.QtGui import QPixmap
        from qfluentwidgets import SubtitleLabel, TitleLabel

        self.logoImage = QLabel(self)
        icon_path = Path(__file__).resolve().parents[2] / "resource" / "icon.png"
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            pixmap = pixmap.scaled(
                100, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            self.logoImage.setPixmap(pixmap)
        self.logoImage.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.logoLabel = TitleLabel("WNACG Downloader", self)
        self.logoLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.welcomeLabel = SubtitleLabel("开启您的漫画探索之旅", self)
        self.welcomeLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)

        heroLayout.addWidget(self.logoImage)
        heroLayout.addWidget(self.logoLabel)
        heroLayout.addWidget(self.welcomeLabel)
        heroLayout.addSpacing(32)

        self.vbox.addWidget(self.heroWidget, 0, Qt.AlignmentFlag.AlignCenter)

        # 顶部搜索栏
        self.searchBar = SearchLineEdit(self)
        self.searchBar.setPlaceholderText("输入漫画名称、aid:编号或画廊链接...")
        self.searchBar.setFixedWidth(600)
        self.searchBar.setMinimumHeight(44)
        self.searchBar.searchSignal.connect(self.do_search)
        self.searchBar.returnPressed.connect(lambda: self.do_search(self.searchBar.text()))

        topLayout = QHBoxLayout()
        topLayout.addWidget(self.searchBar, 0, Qt.AlignmentFlag.AlignCenter)
        self.vbox.addLayout(topLayout)

        # Spacer for vertical centering (bottom)
        self.bottomSpacerWidget = QWidget()
        self.vbox.addWidget(self.bottomSpacerWidget, 2)

        # Main content container (hidden initially)
        self.contentWidget = QWidget(self)
        self.contentLayout = QVBoxLayout(self.contentWidget)
        self.contentLayout.setContentsMargins(0, 0, 0, 0)

        self.commandBar = CommandBar(self)
        self.commandBar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        self.hintAction = Action(FIF.HELP, "操作提示", self)
        self.hintAction.triggered.connect(self._show_hint)
        self.commandBar.addAction(self.hintAction)

        self.commandBar.addSeparator()

        self.selectAllAction = Action(FIF.CHECKBOX, "全选", self)
        self.selectAllAction.triggered.connect(lambda: self.scrollWidget.select_all())
        self.commandBar.addAction(self.selectAllAction)

        self.addToQueueAction = Action(FIF.DOWNLOAD, "加入队列", self)
        self.addToQueueAction.triggered.connect(self._on_topbar_add_to_queue)
        self.commandBar.addAction(self.addToQueueAction)

        self.contentLayout.addWidget(self.commandBar)

        # 中间滚动区域与流式布局 (展示卡片)
        self.scrollArea = QScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.scrollWidget = SelectableContainer()
        self.scrollWidget.setStyleSheet("background: transparent;")
        self.scrollWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scrollWidget.customContextMenuRequested.connect(self._show_context_menu)

        self.flowLayout = FlowLayout(self.scrollWidget, needAni=False)
        self.scrollArea.setWidget(self.scrollWidget)

        # Shortcut for Ctrl+A
        self.shortcut_select_all = QShortcut(QKeySequence("Ctrl+A"), self)
        self.shortcut_select_all.activated.connect(self.scrollWidget.select_all)

        self.contentLayout.addWidget(self.scrollArea)
        downloader.signals.task_added.connect(self._on_task_state_changed, Qt.ConnectionType.QueuedConnection)
        downloader.signals.task_status_changed.connect(
            self._on_task_state_changed,
            Qt.ConnectionType.QueuedConnection,
        )
        downloader.signals.task_deletion_result.connect(
            self._on_task_deletion_result,
            Qt.ConnectionType.QueuedConnection,
        )
        self._init_bottom_layout()

        self._state_refresh_timer = QTimer(self)
        self._state_refresh_timer.setInterval(4_000)
        self._state_refresh_timer.timeout.connect(self.refresh_card_states)
        self._state_refresh_timer.start()

        # 返回顶部悬浮按钮
        self.backToTopBtn = PrimaryToolButton(FIF.UP, self)
        setFont(self.backToTopBtn)
        self.backToTopBtn.setFixedSize(40, 40)
        self.backToTopBtn.hide()
        self.backToTopBtn.clicked.connect(lambda: self.scrollArea.verticalScrollBar().setValue(0))
        self.scrollArea.verticalScrollBar().valueChanged.connect(self._on_scroll)
        qconfig.themeChanged.connect(self._apply_theme_colors)
        self._apply_theme_colors()

    def _apply_theme_colors(self, _theme: object | None = None) -> None:
        self.logoLabel.setStyleSheet(accent_text_style(pixel_size=28, weight=900))
        self.welcomeLabel.setStyleSheet(muted_text_style(pixel_size=15))
        self.emptyTitle.setStyleSheet(accent_text_style(pixel_size=28, weight=900))
        self.emptySubtitle.setStyleSheet(muted_text_style(pixel_size=15))
        self.backToTopBtn.setStyleSheet(round_accent_button_style())
        self.loadingWidget.refresh_theme()
        for button in self._pagination_buttons:
            if bool(button.property("currentPage")):
                button.setStyleSheet(active_page_button_style())
        for dots in self._pagination_dots:
            dots.setStyleSheet(muted_text_style())

    def _show_hint(self) -> None:
        InfoBar.info(
            title="💡 操作提示",
            content="支持鼠标框选 / Shift连选 / Ctrl+A全选\n右键卡片可进行批量下载",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self,
        )

    def _on_scroll(self, value: int) -> None:
        if value > 300:
            self.backToTopBtn.show()
        else:
            self.backToTopBtn.hide()

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        self.backToTopBtn.move(self.width() - 80, self.height() - 100)

    def _on_task_state_changed(self, *args: object) -> None:
        aid_to_update = None
        if len(args) == 1 and isinstance(args[0], DownloadTask):
            aid_to_update = args[0].comic.aid
        elif len(args) >= 1 and isinstance(args[0], str):
            task_id = args[0]
            task = self._repository.get_task(task_id)
            if task and task.comic:
                aid_to_update = task.comic.aid

        if aid_to_update is None or aid_to_update in self.card_map:
            self.refresh_card_states(force=True)

    def _on_task_deletion_result(self, _task_id: str, succeeded: bool, _error: str) -> None:
        if succeeded:
            self.refresh_card_states(force=True)

    def refresh_card_states(self, *, force: bool = False) -> None:
        """Reconcile visible buttons with persisted state and local artifacts."""
        if not force and (not self.isVisible() or not self.scrollArea.isVisible()):
            return
        self._state_coordinator.request(tuple(card.comic for card in self.card_map.values()))

    @Slot(int, object)
    def _apply_card_states(self, generation: int, raw_states: object) -> None:
        if generation != self._state_coordinator.generation or not isinstance(raw_states, dict):
            return
        for aid, state in raw_states.items():
            if not isinstance(aid, str) or not isinstance(state, str):
                continue
            card = self.card_map.get(aid)
            if card is not None:
                card.apply_download_state(state)

    def _init_bottom_layout(self) -> None:
        self._init_empty_state()
        self._init_loading_state()

        self.bottomWidget = QWidget(self)
        self.paginationLayout = QHBoxLayout(self.bottomWidget)
        self.paginationLayout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.contentLayout.addWidget(self.bottomWidget)
        self.bottomWidget.setVisible(False)

        self.vbox.addWidget(self.contentWidget)
        self.contentWidget.hide()

        self.current_keyword = ""
        self.current_page = 1
        self.total_pages = 1
        self.worker = None

    def _init_empty_state(self) -> None:
        self.emptyWidget = QWidget(self)
        emptyLayout = QVBoxLayout(self.emptyWidget)
        emptyLayout.setSpacing(12)

        from PySide6.QtGui import QPixmap
        from qfluentwidgets import SubtitleLabel, TitleLabel

        self.emptyImage = QLabel(self)
        icon_path = Path(__file__).resolve().parents[2] / "resource" / "icon.png"
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            pixmap = pixmap.scaled(
                100, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            self.emptyImage.setPixmap(pixmap)
        self.emptyImage.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.emptyTitle = TitleLabel("未找到相关漫画", self)
        self.emptyTitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.emptySubtitle = SubtitleLabel("可能是关键词有误或网络超时，请尝试换个关键词或稍后再试。", self)
        self.emptySubtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        emptyLayout.addStretch(1)
        emptyLayout.addWidget(self.emptyImage)
        emptyLayout.addWidget(self.emptyTitle)
        emptyLayout.addWidget(self.emptySubtitle)
        emptyLayout.addStretch(2)

        self.contentLayout.addWidget(self.emptyWidget)
        self.emptyWidget.hide()

    def _init_loading_state(self) -> None:
        self.loadingWidget = AnimatedLoadingState(parent=self)
        self.contentLayout.addWidget(self.loadingWidget)
        self.loadingWidget.hide()

    def _update_pagination(self) -> None:
        # Clear existing
        self._pagination_buttons.clear()
        self._pagination_dots.clear()
        while self.paginationLayout.count():
            item = self.paginationLayout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w:
                w.deleteLater()

        if self.total_pages <= 1:
            self.bottomWidget.setVisible(False)
            return

        self.bottomWidget.setVisible(True)

        pages_to_show = set()
        pages_to_show.add(1)
        pages_to_show.add(self.total_pages)
        for i in range(max(1, self.current_page - 2), min(self.total_pages, self.current_page + 2) + 1):
            pages_to_show.add(i)

        sorted_pages = sorted(pages_to_show)

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
                dots.setStyleSheet(muted_text_style())
                self._pagination_dots.append(dots)
                self.paginationLayout.addWidget(dots)

            text = str(p)
            btn = PushButton(text, self.bottomWidget)
            btn.setMinimumWidth(36)
            btn.setFixedHeight(32)
            is_current_page = p == self.current_page
            btn.setProperty("currentPage", is_current_page)
            self._pagination_buttons.append(btn)

            if is_current_page:
                btn.setStyleSheet(active_page_button_style())
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

    def _preload_page(self, page: int) -> None:
        cache_key = (self.current_keyword, page)
        if cache_key in self._search_cache or cache_key in self._preloading_pages:
            return
        if len(self.workers) >= 4:
            return

        self._preloading_pages.add(cache_key)
        import random

        delay = random.uniform(1.0, 2.5)
        w = SearchWorker(self.current_keyword, page, delay)
        w.result_signal.connect(self._on_preload_result, Qt.ConnectionType.QueuedConnection)
        w.error_signal.connect(self._on_preload_error, Qt.ConnectionType.QueuedConnection)
        w.finished.connect(self._on_search_worker_finished, Qt.ConnectionType.QueuedConnection)
        w.finished.connect(w.deleteLater, Qt.ConnectionType.QueuedConnection)
        self.workers[cache_key] = w
        w.start()

    def _on_preload_result(self, keyword: str, results: list[Comic], total_pages: int, page: int) -> None:
        cache_key = (keyword, page)
        self._preloading_pages.discard(cache_key)
        if keyword != self.current_keyword:
            return

        if not results and page > 1:
            total_pages = min(total_pages, page - 1)
            self.total_pages = min(self.total_pages, page - 1)
            self._update_pagination()

        self._search_cache[cache_key] = (results, total_pages)

    def _on_preload_error(self, keyword: str, err_msg: str, page: int) -> None:
        cache_key = (keyword, page)
        self._preloading_pages.discard(cache_key)
        if keyword != self.current_keyword:
            return

    def do_search(self, keyword: str) -> None:
        if not keyword.strip():
            return

        if self.heroWidget.isVisible():
            self.heroWidget.hide()
            self.topSpacerWidget.hide()
            self.bottomSpacerWidget.hide()
            self.contentWidget.show()

        self.current_keyword = keyword
        self.current_page = 1
        self._search_cache.clear()
        self._load_data()

    def go_to_page(self, page: int) -> None:
        self.current_page = page
        self._load_data()

    def prev_page(self) -> None:
        if self.current_page > 1:
            self.current_page -= 1
            self._load_data()

    def next_page(self) -> None:
        if not self.current_keyword:
            return
        self.current_page += 1
        self._load_data()

    def _load_data(self) -> None:
        self.searchBar.setEnabled(False)
        self.bottomWidget.setEnabled(False)

        self._clear_cards()

        self.scrollArea.hide()
        self.emptyWidget.hide()
        self.loadingWidget.show()

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
            w.result_signal.connect(self._on_search_result, Qt.ConnectionType.QueuedConnection)
            w.error_signal.connect(self._on_search_error, Qt.ConnectionType.QueuedConnection)
            if cache_key in self._preloading_pages:
                self._preloading_pages.remove(cache_key)
        else:
            if self.worker is not None:
                self.worker.result_signal.disconnect()
                self.worker.error_signal.disconnect()
                self.worker.requestInterruption()
                self._old_workers.append(self.worker)

            self.worker = SearchWorker(self.current_keyword, self.current_page)
            self.worker.result_signal.connect(self._on_search_result, Qt.ConnectionType.QueuedConnection)
            self.worker.error_signal.connect(self._on_search_error, Qt.ConnectionType.QueuedConnection)
            self.worker.finished.connect(self._on_search_worker_finished, Qt.ConnectionType.QueuedConnection)
            self.worker.finished.connect(self.worker.deleteLater, Qt.ConnectionType.QueuedConnection)
            self.worker.start()

    def _clear_cards(self) -> None:
        """Invalidate snapshots before scheduling every card for deferred deletion."""
        self._state_coordinator.invalidate()
        while self.flowLayout.count():
            item = self.flowLayout.takeAt(0)
            if item is None:
                continue
            widget = item
            if widget:
                widget.deleteLater()
        self.card_map.clear()

    @Slot()
    def _on_search_worker_finished(self) -> None:
        worker = self.sender()
        if not isinstance(worker, SearchWorker):
            return
        if worker in self._old_workers:
            self._old_workers.remove(worker)
        cache_key = (worker.keyword, worker.page)
        if self.workers.get(cache_key) is worker:
            self.workers.pop(cache_key, None)

    def _on_search_result(self, keyword: str, results: list[Comic], total_pages: int, page: int) -> None:
        if keyword != self.current_keyword or page != self.current_page:
            return

        self.worker = None
        self.searchBar.setEnabled(True)
        self.bottomWidget.setEnabled(True)
        self.loadingWidget.hide()

        if not results and page > 1:
            total_pages = min(total_pages, page - 1)

        # update cache
        cache_key = (self.current_keyword, page)
        self._search_cache[cache_key] = (results, total_pages)

        self.total_pages = total_pages
        self._update_pagination()

        if not results:
            self.emptyWidget.show()
            self.scrollArea.hide()
            InfoBar.warning(
                "搜索结束", "未能找到相关漫画或已经是最后一页", parent=self, position=InfoBarPosition.TOP_RIGHT
            )
        else:
            self.emptyWidget.hide()
            self.scrollArea.show()

        for comic in results:
            card = ComicCard(
                comic,
                self._repository,
                self._cover_manager,
                self.scrollWidget,
            )
            card.downloadClicked.connect(self._on_download_clicked)
            self.flowLayout.addWidget(card)
            self.card_map[comic.aid] = card
        self.refresh_card_states(force=True)

    def _on_search_error(self, keyword: str, err_msg: str, page: int) -> None:
        if keyword != self.current_keyword or page != self.current_page:
            return

        self.worker = None
        self.loadingWidget.hide()
        self.searchBar.setEnabled(True)
        self.bottomWidget.setEnabled(True)
        InfoBar.error("搜索失败", f"网络请求失败：{err_msg}", parent=self, position=InfoBarPosition.TOP_RIGHT)

    def stop_workers(self, deadline: float | None = None) -> None:
        """Request interruption and join all search threads during application shutdown."""
        shutdown_deadline = deadline or (time.monotonic() + 16.0)
        self._state_refresh_timer.stop()
        self._state_coordinator.stop(shutdown_deadline)
        candidates = [self.worker, *self.workers.values(), *self._old_workers]
        workers = list(dict.fromkeys(worker for worker in candidates if worker is not None))
        for index, worker in enumerate(workers):
            stop_qthread(worker, shutdown_deadline, name=f"search_worker_{index}", join_after_timeout=True)
        self.worker = None
        self.workers.clear()
        self._old_workers.clear()

    def _on_download_clicked(self, comic: Comic) -> None:
        self._downloader.add_task(comic)
        InfoBar.success(
            "已加入下载队列",
            f"请前往左侧『下载任务』页面查看进度: {comic.title}",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _show_context_menu(self, pos: QPoint) -> None:
        selected_items = [cast(ComicCard, item) for item in self.scrollWidget.get_selected_items()]
        target_item = self.scrollWidget.get_item_at(pos)
        target_card = cast(ComicCard | None, target_item)

        if not selected_items:
            if target_card:
                selected_items = [target_card]
            else:
                return
        elif target_card and target_card not in selected_items:
            self.scrollWidget.clear_selection()
            target_card.setSelected(True)
            selected_items = [target_card]

        menu = QMenu(self)

        action_select_all = QAction("全选", self)
        action_select_all.triggered.connect(self.scrollWidget.select_all)
        menu.addAction(action_select_all)

        menu.addSeparator()

        action_add = QAction(f"加入任务队列 ({len(selected_items)}项)", self)
        action_add.triggered.connect(lambda: self._bulk_download(selected_items))
        menu.addAction(action_add)

        action_deselect = QAction("取消选中", self)
        action_deselect.triggered.connect(self.scrollWidget.clear_selection)
        menu.addAction(action_deselect)

        # Determine global pos from scroll widget pos
        global_pos = self.scrollWidget.mapToGlobal(pos)
        menu.exec(global_pos)

    def _bulk_download(self, selected_items: list[ComicCard]) -> None:
        added_count = 0
        for card in selected_items:
            if card.can_queue_download:
                card.mark_queued()
                self._downloader.add_task(card.comic)
                added_count += 1

        self.scrollWidget.clear_selection()
        if added_count == 0:
            InfoBar.info(
                "无需重复添加",
                "选中的漫画均已下载或已在队列中",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
            )
            return
        InfoBar.success(
            "批量操作成功",
            f"已将 {added_count} 部漫画加入下载队列",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _on_topbar_add_to_queue(self) -> None:
        selected_items = [cast(ComicCard, item) for item in self.scrollWidget.get_selected_items()]
        if not selected_items:
            InfoBar.warning(
                "未选中漫画",
                "请先使用鼠标点击、框选或按 Ctrl+A 选中要下载的漫画",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
            )
            return
        self._bulk_download(selected_items)
