"""Registers the Kitsu/Zou dock widget and menu entry with Krita.

Per Krita's plugin documentation, registration happens unconditionally at
module import time — no active-window checks, no swallowed exceptions. If
this fails, the traceback will show up in Krita's terminal output instead
of being hidden (run krita.com from a command prompt to see it on
Windows).

The dock is reachable via Settings > Dockers > Kitsu once registered, and
also via the "Kitsu/Zou" menu entry added by KitsuExtension for
discoverability.
"""

from krita import DockWidgetFactory, DockWidgetFactoryBase, Krita

from .docker import KitsuDocker
from .extension import KitsuExtension

Krita.instance().addDockWidgetFactory(
    DockWidgetFactory("kitsuDocker", DockWidgetFactoryBase.DockRight, KitsuDocker)
)
Krita.instance().addExtension(KitsuExtension(Krita.instance()))
