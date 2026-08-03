"""Regressions for persisted and live application theme switching."""
# pyright: reportUnknownMemberType=false

from pathlib import Path

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
from qfluentwidgets import isDarkTheme

from wnacg.infrastructure import config
from wnacg.ui.theme import (
    apply_application_theme,
    muted_text_color,
    selected_card_style,
    setting_page_background_color,
)
from wnacg.ui.views.setting_interface import AboutSettingInterface, BaseSettingInterface


def test_custom_theme_tokens_change_between_light_and_dark() -> None:
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    assert isinstance(application, QApplication)

    try:
        apply_application_theme(config.AppearanceTheme.LIGHT)
        light_muted = muted_text_color()
        light_selection = selected_card_style("Card")

        apply_application_theme(config.AppearanceTheme.DARK)
        assert isDarkTheme() is True
        assert muted_text_color() != light_muted
        assert selected_card_style("Card") != light_selection
    finally:
        apply_application_theme(config.AppearanceTheme.SYSTEM)


def test_setting_page_background_repaints_between_light_and_dark() -> None:
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    assert isinstance(application, QApplication)

    interface = BaseSettingInterface()
    interface.resize(480, 320)
    interface.show()
    try:
        apply_application_theme(config.AppearanceTheme.DARK)
        application.processEvents()
        dark_image = interface.viewport().grab().toImage()
        center = dark_image.rect().center()
        assert QColor(dark_image.pixel(center)) == setting_page_background_color()

        apply_application_theme(config.AppearanceTheme.LIGHT)
        application.processEvents()
        light_image = interface.viewport().grab().toImage()
        assert QColor(light_image.pixel(center)) == setting_page_background_color()
        assert QColor(light_image.pixel(center)) != QColor(dark_image.pixel(center))
    finally:
        interface.close()
        interface.deleteLater()
        application.processEvents()
        apply_application_theme(config.AppearanceTheme.SYSTEM)


def test_about_settings_persist_and_apply_theme(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    assert isinstance(application, QApplication)

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)
    original_theme = config.cfg.appearance_theme
    config.cfg.appearance_theme = config.AppearanceTheme.SYSTEM
    interface = AboutSettingInterface()
    try:
        assert interface.themeCard.comboBox.currentIndex() == 0

        interface.themeCard.comboBox.setCurrentIndex(2)
        application.processEvents()

        assert config.cfg.appearance_theme is config.AppearanceTheme.DARK
        assert isDarkTheme() is True
        persisted = config.AppConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
        assert persisted.appearance_theme is config.AppearanceTheme.DARK
    finally:
        interface.close()
        interface.deleteLater()
        application.processEvents()
        config.cfg.appearance_theme = original_theme
        apply_application_theme(original_theme)
