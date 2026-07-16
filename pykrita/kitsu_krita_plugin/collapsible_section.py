"""A simple collapsible section: a clickable title header that shows/hides
a content widget beneath it, used to let the Kitsu dock's Connection/Task/
Feedback/Publish groups shrink out of the way so neighboring dockers
(Layers, brush presets, ...) get more room.
"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """Wraps `content_widget` with a toggleable title header."""

    toggled = pyqtSignal(bool)  # emits the new expanded state

    def __init__(self, title, content_widget, collapsed=False, parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        self.toggle_button = QToolButton()
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle_button.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; } "
            "QToolButton::menu-indicator { image: none; }"
        )
        self.toggle_button.clicked.connect(self._apply_state)
        outer.addWidget(self.toggle_button)

        self.content_widget = content_widget
        outer.addWidget(self.content_widget)

        self.setChecked(not collapsed)

    def isChecked(self):
        return self.toggle_button.isChecked()

    def setChecked(self, expanded):
        self.toggle_button.setChecked(expanded)
        self._apply_state()

    def _apply_state(self):
        expanded = self.toggle_button.isChecked()
        self.content_widget.setVisible(expanded)
        self.toggle_button.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggled.emit(expanded)
