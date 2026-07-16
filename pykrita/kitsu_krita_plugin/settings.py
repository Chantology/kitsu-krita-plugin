"""Persisted connection settings for the Kitsu/Zou plugin.

Everything here is safe to write to disk (server URL, email, project id,
optional API token, and a JWT refresh token obtained after a successful
login). The account password is intentionally never part of this module —
it only ever lives in memory for the current Krita session.

The refresh token is a bearer credential, not just a preference, but it's
bounded and revocable (expires after ~15 days by default on the server,
configurable by whoever runs the Kitsu instance, and unlike the password
it can't be used to change the account's password or email) — it exists
specifically so the plugin can restore a session on the next Krita launch
without asking for the password again.
"""

import hashlib

from PyQt5.QtCore import QSettings

_ORG = "Kitsu"
_APP = "KritaPlugin"

_KEYS = ("server_url", "email", "project_id", "api_token", "refresh_token")


def load():
    settings = QSettings(_ORG, _APP)
    return {key: settings.value(key, "", type=str) for key in _KEYS}


def save(values):
    settings = QSettings(_ORG, _APP)
    for key in _KEYS:
        if key in values:
            settings.setValue(key, values[key])
    settings.sync()


def load_section_collapsed(name):
    settings = QSettings(_ORG, _APP)
    return settings.value(f"section_collapsed_{name}", False, type=bool)


def save_section_collapsed(name, collapsed):
    settings = QSettings(_ORG, _APP)
    settings.setValue(f"section_collapsed_{name}", collapsed)


def _task_type_override_key(document_path):
    digest = hashlib.sha1(document_path.encode("utf-8")).hexdigest()
    return f"task_type_override_{digest}"


def load_task_type_override(document_path):
    """Return the manually-picked task-type id remembered for this exact
    file path, or None if there isn't one (e.g. never set, or the file
    was since renamed/moved — falls back to normal folder detection)."""
    settings = QSettings(_ORG, _APP)
    value = settings.value(_task_type_override_key(document_path), "", type=str)
    return value or None


def save_task_type_override(document_path, task_type_id):
    settings = QSettings(_ORG, _APP)
    settings.setValue(_task_type_override_key(document_path), task_type_id)
    settings.sync()
