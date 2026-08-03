"""Theme-aware animated loading state for asynchronous UI operations."""

from math import cos, radians, sin

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QHideEvent, QPainter, QPaintEvent, QPen, QShowEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import qconfig

from wnacg.ui.theme import accent_color, muted_text_style, primary_text_color, primary_text_style


class _AnimatedLoadingIndicator(QWidget):
    """Paint a compact dual-orbit indicator for a supplied animation time."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._elapsed_milliseconds = 0
        self.setFixedSize(112, 112)

    def sizeHint(self) -> QSize:
        return QSize(112, 112)

    def set_elapsed_time(self, milliseconds: int) -> None:
        """Advance the time-based animation without accumulating timer drift."""
        self._elapsed_milliseconds = max(0, milliseconds)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            elapsed_seconds = self._elapsed_milliseconds / 1_000
            accent = accent_color()
            track = primary_text_color()
            track.setAlpha(28)

            center = QPointF(self.width() / 2, self.height() / 2)
            outer_rect = QRectF(15, 15, 82, 82)
            inner_rect = QRectF(28, 28, 56, 56)

            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(track, 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawEllipse(outer_rect)

            outer_start = -(elapsed_seconds * 190) % 360
            outer_span = 92 + int(30 * (sin(elapsed_seconds * 3.2) + 1) / 2)
            outer_color = QColor(accent)
            outer_color.setAlpha(235)
            painter.setPen(QPen(outer_color, 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(outer_rect, int(outer_start * 16), outer_span * 16)

            inner_track = QColor(track)
            inner_track.setAlpha(20)
            painter.setPen(QPen(inner_track, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawEllipse(inner_rect)

            inner_start = (elapsed_seconds * 260 + 35) % 360
            inner_color = QColor(accent).lighter(125)
            inner_color.setAlpha(170)
            painter.setPen(QPen(inner_color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(inner_rect, int(inner_start * 16), 74 * 16)

            orbit_angle = radians(-(outer_start + outer_span))
            orbit_radius = outer_rect.width() / 2
            orbit_point = QPointF(
                center.x() + cos(orbit_angle) * orbit_radius,
                center.y() - sin(orbit_angle) * orbit_radius,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            orbit_glow = QColor(accent)
            orbit_glow.setAlpha(52)
            painter.setBrush(orbit_glow)
            painter.drawEllipse(orbit_point, 7, 7)
            painter.setBrush(accent)
            painter.drawEllipse(orbit_point, 3.2, 3.2)

            pulse = (sin(elapsed_seconds * 4.0) + 1) / 2
            halo = QColor(accent)
            halo.setAlpha(20 + int(pulse * 22))
            painter.setBrush(halo)
            painter.drawEllipse(center, 14 + pulse * 3, 14 + pulse * 3)
            core = QColor(accent)
            core.setAlpha(210)
            painter.setBrush(core)
            painter.drawEllipse(center, 5.5 + pulse, 5.5 + pulse)
        finally:
            painter.end()


class AnimatedLoadingState(QWidget):
    """Self-contained loading presentation that animates only while visible."""

    _ELLIPSIS_FRAMES = ("", ".", "..", "...")

    def __init__(
        self,
        title: str = "正在获取漫画数据",
        description: str = "正在连接站点并整理搜索结果，请稍候",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._base_title = title
        self._ellipsis_frame = -1
        self._elapsed_timer = QElapsedTimer()
        self._animation_timer = QTimer(self)
        self._animation_timer.setInterval(16)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._animation_timer.timeout.connect(self._advance_animation)

        self.setAccessibleName(title)
        self.setMinimumHeight(300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 36, 24, 56)
        layout.setSpacing(0)
        layout.addStretch(2)

        self.indicator = _AnimatedLoadingIndicator(self)
        layout.addWidget(self.indicator, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(22)

        self.title_label = QLabel(title, self)
        title_font = QFont(self.title_label.font())
        title_font.setFamilies(["Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
        title_font.setPixelSize(20)
        title_font.setWeight(QFont.Weight.DemiBold)
        self.title_label.setFont(title_font)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label.setMinimumWidth(320)
        layout.addWidget(self.title_label, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(9)

        self.description_label = QLabel(description, self)
        description_font = QFont(self.description_label.font())
        description_font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
        description_font.setPixelSize(14)
        self.description_label.setFont(description_font)
        self.description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.description_label)
        layout.addStretch(3)

        qconfig.themeChanged.connect(self.refresh_theme)
        self.refresh_theme()

    @property
    def is_animating(self) -> bool:
        """Return whether the repaint timer currently owns animation work."""
        return self._animation_timer.isActive()

    def start_animation(self) -> None:
        """Start or restart the loading animation."""
        if self._animation_timer.isActive():
            return
        self._elapsed_timer.start()
        self._advance_animation()
        self._animation_timer.start()

    def stop_animation(self) -> None:
        """Stop repaint work when the loading state is not visible."""
        self._animation_timer.stop()

    def refresh_theme(self, _theme: object | None = None) -> None:
        """Refresh custom-painted and standard-label colors after a live switch."""
        self.title_label.setStyleSheet(primary_text_style())
        self.description_label.setStyleSheet(muted_text_style())
        self.indicator.update()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.start_animation()

    def hideEvent(self, event: QHideEvent) -> None:
        self.stop_animation()
        super().hideEvent(event)

    def _advance_animation(self) -> None:
        elapsed_milliseconds = self._elapsed_timer.elapsed()
        self.indicator.set_elapsed_time(elapsed_milliseconds)
        ellipsis_frame = (elapsed_milliseconds // 360) % len(self._ELLIPSIS_FRAMES)
        if ellipsis_frame == self._ellipsis_frame:
            return
        self._ellipsis_frame = ellipsis_frame
        self.title_label.setText(f"{self._base_title}{self._ELLIPSIS_FRAMES[ellipsis_frame]}")
