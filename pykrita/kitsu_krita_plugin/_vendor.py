"""Makes the bundled third-party libraries under vendor/ importable.

Krita's Python environment does not ship `gazu` (CGWire's official
Kitsu/Zou API client) or the packages it needs at runtime (`requests`,
`urllib3`, `certifi`, `idna`), so copies of all of them are vendored here.
This module must be imported before anything does `import gazu` or
`import requests`.
"""

import os
import sys

_VENDOR_DIR = os.path.join(os.path.dirname(__file__), "vendor")

if _VENDOR_DIR not in sys.path:
    # Appended, not inserted at index 0: a real installation of a vendored
    # library (e.g. if the user later pip-installs gazu properly) still
    # takes priority over the bundled fallback.
    sys.path.append(_VENDOR_DIR)
