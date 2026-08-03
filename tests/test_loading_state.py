"""Regressions for the theme-aware animated loading presentation."""

from PySide6.QtWidgets import QApplication

from wnacg.ui.components.loading_state import AnimatedLoadingState


def test_loading_state_animates_only_while_visible() -> None:
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    assert isinstance(application, QApplication)

    loading_state = AnimatedLoadingState()
    assert loading_state.is_animating is False

    loading_state.show()
    application.processEvents()
    assert loading_state.is_animating is True
    assert loading_state.title_label.text().startswith("正在获取漫画数据")

    loading_state.hide()
    application.processEvents()
    assert loading_state.is_animating is False

    loading_state.deleteLater()
    application.processEvents()
