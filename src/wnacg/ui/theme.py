"""Central theme mapping and accessible color tokens for custom Qt styling."""
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from PySide6.QtGui import QColor
from qfluentwidgets import Theme, ThemeColor, isDarkTheme, setTheme

from wnacg.infrastructure.config import AppearanceTheme

_FLUENT_THEMES: dict[AppearanceTheme, Theme] = {
    AppearanceTheme.SYSTEM: Theme.AUTO,
    AppearanceTheme.LIGHT: Theme.LIGHT,
    AppearanceTheme.DARK: Theme.DARK,
}


def apply_application_theme(theme: AppearanceTheme) -> None:
    """Apply a persisted preference through QFluentWidgets' live theme engine."""
    setTheme(_FLUENT_THEMES[theme])


def accent_color() -> QColor:
    """Return the current theme-adjusted Fluent accent color."""
    return ThemeColor.PRIMARY.color()


def primary_text_color() -> QColor:
    """Return the primary custom-widget foreground for the active theme."""
    return QColor("#F2F2F2") if isDarkTheme() else QColor("#202020")


def muted_text_color() -> QColor:
    """Return secondary text with readable contrast in both themes."""
    return QColor("#BDBDBD") if isDarkTheme() else QColor("#606060")


def danger_text_color() -> QColor:
    """Return an accessible destructive/error foreground color."""
    return QColor("#FF8A80") if isDarkTheme() else QColor("#B42318")


def success_text_color() -> QColor:
    """Return an accessible success foreground color."""
    return QColor("#6CCB8F") if isDarkTheme() else QColor("#167344")


def warning_text_color() -> QColor:
    """Return an accessible warning foreground color."""
    return QColor("#F5C66A") if isDarkTheme() else QColor("#8A5A00")


def setting_page_background_color() -> QColor:
    """Return the opaque settings surface used behind Fluent setting cards."""
    return QColor("#202020") if isDarkTheme() else QColor("#F9F9F9")


def _css_color(color: QColor, *, alpha: int | None = None) -> str:
    resolved_alpha = color.alpha() if alpha is None else min(max(alpha, 0), 255)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {resolved_alpha})"


def _contrast_text_color(background: QColor) -> QColor:
    red = background.redF()
    green = background.greenF()
    blue = background.blueF()
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return QColor("#171717") if luminance > 0.62 else QColor("#FFFFFF")


def muted_text_style(*, pixel_size: int | None = None) -> str:
    """Build a secondary-label style without freezing the active theme."""
    font_rule = f" font-size: {pixel_size}px;" if pixel_size is not None else ""
    return f"color: {_css_color(muted_text_color())};{font_rule}"


def primary_text_style(*, pixel_size: int | None = None, weight: int | None = None) -> str:
    """Build a primary-label style for standard Qt labels."""
    font_rule = f" font-size: {pixel_size}px;" if pixel_size is not None else ""
    weight_rule = f" font-weight: {weight};" if weight is not None else ""
    return f"color: {_css_color(primary_text_color())};{font_rule}{weight_rule}"


def danger_text_style(*, bold: bool = False) -> str:
    """Build a theme-aware error/warning text style."""
    weight_rule = " font-weight: 600;" if bold else ""
    return f"color: {_css_color(danger_text_color())};{weight_rule}"


def setting_page_style() -> str:
    """Style every QScrollArea layer so transparent Mica cannot expose Qt's light palette."""
    background = _css_color(setting_page_background_color())
    return (
        f"QScrollArea {{ border: none; background-color: {background}; }}"
        f"QWidget#settingsViewport {{ background-color: {background}; }}"
        f"QWidget#settingsScrollWidget {{ background-color: {background}; }}"
    )


def cover_placeholder_style() -> str:
    """Build a low-contrast cover placeholder for either surface theme."""
    fill = QColor("#FFFFFF") if isDarkTheme() else QColor("#000000")
    alpha = 18 if isDarkTheme() else 12
    return f"background-color: {_css_color(fill, alpha=alpha)}; border-radius: 8px;"


def selected_card_style(selector: str) -> str:
    """Build a theme-aware accent border and translucent selection surface."""
    accent = accent_color()
    background_alpha = 38 if isDarkTheme() else 24
    return (
        f"{selector} {{ border: 2px solid {_css_color(accent)}; "
        f"background-color: {_css_color(accent, alpha=background_alpha)}; border-radius: 8px; }}"
    )


def active_page_button_style() -> str:
    """Build the selected pagination-button style with contrast-safe text."""
    accent = accent_color()
    hover = ThemeColor.LIGHT_1.color()
    pressed = ThemeColor.DARK_1.color()
    foreground = _contrast_text_color(accent)
    return (
        f"PushButton {{ background-color: {_css_color(accent)}; color: {_css_color(foreground)}; "
        "border: none; border-radius: 5px; }}"
        f"PushButton:hover {{ background-color: {_css_color(hover)}; }}"
        f"PushButton:pressed {{ background-color: {_css_color(pressed)}; }}"
    )


def round_accent_button_style() -> str:
    """Build a round floating accent button with hover and pressed states."""
    accent = accent_color()
    hover = ThemeColor.LIGHT_1.color()
    pressed = ThemeColor.DARK_1.color()
    foreground = _contrast_text_color(accent)
    return (
        f"PrimaryToolButton {{ border-radius: 20px; background-color: {_css_color(accent)}; "
        f"color: {_css_color(foreground)}; border: none; }}"
        f"PrimaryToolButton:hover {{ background-color: {_css_color(hover)}; }}"
        f"PrimaryToolButton:pressed {{ background-color: {_css_color(pressed)}; }}"
    )
