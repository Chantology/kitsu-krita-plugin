"""Resolve a Kitsu asset/shot task from a Krita document's folder structure.

Kitsu supports two separate entity hierarchies, and this tries both:

  - Assets: .../<AssetType>/<Asset>/<TaskType>/file.kra
    e.g. assets/characters/Hero/design/Hero_design.kra

  - Shots:  .../<Sequence>/<Shot>/<TaskType>/file.kra
    for movies/shorts/commercials (no episodes), or
    .../<Episode>/<Sequence>/<Shot>/<TaskType>/file.kra
    for a TV series, where episodes are an extra optional level.

Folder names are matched to Kitsu's names case-insensitively, since
pipeline folder conventions (lowercase) commonly differ from how they're
capitalized in Kitsu ("characters" vs "Characters").
"""

import os
from collections import namedtuple


class TaskDetectionError(Exception):
    pass


class TaskTypeMismatch(TaskDetectionError):
    """Raised when the asset/shot resolved fine but its task-type folder
    name didn't match any task type in Kitsu (or that entity has no Task
    of the matched type yet) — callers can offer a manual task-type
    picker instead of just reporting failure."""

    def __init__(self, entity, breadcrumb, task_type_name):
        super().__init__(
            f"Could not match task type {task_type_name!r} for "
            f"{' / '.join(breadcrumb)!r} against this project's task types."
        )
        self.entity = entity
        self.breadcrumb = breadcrumb
        self.task_type_name = task_type_name


# Returned by _try_*_context in place of None when the entity (asset or
# shot) resolved but the task-type folder name didn't — as opposed to a
# bare None, which still means "this context doesn't apply at all".
TaskTypeUnresolved = namedtuple("TaskTypeUnresolved", ["entity", "breadcrumb", "task_type_name"])


def _folder_names(document_path, levels):
    """Return the `levels` folder names directly above the document, in
    root-to-leaf order — or None if there aren't that many levels."""
    names = []
    current = document_path
    for _ in range(levels):
        current = os.path.dirname(current)
        name = os.path.basename(current)
        if not name:
            return None
        names.append(name)
    names.reverse()
    return names


def _find_by_name(items, name):
    target = name.strip().casefold()
    for item in items:
        if (item.get("name") or "").strip().casefold() == target:
            return item
    return None


def _try_asset_context(session, project, document_path):
    names = _folder_names(document_path, 3)
    if names is None:
        return None
    asset_type_name, asset_name, task_type_name = names

    asset_type = _find_by_name(session.all_asset_types_for_project(project), asset_type_name)
    if asset_type is None:
        return None

    asset = session.get_asset_by_name(project, asset_name, asset_type=asset_type)
    if asset is None:
        return None

    breadcrumb = [asset_type_name, asset_name]

    task_type = _find_by_name(session.all_task_types_for_project(project), task_type_name)
    if task_type is None:
        return TaskTypeUnresolved(asset, breadcrumb, task_type_name)

    task = session.get_task_by_entity(asset, task_type)
    if task is None:
        return TaskTypeUnresolved(asset, breadcrumb, task_type_name)

    return task, breadcrumb, task_type_name


def _try_shot_context(session, project, document_path, with_episode):
    names = _folder_names(document_path, 4 if with_episode else 3)
    if names is None:
        return None

    if with_episode:
        episode_name, sequence_name, shot_name, task_type_name = names
        episode = session.get_episode_by_name(project, episode_name)
        if episode is None:
            return None
        sequence = session.get_sequence_by_name(project, sequence_name, episode=episode)
    else:
        sequence_name, shot_name, task_type_name = names
        sequence = session.get_sequence_by_name(project, sequence_name)

    if sequence is None:
        return None

    shot = session.get_shot_by_name(sequence, shot_name)
    if shot is None:
        return None

    breadcrumb = ([episode_name] if with_episode else []) + [sequence_name, shot_name]

    task_type = _find_by_name(session.all_task_types_for_project(project), task_type_name)
    if task_type is None:
        return TaskTypeUnresolved(shot, breadcrumb, task_type_name)

    task = session.get_task_by_entity(shot, task_type)
    if task is None:
        return TaskTypeUnresolved(shot, breadcrumb, task_type_name)

    return task, breadcrumb, task_type_name


def detect_task(session, project, document_path):
    """Resolve the Kitsu task matching the document's folder structure.

    Tries, in order: asset context, shot context without an episode
    level, then shot context with one (for TV series).

    Returns:
        tuple: (task, breadcrumb, task_type_name), where breadcrumb is a
        list of strings describing the hierarchy above the task type,
        e.g. ["Characters", "Hero"] or ["SEQ01", "SH010"] or
        ["E01", "SEQ01", "SH010"].
    """
    if not document_path:
        raise TaskDetectionError(
            "This document has not been saved yet, so its folder can't be inspected."
        )

    # `TaskTypeUnresolved` is a non-empty namedtuple, so it's truthy —
    # this `or` chain still short-circuits exactly like before: the first
    # context that gets any hit (full match or a same-level task-type
    # miss) wins, and later contexts are never even queried.
    result = (
        _try_asset_context(session, project, document_path)
        or _try_shot_context(session, project, document_path, with_episode=False)
        or _try_shot_context(session, project, document_path, with_episode=True)
    )

    if isinstance(result, TaskTypeUnresolved):
        raise TaskTypeMismatch(result.entity, result.breadcrumb, result.task_type_name)

    if result is not None:
        return result

    raise TaskDetectionError(
        "Could not resolve a task from this document's folder structure. "
        "Expected '<AssetType>/<Asset>/<TaskType>/file' or "
        "'<Sequence>/<Shot>/<TaskType>/file' (optionally with an "
        "'<Episode>/' level in front, for TV series) — checked the "
        f"folders above {document_path!r} against this project's asset "
        "types, sequences, and episodes, but found no match."
    )
