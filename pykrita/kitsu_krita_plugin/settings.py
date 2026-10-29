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


def _override_key(kind, document_path):
    digest = hashlib.sha1(document_path.encode("utf-8")).hexdigest()
    return f"{kind}_override_{digest}"


def _load_override(kind, document_path):
    settings = QSettings(_ORG, _APP)
    value = settings.value(_override_key(kind, document_path), "", type=str)
    return value or None


def _save_override(kind, document_path, entity_id):
    settings = QSettings(_ORG, _APP)
    settings.setValue(_override_key(kind, document_path), entity_id)
    settings.sync()


def load_task_type_override(document_path):
    """Return the manually-picked task-type id remembered for this exact
    file path, or None if there isn't one (e.g. never set, or the file
    was since renamed/moved — falls back to normal folder detection)."""
    return _load_override("task_type", document_path)


def save_task_type_override(document_path, task_type_id):
    _save_override("task_type", document_path, task_type_id)


def load_shot_override(document_path):
    """Return the manually-picked shot id remembered for this exact file
    path, or None — same lifetime rules as load_task_type_override."""
    return _load_override("shot", document_path)


def save_shot_override(document_path, shot_id):
    _save_override("shot", document_path, shot_id)


def clear_overrides(document_path):
    """Forget both manual picks for this file, so detection goes back to
    reading its folders."""
    settings = QSettings(_ORG, _APP)
    for kind in ("shot", "task_type"):
        settings.remove(_override_key(kind, document_path))
    settings.sync()
