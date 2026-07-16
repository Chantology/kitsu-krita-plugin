"""Thin wrapper around the vendored `gazu` client for the plugin's needs.

All Kitsu/Zou protocol details (URL shape, auth headers, endpoint paths)
are handled by `gazu` itself. This module only adapts its calls to what the
plugin's UI needs and translates failures into specific, user-facing
messages instead of a generic "Login failed".

`gazu` and its own dependencies (`requests`, `urllib3`, `certifi`, `idna`)
are all vendored under vendor/ — Krita's bundled Python isn't expected to
have any of them. Importing them is deliberately deferred to
`KitsuSession.__init__` (i.e. until the user actually clicks Login), not
done at module import time, so that a problem here can never take down
the whole plugin (dock + menu) with it — only the login attempt fails,
with a clear message.
"""


class KitsuConnectionError(Exception):
    """A specific, user-facing reason a Kitsu/Zou call failed."""


def _import_gazu():
    try:
        from . import _vendor  # noqa: F401  (side effect: extends sys.path)
        import gazu
    except Exception as exc:
        raise KitsuConnectionError(
            "Could not load the bundled gazu Kitsu/Zou client "
            f"(vendor/ folder may be missing or incomplete): {exc}"
        ) from exc
    return gazu


def normalize_host(url):
    """Turn a user-entered server URL into the `.../api` host gazu expects."""
    url = (url or "").strip().rstrip("/")
    if not url:
        raise KitsuConnectionError("Server URL is empty.")
    if not url.endswith("/api"):
        url = f"{url}/api"
    return url


class KitsuSession:
    """One authenticated connection to a Kitsu/Zou instance."""

    def __init__(self, host):
        self.host = normalize_host(host)
        self.gazu = _import_gazu()
        self.client = self.gazu.client.create_client(self.host)

    def login(self, email, password):
        exceptions = self.gazu.exception
        try:
            self.gazu.log_in(email, password, client=self.client)
        except exceptions.AuthFailedException as exc:
            raise KitsuConnectionError(
                f"{self.host} rejected the login — check the email/password."
            ) from exc
        except exceptions.RouteNotFoundException as exc:
            raise KitsuConnectionError(
                f"{self.host} has no route at '{exc}' (404). The server URL "
                "is likely wrong for the API — the Zou API may live at a "
                "different host or path than the Kitsu web UI. Ask whoever "
                "manages the server for the correct API URL."
            ) from exc
        except exceptions.NotAllowedException as exc:
            raise KitsuConnectionError(
                f"{self.host} refused the request (403): {exc}"
            ) from exc
        except exceptions.ServerErrorException as exc:
            raise KitsuConnectionError(
                f"{self.host} returned a server error: {exc}"
            ) from exc
        except Exception as exc:
            raise KitsuConnectionError(f"Could not reach {self.host}: {exc}") from exc

    def login_with_token(self, access_token, refresh_token=""):
        self.gazu.client.set_tokens(
            {"access_token": access_token, "refresh_token": refresh_token},
            client=self.client,
        )

    def get_refresh_token(self):
        """The refresh token gazu stored after a successful `login()` (or
        `login_with_refresh_token()`) — save this to restore the session
        later without needing the password again."""
        return self.client.refresh_token

    def login_with_refresh_token(self, refresh_token):
        """Re-authenticate using a previously-saved refresh token instead
        of a password. Raises KitsuConnectionError if it's expired or
        otherwise invalid (the refresh token itself has a server-side
        lifetime, ~15 days by default) — the caller should fall back to a
        normal password/API-token login in that case.
        """
        self.client.refresh_token = refresh_token
        try:
            self.gazu.refresh_access_token(client=self.client)
        except Exception as exc:
            raise KitsuConnectionError(
                f"Saved login for {self.host} has expired or is no longer valid: {exc}"
            ) from exc

    def current_user(self):
        return self.gazu.client.get_current_user(client=self.client)

    def get_person(self, person_id):
        return self.gazu.person.get_person(person_id, client=self.client)

    def download_person_avatar(self, person, file_path):
        return self.gazu.files.download_person_avatar(person, file_path, client=self.client)

    def project(self, project_id):
        return self.gazu.project.get_project(project_id, client=self.client)

    def download_project_avatar(self, project, file_path):
        return self.gazu.files.download_project_avatar(project, file_path, client=self.client)

    def all_task_statuses(self, project):
        return self.gazu.task.all_task_statuses_for_project(project, client=self.client)

    def get_task_status(self, status_id):
        """Fetch a task status directly by id, regardless of whether it's
        in this project's configured status settings (e.g. the global
        default "Todo" status often isn't)."""
        return self.gazu.task.get_task_status(status_id, client=self.client)

    def get_time_spent(self, task, date):
        """Return the list of time-spent entries (one per person) for this
        task on `date` (a "YYYY-MM-DD" string)."""
        return self.gazu.task.get_time_spent(task, date=date, client=self.client)

    def set_time_spent(self, task, person, date, duration_minutes):
        """Set (overwrite, not add to) the logged time for `person` on
        `task` for `date`, in minutes."""
        return self.gazu.task.set_time_spent(
            task, person, date, duration_minutes, client=self.client
        )

    def all_asset_types_for_project(self, project):
        return self.gazu.asset.all_asset_types_for_project(project, client=self.client)

    def get_asset_by_name(self, project, name, asset_type=None):
        return self.gazu.asset.get_asset_by_name(
            project, name, asset_type=asset_type, client=self.client
        )

    def all_task_types_for_project(self, project):
        return self.gazu.task.all_task_types_for_project(project, client=self.client)

    def get_episode_by_name(self, project, name):
        return self.gazu.shot.get_episode_by_name(project, name, client=self.client)

    def get_sequence_by_name(self, project, name, episode=None):
        return self.gazu.shot.get_sequence_by_name(
            project, name, episode=episode, client=self.client
        )

    def get_shot_by_name(self, sequence, name):
        return self.gazu.shot.get_shot_by_name(sequence, name, client=self.client)

    def get_task_by_entity(self, entity, task_type):
        return self.gazu.task.get_task_by_entity(entity, task_type, client=self.client)

    def all_comments_for_task(self, task):
        return self.gazu.task.all_comments_for_task(task, client=self.client)

    def update_comment_checklist(self, comment_id, checklist):
        """Persist a comment's checklist (with updated "checked" flags).

        Sends a body with ONLY `checklist` (no "id") — confirmed against
        Zou's own server source and Kitsu's own frontend source that this
        exactly matches the wire payload Kitsu's own web UI sends for a
        checklist-only toggle. This alone was NOT enough to resolve a
        real report of a non-author (an artist assigned to the task,
        confirmed on the project team, against a Zou server confirmed
        newer than the version that added the "assigned team member can
        toggle checklist" exception) still getting denied — every
        condition checkable from Zou's public source said this should
        succeed. Since gazu's own exception classes only preserve the
        bare URL path and discard the response body on failure, this
        bypasses `gazu.client.put()`/`gazu.task.update_comment()` and
        makes the request directly, so a future failure surfaces Zou's
        actual JSON error body (which almost always has far more detail
        than a 404/403 status code alone) instead of just a route string.
        """
        url = self.gazu.client.get_full_url(f"data/comments/{comment_id}", client=self.client)
        headers = self.client.make_auth_header()
        headers["Content-Type"] = "application/json"
        try:
            response = self.client.session.put(
                url, json={"checklist": checklist}, headers=headers
            )
        except Exception as exc:
            raise KitsuConnectionError(
                f"Could not reach {self.host} to update the checklist: {exc}"
            ) from exc

        if 200 <= response.status_code < 300:
            return response.json()

        try:
            body = response.json()
        except ValueError:
            body = response.text
        raise KitsuConnectionError(
            f"Kitsu rejected this checklist update — HTTP {response.status_code} "
            f"on PUT {url}. Server response: {body}"
        )

    def publish_preview(self, task, task_status, comment="", preview_file_path=None, revision=None):
        """Publish a comment with a proper Kitsu preview/revision attached.

        Unlike a plain comment attachment, this shows up in the task's
        revision history (comparable across versions, drawover-able in
        review sessions). `revision` pins the revision number explicitly
        (e.g. parsed from a "_v002" filename) instead of letting Kitsu's
        counter auto-increment.

        Returns (comment, preview_file) — pass `preview_file` (along with
        the same `task`/`comment`) to `add_extra_preview` to attach more
        images to this *same* revision.
        """
        return self.gazu.task.publish_preview(
            task,
            task_status,
            comment=comment,
            preview_file_path=preview_file_path,
            revision=revision,
            client=self.client,
        )

    def add_comment(self, task, task_status, comment=""):
        """Post a status change + comment with no preview file attached —
        used when no image was explicitly queued to publish, so a status
        change (e.g. "starting work") never implicitly exports and
        publishes the canvas as a revision nobody asked to share."""
        return self.gazu.task.add_comment(
            task, task_status, comment=comment, client=self.client
        )

    def add_extra_preview(self, task, comment, reference_preview_file, file_path):
        """Attach another image to the same revision as
        `reference_preview_file` (e.g. a second layer-visibility
        variation) — a proper extra preview, not a plain attachment, so it
        gets its own drawover support and stays grouped under the same
        revision number rather than becoming a separate, unversioned file.
        """
        return self.gazu.task.add_extra_preview(
            task, comment, reference_preview_file, preview_file_path=file_path, client=self.client
        )

    def download_attachment(self, attachment_file, file_path):
        return self.gazu.files.download_attachment_file(
            attachment_file, file_path, client=self.client
        )

    def download_preview(self, preview_file, file_path):
        return self.gazu.files.download_preview_file(
            preview_file, file_path, client=self.client
        )
