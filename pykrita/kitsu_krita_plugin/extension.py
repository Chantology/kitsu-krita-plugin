"""Adds a "Kitsu/Zou" menu entry next to Help that shows/raises the dock.

The dock itself is already registered via a DockWidgetFactory (see
kitsu_krita_plugin.py) and reachable through Settings > Dockers > Kitsu,
but that's not very discoverable. This gives it a direct, visible entry
point, matching where users of the previous version expect to find it.
"""

from krita import Extension, Krita
from PyQt5.QtWidgets import QDockWidget


class KitsuExtension(Extension):
    def __init__(self, parent):
        super().__init__(parent)

    def setup(self):
        pass

    def createActions(self, window):
        # menu="" avoids Krita also placing it under a default submenu
        # (e.g. Tools/Scripts) — addAction below puts it top-level instead.
        action = window.createAction("kitsu_open_docker", "Kitsu/Zou", "")
        # Deliberately not capturing `window` here: Krita can tear down and
        # recreate its Window wrapper between now and whenever the action
        # is actually clicked, which leaves a captured reference pointing
        # at a deleted C++ object ("wrapped C/C++ object ... has been
        # deleted"). Always look up the *current* active window instead.
        action.triggered.connect(self._show_docker)
        window.qwindow().menuBar().addAction(action)

    def _show_docker(self):
        window = Krita.instance().activeWindow()
        if window is None:
            return
        qwindow = window.qwindow()

        dock = qwindow.findChild(QDockWidget, "kitsuDocker")
        if dock is None:
            # Fall back to matching by title in case Krita's internal
            # objectName convention for factory-created docks changes.
            for candidate in qwindow.findChildren(QDockWidget):
                if candidate.windowTitle() == "Kitsu":
                    dock = candidate
                    break

        if dock is None:
            return

        dock.setVisible(True)
        dock.raise_()
