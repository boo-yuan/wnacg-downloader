from PySide6.QtCore import Qt, QRect, Signal, QPoint, QSize
from PySide6.QtWidgets import QWidget, QRubberBand
from PySide6.QtGui import QMouseEvent

class SelectableContainer(QWidget):
    selectionChanged = Signal() # emitted when selection changes

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rubberBand = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.origin = QPoint()
        self._last_selected_item = None
        self.is_dragging = False
        self._pre_drag_selection = {}

    def get_selectable_items(self):
        # Return all visible children that have a 'setSelected' method
        items = []
        for child in self.children():
            if isinstance(child, QWidget) and child.isVisible() and hasattr(child, 'setSelected'):
                items.append(child)
        return items

    def _get_item_at(self, pos: QPoint):
        for item in self.get_selectable_items():
            if item.geometry().contains(pos):
                return item
        return None

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            item = self._get_item_at(event.pos())
            items = self.get_selectable_items()
            
            if item:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    if self._last_selected_item and self._last_selected_item in items:
                        idx1 = items.index(self._last_selected_item)
                        idx2 = items.index(item)
                        start, end = min(idx1, idx2), max(idx1, idx2)
                        
                        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                            self.clear_selection(emit=False)
                            
                        for i in range(start, end + 1):
                            items[i].setSelected(True)
                    else:
                        item.setSelected(True)
                        self._last_selected_item = item
                elif event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    item.setSelected(not getattr(item, '_is_selected', False))
                    self._last_selected_item = item
                else:
                    self.clear_selection(emit=False)
                    item.setSelected(True)
                    self._last_selected_item = item
                
                self.selectionChanged.emit()
            else:
                self.origin = event.pos()
                self.rubberBand.setGeometry(QRect(self.origin, QSize()))
                self.rubberBand.show()
                self.is_dragging = True
                self._pre_drag_selection = {i: getattr(i, '_is_selected', False) for i in items}

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.is_dragging:
            rect = QRect(self.origin, event.pos()).normalized()
            self.rubberBand.setGeometry(rect)
            
            ctrl_held = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            items = self.get_selectable_items()
            
            for item in items:
                intersects = rect.intersects(item.geometry())
                if intersects:
                    item.setSelected(True)
                else:
                    if ctrl_held:
                        item.setSelected(self._pre_drag_selection.get(item, False))
                    else:
                        item.setSelected(False)
                        
            self.selectionChanged.emit()
            
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.is_dragging:
            self.rubberBand.hide()
            self.is_dragging = False
            
            if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                rect = QRect(self.origin, event.pos()).normalized()
                if rect.width() < 5 and rect.height() < 5:
                    self.clear_selection(emit=True)
                    
        super().mouseReleaseEvent(event)

    def clear_selection(self, emit=True):
        changed = False
        for item in self.get_selectable_items():
            if getattr(item, '_is_selected', False):
                item.setSelected(False)
                changed = True
        if emit and changed:
            self.selectionChanged.emit()
            
    def select_all(self):
        for item in self.get_selectable_items():
            item.setSelected(True)
        self.selectionChanged.emit()

    def get_selected_items(self):
        return [i for i in self.get_selectable_items() if getattr(i, '_is_selected', False)]
