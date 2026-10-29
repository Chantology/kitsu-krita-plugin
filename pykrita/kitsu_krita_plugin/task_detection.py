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

When none of those layouts resolves to a Kitsu asset or shot, ShotNotFound
is raised so the docker can offer a manual episode/sequence/shot picker
(see ShotTree) -- Krita is used for storyboards/shots as much as for
assets, and unlike the Nuke port there's no anchor folder to tell the two
apart, so the shot picker is the fallback either way. detect_entity and
resolve_task are detect_task's two halves, so a hand-picked shot or task
type can replace either one (see detect_with_overrides).
"""

import os


class TaskDetectionError(Exception):
    pass


class TaskTypeMismatch(TaskDetectionError):
    """Raised when the asset/shot resolved fine but its task-type folder
    name didn't match any task type in Kitsu (or that entity has no Task
    of the matched type yet) — callers can offer a manual task-type
    picker instead of just reporting failure.

    `breadcrumb` is the resolved entity's names, top-down, NOT including
    the (unmatched) task type. `context` is "asset" or "shot", so callers
    can still populate it once a task type is manually picked.
    """

    def __init__(self, entity, breadcrumb, task_type_name, context):
        super().__init__(
            f"Could not match task type {task_type_name!r} for "
            f"{' / '.join(breadcrumb)!r} against this project's task types."
        )
        self.entity = entity
        self.breadcrumb = breadcrumb
        self.task_type_name = task_type_name
        self.context = context


class ShotNotFound(TaskDetectionError):
    """Raised when none of the supported folder layouts maps onto a Kitsu
    asset or shot — callers can offer a manual shot picker instead of just
    reporting failure.

    `reason` is the lookup failure, for display. `task_type_name` is the
    folder directly above the file, so once a shot is picked the task type
    can still be resolved from it (see resolve_task).
    """

    def __init__(self, reason, task_type_name):
        super().__init__(reason)
        self.reason = reason
        self.task_type_name = task_type_name


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


def task_type_folder_name(document_path):
    """The folder directly above the file -- by convention, the task type."""
    return os.path.basename(os.path.dirname(document_path)) or None


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

    return asset, [asset_type_name, asset_name], task_type_name, "asset"


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
    return shot, breadcrumb, task_type_name, "shot"


def detect_entity(session, project, document_path):
    """First half of detect_task: resolve only the asset/shot.

    Tries, in order: asset context, shot context without an episode
    level, then shot context with one (for TV series) — the first layout
    whose entity resolves wins, even if its task type later doesn't.

    Returns (entity, breadcrumb, task_type_name, context) -- breadcrumb
    being the entity's names only, task_type_name the (not yet matched)
    task-type folder name, and context "asset" or "shot". Raises
    ShotNotFound when no layout resolves, TaskDetectionError for an
    unsaved document.
    """
    if not document_path:
        raise TaskDetectionError(
            "This document has not been saved yet, so its folder can't be inspected."
        )

    result = (
        _try_asset_context(session, project, document_path)
        or _try_shot_context(session, project, document_path, with_episode=False)
        or _try_shot_context(session, project, document_path, with_episode=True)
    )
    if result is not None:
        return result

    raise ShotNotFound(
        "Could not resolve an asset or shot from this document's folder "
        "structure. Expected '<AssetType>/<Asset>/<TaskType>/file' or "
        "'<Sequence>/<Shot>/<TaskType>/file' (optionally with an "
        "'<Episode>/' level in front, for TV series) — checked the "
        f"folders above {document_path!r} against this project's asset "
        "types, sequences, and episodes, but found no match.",
        task_type_folder_name(document_path),
    )


def resolve_task(session, project, entity, breadcrumb, task_type_name, context):
    """Second half of detect_task: find `entity`'s task matching the
    task-type folder name. Raises TaskTypeMismatch when there's no
    matching task type, or the entity has no task of that type."""
    if not task_type_name:
        raise TaskTypeMismatch(entity, breadcrumb, "", context)

    task_type = _find_by_name(session.all_task_types_for_project(project), task_type_name)
    if task_type is None:
        raise TaskTypeMismatch(entity, breadcrumb, task_type_name, context)

    task = session.get_task_by_entity(entity, task_type)
    if task is None:
        raise TaskTypeMismatch(entity, breadcrumb, task_type_name, context)

    return task, breadcrumb + [task_type_name], context


def resolve_task_by_type_id(session, project, entity, breadcrumb, context, task_type_id):
    """Like resolve_task, but for a task type picked by hand (by id).
    Returns None instead of raising when the type no longer exists or
    the entity has no task of that type, so callers can fall back to
    normal folder matching."""
    task_type = next(
        (
            t
            for t in session.all_task_types_for_project(project)
            if t.get("id") == task_type_id
        ),
        None,
    )
    if task_type is None:
        return None
    task = session.get_task_by_entity(entity, task_type)
    if task is None:
        return None
    return task, breadcrumb + [task_type.get("name", "?")], context


class ShotTree:
    """The project's episodes, sequences and shots, for the manual
    episode -> sequence -> shot picker. Built from three bulk requests
    (see load) rather than per-shot lookups; shots link to their
    sequence and sequences to their episode via `parent_id`."""

    def __init__(self, episodes, sequences, shots):
        def by_name(items):
            return sorted(items, key=lambda i: (i.get("name") or "").casefold())

        self._episodes = by_name(episodes)
        self._sequences = by_name(sequences)
        self._shots = by_name(shots)
        self._episodes_by_id = {e["id"]: e for e in self._episodes}
        self._sequences_by_id = {s["id"]: s for s in self._sequences}
        self._shots_by_id = {s["id"]: s for s in self._shots}

    @classmethod
    def load(cls, session, project):
        return cls(
            session.all_episodes_for_project(project) or [],
            session.all_sequences_for_project(project) or [],
            session.all_shots_for_project(project) or [],
        )

    @property
    def episodic(self):
        return bool(self._episodes)

    def episodes(self):
        return list(self._episodes)

    def sequences(self, episode_id=None):
        """Sequences of `episode_id`, or all of them for a project
        without episodes."""
        if not self.episodic:
            return list(self._sequences)
        return [s for s in self._sequences if s.get("parent_id") == episode_id]

    def shots(self, sequence_id):
        return [s for s in self._shots if s.get("parent_id") == sequence_id]

    def find_shot(self, shot_id):
        return self._shots_by_id.get(shot_id)

    def parents(self, shot):
        """(episode, sequence) dicts of `shot`; either may be None."""
        sequence = self._sequences_by_id.get(shot.get("parent_id"))
        episode = None
        if sequence is not None:
            episode = self._episodes_by_id.get(sequence.get("parent_id"))
        return episode, sequence

    def breadcrumb(self, shot):
        episode, sequence = self.parents(shot)
        names = [shot.get("name", "?")]
        if sequence is not None:
            names.insert(0, sequence.get("name", "?"))
        if episode is not None:
            names.insert(0, episode.get("name", "?"))
        return names


def detect_with_overrides(session, project, document_path, shot_id, task_type_id):
    """Folder detection plus the document's remembered manual picks, which
    win over the folders. Network and plain Python only (no Krita/Qt), so
    it can be tested without Krita.

    Returns a dict -- `entity`, `breadcrumb`, `context` and
    `task_type_name` (the folder name) describe what the picker would
    apply to; `result` is detect_task's (task, breadcrumb, context) or
    None with `error` saying why; `manual` is True when a remembered pick
    was used; `shot_tree` is the loaded ShotTree, if one was needed.
    Raises TaskDetectionError for an unsaved document.
    """
    outcome = {
        "entity": None,
        "breadcrumb": None,
        "context": None,
        "task_type_name": task_type_folder_name(document_path) if document_path else None,
        "shot_tree": None,
        "result": None,
        "error": None,
        "manual": False,
    }
    try:
        entity, breadcrumb, task_type_name, context = detect_entity(
            session, project, document_path
        )
        outcome.update(
            entity=entity, breadcrumb=breadcrumb, task_type_name=task_type_name, context=context
        )
    except ShotNotFound as exc:
        outcome.update(context="shot", error=exc.reason)

    # A remembered shot pick wins over whatever the folders resolved to
    # (there's no anchor folder in Krita to rule shot context out).
    if shot_id or outcome["entity"] is None:
        tree = outcome["shot_tree"] = ShotTree.load(session, project)
        shot = tree.find_shot(shot_id) if shot_id else None
        if shot is not None:
            outcome.update(
                entity=shot, breadcrumb=tree.breadcrumb(shot), context="shot",
                manual=True, error=None,
            )

    if outcome["entity"] is None:
        return outcome

    if task_type_id:
        result = resolve_task_by_type_id(
            session, project, outcome["entity"], outcome["breadcrumb"],
            outcome["context"], task_type_id,
        )
        if result is not None:
            outcome.update(result=result, manual=True)
            return outcome

    try:
        outcome["result"] = resolve_task(
            session, project, outcome["entity"], outcome["breadcrumb"],
            outcome["task_type_name"], outcome["context"],
        )
    except TaskTypeMismatch:
        outcome["error"] = (
            " / ".join(outcome["breadcrumb"])
            + f" — folder task type '{outcome['task_type_name']}' has no matching task in Kitsu"
        )
    return outcome


def detect_task(session, project, document_path):
    """Resolve the Kitsu task matching the document's folder structure.

    Returns:
        tuple: (task, breadcrumb, context) where breadcrumb is a list of
        the resolved names, top-down, including the task type, e.g.
        ["Characters", "Hero", "design"] or ["E01", "SEQ01", "SH010",
        "storyboard"], and context is "asset" or "shot".
    """
    entity, breadcrumb, task_type_name, context = detect_entity(
        session, project, document_path
    )
    return resolve_task(session, project, entity, breadcrumb, task_type_name, context)
