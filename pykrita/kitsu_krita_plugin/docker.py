"""Dock widget UI for the Kitsu/Zou plugin."""

import datetime
import os
import tempfile
import time

from PyQt5.QtCore import QByteArray, QEvent, Qt, QTimer
from PyQt5.QtGui import QImage, QPalette, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from krita import DockWidget, Krita, InfoObject

from . import settings
from .annotations import picture_annotation_objects, render_annotations
from .collapsible_section import CollapsibleSection
from .kitsu_api import (
    KitsuConnectionError,
    KitsuSession,
    TwoFactorRequired,
    WrongTwoFactorCode,
    two_factor_prompt,
)
from .task_detection import TaskDetectionError, TaskTypeMismatch, detect_task
from .versioning import parse_version_number


class KitsuDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kitsu")

        self.session = None
        self.project = None
        self.task = None
        self._pending_override_entity = None  # entity awaiting a manual task-type pick
        self._pending_override_breadcrumb = None
        self._document_path_for_override = None
        self._attachment_cache = {}  # attachment id -> local file path
        self._preview_cache = {}  # preview (revision) id -> local file path
        self._pending_publish_images = []  # image paths queued to publish (first = main preview, rest = variations)
        self._two_factor_methods = []  # set once login raises TwoFactorRequired
        self._status_is_error = None  # None until a status message is shown

        # Timesheet auto-advance: while active, the slider auto-advances
        # from `_time_tracking_base_minutes` (today's already-logged time
        # as of the last server sync) by however many minutes have elapsed
        # since `_time_tracking_start`. Any manual slider interaction turns
        # it off until the next task (re-)detection or a successful submit
        # restarts it -- matching the Blender/Maya/Houdini/Nuke/C4D ports.
        # `_timesheet_loaded_task_id` lets re-detecting the same task (e.g.
        # clicking "Detect Task" again) leave the running clock alone.
        self._time_tracking_active = False
        self._time_tracking_base_minutes = 0
        self._time_tracking_start = None
        self._timesheet_loaded_task_id = None
        self._timesheet_timer = QTimer(self)
        self._timesheet_timer.setInterval(30_000)  # 30s: plenty for minute-level display
        self._timesheet_timer.timeout.connect(self._on_timesheet_tick)

        root = QWidget(self)
        layout = QVBoxLayout(root)

        self.status_label = QLabel("Not connected")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addWidget(self._build_section("connection", "Connection", self._build_connection_group()))
        layout.addWidget(self._build_section("task", "Task", self._build_task_group()))
        layout.addWidget(self._build_section("feedback", "Feedback", self._build_feedback_group()))
        layout.addWidget(self._build_section("publish", "Publish", self._build_publish_group()))
        layout.addStretch(1)

        self.setWidget(root)
        self._load_settings()

    # ---- Krita DockWidget API ----

    def canvasChanged(self, canvas):
        pass

    # ---- UI construction ----

    def _build_section(self, name, title, content_widget):
        section = CollapsibleSection(
            title, content_widget, collapsed=settings.load_section_collapsed(name)
        )
        section.toggled.connect(
            lambda expanded: settings.save_section_collapsed(name, not expanded)
        )
        return section

    def _build_connection_group(self):
        group = QWidget()
        form = QFormLayout(group)

        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("https://kitsu.yourstudio.com")
        self.email_edit = QLineEdit()
        self.email_edit.textChanged.connect(self._on_login_identity_changed)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.project_id_edit = QLineEdit()
        self.project_id_edit.setToolTip(
            "Paste the project's ID (a long hash) from its Kitsu URL. "
            "The project name and thumbnail will show in the Task section "
            "once you log in."
        )
        self.project_id_edit.textChanged.connect(self._on_project_id_changed)
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText("Optional: API token instead of password")
        self.token_edit.textChanged.connect(self._on_login_identity_changed)

        form.addRow("Server URL", self.server_edit)
        form.addRow("Email", self.email_edit)
        form.addRow("Password", self.password_edit)
        form.addRow("Project ID", self.project_id_edit)
        form.addRow("API token", self.token_edit)

        # Only shown once the server has said this account needs a 2FA
        # code (see _show_two_factor / TwoFactorRequired).
        form.addRow(self._build_two_factor_group())

        self.login_button = QPushButton("Login")
        self.login_button.clicked.connect(self._on_login_clicked)
        form.addRow(self.login_button)

        self.user_welcome_label = QLabel("")
        self.user_welcome_label.setVisible(False)
        form.addRow(self.user_welcome_label)

        self.user_thumbnail_label = QLabel()
        self.user_thumbnail_label.setVisible(False)
        form.addRow(self.user_thumbnail_label)

        return group

    _TWO_FACTOR_LABELS = {
        "totp": "Authenticator App",
        "email_otp": "Email Code",
        "recovery_code": "Recovery Code",
    }

    def _build_two_factor_group(self):
        self.two_factor_group = QGroupBox("Two-Factor Authentication")
        form = QFormLayout(self.two_factor_group)

        self.two_factor_method_label = QLabel("Method")
        self.two_factor_method_combo = QComboBox()
        self.two_factor_method_combo.setToolTip("Which two-factor method to enter a code for")
        self.two_factor_method_combo.currentIndexChanged.connect(
            self._on_two_factor_method_changed
        )
        form.addRow(self.two_factor_method_label, self.two_factor_method_combo)

        self.two_factor_code_edit = QLineEdit()
        self.two_factor_code_edit.setToolTip(
            "Two-factor authentication code (only asked for when your account has 2FA enabled)"
        )
        form.addRow("Code", self.two_factor_code_edit)

        self.send_email_code_button = QPushButton("Send Code")
        self.send_email_code_button.setToolTip("Email a new two-factor code to this account")
        self.send_email_code_button.clicked.connect(self._on_send_email_code_clicked)
        form.addRow(self.send_email_code_button)

        self.two_factor_group.setVisible(False)
        return self.two_factor_group

    def _build_task_group(self):
        group = QWidget()
        layout = QVBoxLayout(group)

        self.project_thumbnail_label = QLabel()
        self.project_thumbnail_label.setVisible(False)
        layout.addWidget(self.project_thumbnail_label)

        self.project_name_label = QLabel("")
        layout.addWidget(self.project_name_label)

        self.task_label = QLabel("No task detected")
        self.task_label.setWordWrap(True)
        layout.addWidget(self.task_label)
        self.refresh_button = QPushButton("Detect Task")
        self.refresh_button.setToolTip("Detect task from current document")
        self.refresh_button.clicked.connect(self._on_refresh_task_clicked)
        layout.addWidget(self.refresh_button)

        self.task_type_override_label = QLabel("")
        self.task_type_override_label.setWordWrap(True)
        self.task_type_override_label.setVisible(False)
        layout.addWidget(self.task_type_override_label)

        self.task_type_override_combo = QComboBox()
        self.task_type_override_combo.setVisible(False)
        self.task_type_override_combo.currentIndexChanged.connect(
            self._on_task_type_override_changed
        )
        layout.addWidget(self.task_type_override_combo)

        self.task_status_label = QLabel("")
        self.task_status_label.setVisible(False)
        layout.addWidget(self.task_status_label)

        self.task_due_date_label = QLabel("")
        self.task_due_date_label.setVisible(False)
        layout.addWidget(self.task_due_date_label)

        self.timesheet_widget = self._build_timesheet_widget()
        self.timesheet_widget.setVisible(False)
        layout.addWidget(self.timesheet_widget)

        return group

    _TIME_SLIDER_MAX_MINUTES = 12 * 60
    _TIME_SLIDER_STEP_MINUTES = 30
    _TIME_PRESETS_MINUTES = (60, 240, 480)  # 1h, 4h, 8h

    def _build_timesheet_widget(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.timesheet_logged_label = QLabel("")
        layout.addWidget(self.timesheet_logged_label)

        self.timesheet_slider = QSlider(Qt.Horizontal)
        self.timesheet_slider.setMinimum(0)
        self.timesheet_slider.setMaximum(self._TIME_SLIDER_MAX_MINUTES)
        self.timesheet_slider.setSingleStep(self._TIME_SLIDER_STEP_MINUTES)
        self.timesheet_slider.setPageStep(self._TIME_SLIDER_STEP_MINUTES * 2)
        self.timesheet_slider.setTickInterval(self._TIME_SLIDER_STEP_MINUTES * 2)
        self.timesheet_slider.setTickPosition(QSlider.TicksBelow)
        self.timesheet_slider.valueChanged.connect(self._on_timesheet_slider_changed)
        # actionTriggered fires only for user-driven changes (drag,
        # arrow/page keys, click-to-page) — never for our own setValue()
        # calls — so this is what turns off auto-tracking the moment the
        # user actually touches the slider themselves.
        self.timesheet_slider.actionTriggered.connect(self._on_timesheet_slider_user_interacted)
        layout.addWidget(self.timesheet_slider)

        self.timesheet_value_label = QLabel("Selected: 0h 0m")
        layout.addWidget(self.timesheet_value_label)

        presets_layout = QHBoxLayout()
        for minutes in self._TIME_PRESETS_MINUTES:
            button = QPushButton(self._format_minutes(minutes))
            button.clicked.connect(
                lambda _checked=False, m=minutes: self._apply_timesheet_preset(m)
            )
            presets_layout.addWidget(button)
        layout.addLayout(presets_layout)

        self.timesheet_submit_button = QPushButton("Submit Time")
        self.timesheet_submit_button.clicked.connect(self._on_timesheet_submit_clicked)
        layout.addWidget(self.timesheet_submit_button)

        return widget

    def _format_minutes(self, minutes):
        hours, remaining_minutes = divmod(int(minutes), 60)
        return f"{hours}h {remaining_minutes}m"

    def _build_feedback_group(self):
        group = QWidget()
        layout = QVBoxLayout(group)

        self.comments_list = QListWidget()
        self.comments_list.currentItemChanged.connect(self._on_comment_selected)
        layout.addWidget(self.comments_list)

        self.comment_detail_area = QScrollArea()
        self.comment_detail_area.setWidgetResizable(True)
        self.comment_detail_area.setMinimumHeight(160)
        self.comment_detail_widget = QWidget()
        self.comment_detail_layout = QVBoxLayout(self.comment_detail_widget)
        self.comment_detail_layout.setAlignment(Qt.AlignTop)
        self.comment_detail_area.setWidget(self.comment_detail_widget)
        layout.addWidget(self.comment_detail_area)

        self.download_attachment_button = QPushButton("Save Attachment(s)")
        self.download_attachment_button.setToolTip(
            "Save attachment(s) from the selected comment to disk"
        )
        self.download_attachment_button.clicked.connect(
            self._on_download_attachment_clicked
        )
        layout.addWidget(self.download_attachment_button)
        return group

    def _build_publish_group(self):
        group = QWidget()
        layout = QVBoxLayout(group)

        self.status_combo = QComboBox()
        layout.addWidget(self.status_combo)

        self.comment_edit = QTextEdit()
        self.comment_edit.setPlaceholderText("Comment")
        layout.addWidget(self.comment_edit)

        self.publish_images_list = QListWidget()
        self.publish_images_list.setToolTip(
            "Images to publish with this update, queued explicitly here — "
            "the canvas is never exported automatically. The first image "
            "becomes the main preview; any additional ones are added as "
            "variations on the SAME revision (e.g. different layer "
            "visibility). If this list is empty, Publish just posts the "
            "status change and comment below with no preview attached."
        )
        self.publish_images_list.setMaximumHeight(70)
        layout.addWidget(self.publish_images_list)

        images_buttons_layout = QHBoxLayout()
        self.add_publish_image_button = QPushButton("Add Image")
        self.add_publish_image_button.setToolTip(
            "Export the current canvas right now and queue it as an "
            "image to publish (e.g. toggle some layers, then Add Image "
            "again for another variation) — they'll all share the same "
            "revision."
        )
        self.add_publish_image_button.clicked.connect(self._on_add_publish_image_clicked)
        images_buttons_layout.addWidget(self.add_publish_image_button)

        self.clear_publish_images_button = QPushButton("Clear")
        self.clear_publish_images_button.setToolTip("Remove all queued images")
        self.clear_publish_images_button.clicked.connect(self._on_clear_publish_images_clicked)
        images_buttons_layout.addWidget(self.clear_publish_images_button)
        layout.addLayout(images_buttons_layout)

        self.publish_button = QPushButton("Publish")
        self.publish_button.setToolTip(
            "Post the status change and comment. If any image(s) are "
            "queued above, publishes them as a new revision (first = "
            "main preview, rest = variations); otherwise posts the "
            "status/comment alone, with no preview — the canvas is "
            "never exported automatically."
        )
        self.publish_button.clicked.connect(self._on_publish_clicked)
        layout.addWidget(self.publish_button)

        return group

    # ---- settings ----

    def _load_settings(self):
        values = settings.load()
        self.server_edit.setText(values["server_url"])
        self.email_edit.setText(values["email"])
        self.project_id_edit.setText(values["project_id"])
        self.token_edit.setText(values["api_token"])
        self._try_restore_session(values)

    def _save_settings(self):
        values = {
            "server_url": self.server_edit.text().strip(),
            "email": self.email_edit.text().strip(),
            "project_id": self.project_id_edit.text().strip(),
            "api_token": self.token_edit.text().strip(),
        }
        if self.session is not None:
            # Saved alongside the rest so the next Krita launch can
            # restore the session without asking for the password again
            # — see settings.py for why this is safe to persist.
            values["refresh_token"] = self.session.get_refresh_token() or ""
        settings.save(values)

    # ---- status helper ----

    def _set_status(self, message, is_error=False):
        self.status_label.setText(message)
        self._status_is_error = is_error
        self._apply_status_color()

    def _apply_status_color(self):
        # Krita ships both dark and bright themes. The dark #0b7a0b/#b00020
        # read well on bright ones but nearly vanish on the default dark
        # theme (~1.8:1 contrast), so dark backgrounds get light variants.
        dark_background = self.palette().color(QPalette.Window).lightness() < 128
        if self._status_is_error:
            color = "#ff9999" if dark_background else "#b00020"
        else:
            color = "#81c784" if dark_background else "#0b7a0b"
        self.status_label.setStyleSheet(f"color: {color};")

    def changeEvent(self, event):
        super().changeEvent(event)
        # Switching Krita's theme while a status message is showing.
        # getattr: a palette change can arrive during DockWidget.__init__,
        # before this attribute exists.
        if event.type() == QEvent.PaletteChange and getattr(self, "_status_is_error", None) is not None:
            self._apply_status_color()

    # ---- actions ----

    def _try_restore_session(self, values):
        server_url = values["server_url"].strip()
        project_id = values["project_id"].strip()
        refresh_token = values.get("refresh_token", "").strip()
        if not (server_url and project_id and refresh_token):
            return  # nothing saved from a previous session to restore

        self._set_status("Restoring previous session...")
        try:
            session = KitsuSession(server_url)
            session.login_with_refresh_token(refresh_token)
            project = session.project(project_id)
        except Exception as exc:
            self._set_status(
                f"Could not restore the previous session ({exc}). Please log in again.",
                is_error=True,
            )
            return

        self._on_authenticated(session, project, project_id, restored=True)

    def _on_login_clicked(self):
        server_url = self.server_edit.text().strip()
        email = self.email_edit.text().strip()
        password = self.password_edit.text()
        project_id = self.project_id_edit.text().strip()
        token = self.token_edit.text().strip()

        if not server_url or not project_id:
            QMessageBox.warning(self, "Kitsu", "Please fill in the server URL and project ID.")
            return
        if not token and not (email and password):
            QMessageBox.warning(self, "Kitsu", "Please enter an email/password or an API token.")
            return

        # Only sent once the server has asked for it (TwoFactorRequired below).
        two_factor_method = None
        two_factor_code = None
        if self._two_factor_methods and not token:
            two_factor_method = self.two_factor_method_combo.currentData()
            two_factor_code = self.two_factor_code_edit.text().strip()
            if not two_factor_code:
                self._set_status("Enter your two-factor code.", is_error=True)
                return

        self._set_status("Connecting...")
        try:
            session = KitsuSession(server_url)
            if token:
                session.login_with_token(token)
            else:
                session.login(email, password, two_factor_method, two_factor_code)
            project = session.project(project_id)
        except TwoFactorRequired as exc:
            # Password was right — reveal the code field and ask again.
            self._show_two_factor(exc.methods, exc.preferred)
            self._set_status(str(exc))
            return
        except WrongTwoFactorCode as exc:
            self.two_factor_code_edit.clear()
            self._set_status(str(exc), is_error=True)
            return
        except KitsuConnectionError as exc:
            self._hide_two_factor()
            self._set_status(str(exc), is_error=True)
            return
        except Exception as exc:
            self._hide_two_factor()
            self._set_status(f"Unexpected error: {exc}", is_error=True)
            return

        self.password_edit.clear()
        self._hide_two_factor()
        self._on_authenticated(session, project, project_id)

    def _show_two_factor(self, methods, preferred):
        combo = self.two_factor_method_combo
        combo.blockSignals(True)  # selecting the preferred method isn't a user switch
        combo.clear()
        for method in methods:
            combo.addItem(self._TWO_FACTOR_LABELS.get(method, method), method)
        combo.setCurrentIndex(max(0, combo.findData(preferred)))
        combo.blockSignals(False)

        show_method = len(methods) > 1
        self.two_factor_method_label.setVisible(show_method)
        combo.setVisible(show_method)
        self.two_factor_code_edit.clear()
        self._update_send_email_code_visibility()
        self._two_factor_methods = list(methods)
        self.two_factor_group.setVisible(True)
        self.login_button.setText("Verify & Log In")

    def _hide_two_factor(self):
        self._two_factor_methods = []
        self.two_factor_group.setVisible(False)
        self.two_factor_code_edit.clear()
        self.login_button.setText("Login")

    def _update_send_email_code_visibility(self):
        self.send_email_code_button.setVisible(
            self.two_factor_method_combo.currentData() == "email_otp"
        )

    def _on_two_factor_method_changed(self, _index):
        method = self.two_factor_method_combo.currentData()
        self._update_send_email_code_visibility()
        # Switching to email doesn't send anything by itself — the Send
        # Code button does that, so a stray click can't spam the inbox.
        if method == "email_otp":
            self._set_status("Click Send Code, then enter the emailed code and log in again.")
        else:
            self._set_status(two_factor_prompt(method))

    def _on_send_email_code_clicked(self):
        server_url = self.server_edit.text().strip()
        email = self.email_edit.text().strip()
        if not (server_url and email):
            self._set_status("Enter the server URL and email first.", is_error=True)
            return

        self._set_status("Sending code...")
        try:
            KitsuSession(server_url).send_email_code(email)
        except Exception as exc:
            self._set_status(str(exc), is_error=True)
            return
        self._set_status(two_factor_prompt("email_otp"))

    def _on_authenticated(self, session, project, project_id, restored=False):
        self.session = session
        self.project = project
        self._save_settings()
        project_name = project.get("name", project_id)
        self.project_name_label.setText(project_name)
        self._update_project_thumbnail(project)
        self._update_user_welcome(session)
        suffix = " (restored session)" if restored else ""
        self._set_status(f"Connected to {session.host} — project '{project_name}'{suffix}")
        self._populate_statuses()
        self._on_refresh_task_clicked()
        self._timesheet_timer.start()

    def _update_user_welcome(self, session):
        try:
            user = session.current_user()
        except Exception:
            self.user_welcome_label.setVisible(False)
            self.user_thumbnail_label.setVisible(False)
            return

        name = user.get("full_name") or user.get("first_name") or "there"
        self.user_welcome_label.setText(f"Welcome, {name}")
        self.user_welcome_label.setVisible(True)

        self.user_thumbnail_label.setVisible(False)
        try:
            target_path = os.path.join(
                tempfile.gettempdir(), f"kitsu_user_thumbnail_{user['id']}.png"
            )
            session.download_person_avatar(user, target_path)
            pixmap = QPixmap(target_path)
        except Exception:
            return  # not every user has an avatar set

        if pixmap.isNull():
            return

        self.user_thumbnail_label.setPixmap(
            pixmap.scaledToWidth(80, Qt.SmoothTransformation) if pixmap.width() > 80 else pixmap
        )
        self.user_thumbnail_label.setVisible(True)

    def _on_login_identity_changed(self, _text):
        # Changing email/token means logging in as someone potentially
        # different — clear the stale welcome banner until the next
        # successful login confirms who it actually is.
        self.user_welcome_label.setText("")
        self.user_welcome_label.setVisible(False)
        self.user_thumbnail_label.clear()
        self.user_thumbnail_label.setVisible(False)

    def _update_project_thumbnail(self, project):
        self.project_thumbnail_label.setVisible(False)
        try:
            target_path = os.path.join(
                tempfile.gettempdir(), f"kitsu_project_thumbnail_{project['id']}.png"
            )
            self.session.download_project_avatar(project, target_path)
            pixmap = QPixmap(target_path)
        except Exception:
            return  # most projects have no thumbnail set — that's normal

        if pixmap.isNull():
            return

        self.project_thumbnail_label.setPixmap(
            pixmap.scaledToWidth(200, Qt.SmoothTransformation) if pixmap.width() > 200 else pixmap
        )
        self.project_thumbnail_label.setVisible(True)

    def _on_project_id_changed(self, _text):
        # The resolved name/thumbnail only apply to the ID that was
        # actually logged in with — clear them so stale info never lingers
        # after the ID is edited to something else.
        self.project_name_label.setText("")
        self.project_thumbnail_label.clear()
        self.project_thumbnail_label.setVisible(False)

    def _populate_statuses(self):
        self.status_combo.clear()
        if not (self.session and self.project):
            return
        try:
            statuses = self.session.all_task_statuses(self.project)
        except Exception as exc:
            self._set_status(f"Could not load task statuses: {exc}", is_error=True)
            return
        for status in statuses:
            self.status_combo.addItem(status.get("name", status.get("short_name", "?")), status)

    def _on_refresh_task_clicked(self):
        if not (self.session and self.project):
            QMessageBox.warning(self, "Kitsu", "Log in first.")
            return

        document = Krita.instance().activeDocument()
        document_path = document.fileName() if document else None
        self._document_path_for_override = document_path
        self._hide_task_type_override()

        try:
            task, breadcrumb, task_type_name = detect_task(
                self.session, self.project, document_path
            )
        except TaskTypeMismatch as exc:
            self._handle_task_type_mismatch(exc, document_path)
            return
        except TaskDetectionError as exc:
            self.task = None
            self.task_label.setText(str(exc))
            self.task_status_label.setVisible(False)
            self.task_due_date_label.setVisible(False)
            self.timesheet_widget.setVisible(False)
            return

        self._apply_resolved_task(task, breadcrumb, task_type_name)

    def _apply_resolved_task(self, task, breadcrumb, task_type_name):
        self._hide_task_type_override()
        self.task = task
        self.task_label.setText(" / ".join(breadcrumb + [task_type_name]))
        self._update_task_details(task)
        self._load_comments()

    def _hide_task_type_override(self):
        self.task_type_override_label.setVisible(False)
        self.task_type_override_combo.setVisible(False)
        self.task_type_override_combo.blockSignals(True)
        self.task_type_override_combo.clear()
        self.task_type_override_combo.blockSignals(False)
        self._pending_override_entity = None
        self._pending_override_breadcrumb = None

    def _handle_task_type_mismatch(self, exc, document_path):
        remembered_id = settings.load_task_type_override(document_path) if document_path else None
        if remembered_id:
            task_type = next(
                (
                    t
                    for t in self.session.all_task_types_for_project(self.project)
                    if t.get("id") == remembered_id
                ),
                None,
            )
            if task_type:
                task = self.session.get_task_by_entity(exc.entity, task_type)
                if task:
                    self._apply_resolved_task(
                        task, exc.breadcrumb, task_type.get("name", "?")
                    )
                    return

        self.task = None
        self.task_status_label.setVisible(False)
        self.task_due_date_label.setVisible(False)
        self.timesheet_widget.setVisible(False)

        self._pending_override_entity = exc.entity
        self._pending_override_breadcrumb = exc.breadcrumb

        self.task_label.setText(
            " / ".join(exc.breadcrumb) + " — task type not recognized"
        )
        self.task_type_override_label.setText(
            f"Folder task type {exc.task_type_name!r} didn't match any task "
            "type in this project — pick one:"
        )
        self.task_type_override_label.setVisible(True)

        self.task_type_override_combo.blockSignals(True)
        self.task_type_override_combo.addItem("Select task type…", None)
        for task_type in self.session.all_task_types_for_project(self.project):
            self.task_type_override_combo.addItem(
                task_type.get("name", "?"), task_type
            )
        self.task_type_override_combo.setCurrentIndex(0)
        self.task_type_override_combo.blockSignals(False)
        self.task_type_override_combo.setVisible(True)

    def _on_task_type_override_changed(self, _index):
        task_type = self.task_type_override_combo.currentData()
        if task_type is None or self._pending_override_entity is None:
            return

        task = self.session.get_task_by_entity(self._pending_override_entity, task_type)
        if task is None:
            QMessageBox.warning(
                self,
                "Kitsu",
                f"{task_type.get('name', '?')!r} is a valid task type, but this "
                "entity doesn't have a task of that type yet in Kitsu.",
            )
            return

        document_path = self._document_path_for_override
        if document_path:
            settings.save_task_type_override(document_path, task_type.get("id"))

        self._apply_resolved_task(
            task, self._pending_override_breadcrumb, task_type.get("name", "?")
        )

    def _update_task_details(self, task):
        status = self._resolve_task_status(task)
        if status:
            name = status.get("name") or status.get("short_name") or "?"
            self.task_status_label.setText(f"Status: {name}")
            self.task_status_label.setVisible(True)
        else:
            self.task_status_label.setVisible(False)

        due_date = self._format_due_date(task.get("due_date"))
        if due_date:
            self.task_due_date_label.setText(f"Due: {due_date}")
            self.task_due_date_label.setVisible(True)
        else:
            self.task_due_date_label.setVisible(False)

        self._load_timesheet(task)

    def _resolve_task_status(self, task):
        status_id = task.get("task_status_id")
        if not status_id:
            return None
        try:
            statuses = self.session.all_task_statuses(self.project)
            for status in statuses:
                if status.get("id") == status_id:
                    return status
        except Exception:
            pass

        # Not in this project's configured status list — e.g. "Todo" is a
        # global default every new task starts with, which studios don't
        # always add to their project's own status settings, so it can be
        # legitimately missing here even though the task genuinely has it.
        # Fetch it directly by id rather than hiding the status entirely.
        try:
            return self.session.get_task_status(status_id)
        except Exception:
            return None

    def _format_due_date(self, raw_value):
        if not raw_value:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            return raw_value

    # ---- timesheet ----

    def _on_timesheet_slider_changed(self, minutes):
        suffix = " (auto)" if self._time_tracking_active else ""
        self.timesheet_value_label.setText(f"Selected: {self._format_minutes(minutes)}{suffix}")

    def _on_timesheet_slider_user_interacted(self, _action):
        # The user just dragged/keyed the slider themselves — stop
        # auto-advancing so it doesn't fight their manual choice; it
        # resumes on the next task (re-)detection or a successful submit.
        # actionTriggered fires *before* Qt applies the action's value
        # change, so the label refresh (to drop the "(auto)" suffix) is
        # deferred to right after — otherwise a user action that doesn't
        # actually change the value would leave the stale "(auto)" suffix
        # showing indefinitely, since no valueChanged would follow to
        # refresh it.
        self._time_tracking_active = False
        QTimer.singleShot(
            0, lambda: self._on_timesheet_slider_changed(self.timesheet_slider.value())
        )

    def _apply_timesheet_preset(self, minutes):
        # A preset button is just as much a manual override as dragging
        # the slider — setValue() alone wouldn't turn tracking off (it
        # doesn't fire actionTriggered), so this does it explicitly. The
        # label refresh below is unconditional (not left to valueChanged)
        # since setValue() is a no-op — no signal at all — if the preset
        # happens to match the slider's current value already.
        self._time_tracking_active = False
        self.timesheet_slider.setValue(minutes)
        self._on_timesheet_slider_changed(self.timesheet_slider.value())

    def _start_time_tracking(self, base_minutes):
        """(Re)start auto-advancing the slider from `base_minutes` (the
        already-logged time as of now) — called on every task
        (re-)detection, so the slider always resumes counting up from the
        latest known-good total. Sets the slider to `base_minutes` itself
        (capped to its 12h range), so callers don't need their own
        separate setValue step."""
        self._time_tracking_active = True
        self._time_tracking_base_minutes = base_minutes
        self._time_tracking_start = time.monotonic()

        self.timesheet_slider.blockSignals(True)
        self.timesheet_slider.setValue(min(int(base_minutes), self._TIME_SLIDER_MAX_MINUTES))
        self.timesheet_slider.blockSignals(False)
        self._on_timesheet_slider_changed(self.timesheet_slider.value())

    def _on_timesheet_tick(self):
        if not self._time_tracking_active or self.task is None:
            return
        elapsed_minutes = int((time.monotonic() - self._time_tracking_start) // 60)
        minutes = min(
            self._time_tracking_base_minutes + elapsed_minutes,
            self._TIME_SLIDER_MAX_MINUTES,
        )
        self.timesheet_slider.setValue(minutes)

    def _load_timesheet(self, task):
        self.timesheet_widget.setVisible(True)

        if task.get("id") == self._timesheet_loaded_task_id:
            return  # same task as last time — leave the running clock alone
        self._timesheet_loaded_task_id = task.get("id")

        self.timesheet_slider.blockSignals(True)
        self.timesheet_slider.setValue(0)
        self.timesheet_slider.blockSignals(False)
        self.timesheet_value_label.setText("Selected: 0h 0m")
        self.timesheet_logged_label.setText("Logged today: checking...")

        try:
            person = self.session.current_user()
            today = datetime.date.today().isoformat()
            result = self.session.get_time_spent(task, today)
        except Exception as exc:
            self.timesheet_logged_label.setText(f"Could not load logged time: {exc}")
            return

        # The endpoint returns a dict keyed by person id (plus a "total"
        # key for everyone combined), not a list — each person's value is
        # itself a list of time-spent entries for that day.
        person_entries = (result or {}).get(person.get("id")) or []
        minutes_today = sum(entry.get("duration") or 0 for entry in person_entries)

        self.timesheet_logged_label.setText(f"Logged today: {self._format_minutes(minutes_today)}")

        # (Re)start the auto-advance clock from this task's already-logged
        # baseline, minute by minute, so it climbs from what's really
        # logged today rather than from wherever the previous task left
        # off (or from a coarse half-hour rounding of it).
        self._start_time_tracking(minutes_today)

    def _on_timesheet_submit_clicked(self):
        if not (self.session and self.task):
            QMessageBox.warning(self, "Kitsu", "Detect a task before submitting time.")
            return

        try:
            person = self.session.current_user()
        except Exception as exc:
            QMessageBox.warning(self, "Kitsu", f"Could not identify the current user: {exc}")
            return

        # The slider itself only snaps to _TIME_SLIDER_STEP_MINUTES when
        # dragged by hand -- the auto-advance timer sets it minute-by-minute
        # (see _on_timesheet_tick), so a Submit click mid-tick could
        # otherwise submit an odd value like 80, which Kitsu's web
        # timesheet then shows as a repeating decimal ("1.3333333h").
        step = self._TIME_SLIDER_STEP_MINUTES
        minutes = round(self.timesheet_slider.value() / step) * step
        today = datetime.date.today().isoformat()

        try:
            self.session.set_time_spent(self.task, person, today, minutes)
        except Exception as exc:
            QMessageBox.warning(self, "Kitsu", f"Could not submit time: {exc}")
            return

        self.timesheet_logged_label.setText(f"Logged today: {self._format_minutes(minutes)}")
        self._set_status(f"Submitted {self._format_minutes(minutes)} for today.")

    def _load_comments(self):
        self.comments_list.clear()
        self._clear_attachment_preview()
        if not (self.session and self.task):
            return
        try:
            comments = self.session.all_comments_for_task(self.task)
        except Exception as exc:
            self._set_status(f"Could not load comments: {exc}", is_error=True)
            return

        for comment in comments:
            status = self._resolve_comment_status(comment)
            person = self._resolve_comment_author(comment)
            text = comment.get("text", "")
            attachments = comment.get("attachment_files") or []
            label = f"{status} - {person}: {text}" if status else f"{person}: {text}"

            revision_label = self._resolve_comment_revision_label(comment)
            if revision_label:
                label = f"{revision_label} - {label}"

            if attachments:
                label += f"  [{len(attachments)} attachment(s)]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, comment)
            self.comments_list.addItem(item)

    def _resolve_comment_status(self, comment):
        status = comment.get("task_status") or {}
        name = status.get("short_name") or status.get("name") or ""
        return name.upper()

    def _resolve_comment_revision_label(self, comment):
        # A comment only carries "previews" when it published a new
        # version (e.g. via this plugin's Publish button) — a plain
        # comment has none, so the label stays unchanged for those.
        previews = comment.get("previews") or []
        revisions = [p.get("revision") for p in previews if p.get("revision") is not None]
        if not revisions:
            return None
        if len(revisions) == 1:
            return f"Revision {revisions[0]}"
        return "Revisions " + ", ".join(str(r) for r in revisions)

    def _resolve_comment_author(self, comment):
        person = comment.get("person") or {}
        full_name = person.get("full_name")
        if full_name:
            return full_name

        # Kitsu can omit the nested "person" object (e.g. client-portal
        # accounts have studio identities hidden) even for a comment's own
        # author; fall back to a direct person lookup by id.
        person_id = comment.get("person_id")
        if person_id and self.session:
            try:
                person = self.session.get_person(person_id)
            except Exception:
                person = None
            if person and person.get("full_name"):
                return person["full_name"]

        return "Unknown"

    # ---- comment detail (checklist + attachment preview) ----

    _IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp", "tif", "tiff", "webp"}

    def _clear_attachment_preview(self):
        while self.comment_detail_layout.count():
            child = self.comment_detail_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()

    def _on_comment_selected(self, current, previous):
        self._clear_attachment_preview()
        if current is None:
            self.comment_detail_area.setVisible(False)
            return

        comment = current.data(Qt.UserRole)
        has_content = False

        checklist = comment.get("checklist") or []
        if checklist:
            has_content = True
            self.comment_detail_layout.addWidget(self._section_label("Checklist"))
            for index, checklist_item in enumerate(checklist):
                checkbox = QCheckBox(checklist_item.get("text", ""))
                checkbox.setChecked(bool(checklist_item.get("checked")))
                checkbox.stateChanged.connect(
                    lambda _state, c=comment, i=index, cb=checkbox: self._on_checklist_item_toggled(c, i, cb)
                )
                self.comment_detail_layout.addWidget(checkbox)

        previews = comment.get("previews") or []
        if previews:
            has_content = True
            self.comment_detail_layout.addWidget(self._section_label("Revisions"))
            for preview in previews:
                self.comment_detail_layout.addWidget(self._build_revision_preview(preview))

        attachments = comment.get("attachment_files") or []
        if attachments:
            has_content = True
            self.comment_detail_layout.addWidget(self._section_label("Attachments"))
            for attachment in attachments:
                self.comment_detail_layout.addWidget(self._build_attachment_preview(attachment))

        self.comment_detail_area.setVisible(has_content)

        # Without this, the scroll area's minimum height stretches the
        # content widget taller than the actual checklist/attachments, and
        # the layout distributes that leftover space between items instead
        # of pushing it below everything — showing up as a gap right under
        # the "Checklist" title.
        self.comment_detail_layout.addStretch(1)

    def _section_label(self, text):
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold;")
        return label

    def _on_checklist_item_toggled(self, comment, index, checkbox):
        checklist = comment.get("checklist") or []
        previous_value = checklist[index].get("checked", False)
        new_value = checkbox.isChecked()
        checklist[index]["checked"] = new_value

        try:
            self.session.update_comment_checklist(comment["id"], checklist)
        except Exception as exc:
            checklist[index]["checked"] = previous_value
            checkbox.blockSignals(True)
            checkbox.setChecked(previous_value)
            checkbox.blockSignals(False)
            QMessageBox.warning(self, "Kitsu", f"Could not sync checklist item to Kitsu: {exc}")

    def _wrapped_label(self, text):
        # Exception messages/filenames can be long — without word-wrap, a
        # single-line QLabel's minimum width would be the whole message,
        # blocking the dock from narrowing whenever one is shown.
        label = QLabel(text)
        label.setWordWrap(True)
        return label

    def _build_attachment_preview(self, attachment):
        file_name = attachment.get("name") or str(attachment.get("id"))
        extension = (attachment.get("extension") or os.path.splitext(file_name)[1].lstrip(".")).lower()

        try:
            local_path = self._ensure_attachment_downloaded(attachment)
        except Exception as exc:
            return self._wrapped_label(f"{file_name}: could not download ({exc})")

        if extension in self._IMAGE_EXTENSIONS:
            pixmap = QPixmap(local_path)
            if not pixmap.isNull():
                label = QLabel()
                label.setPixmap(
                    pixmap.scaledToWidth(320, Qt.SmoothTransformation)
                    if pixmap.width() > 320
                    else pixmap
                )
                label.setToolTip(file_name)
                return label

        return self._wrapped_label(f"{file_name} (no preview available for this file type)")

    def _build_revision_preview(self, preview):
        revision = preview.get("revision", "?")
        file_name = preview.get("original_name") or f"revision_{revision}"

        try:
            local_path = self._ensure_preview_downloaded(preview)
        except Exception as exc:
            return self._wrapped_label(f"Revision {revision}: could not download ({exc})")

        pixmap = QPixmap(local_path)
        if pixmap.isNull():
            return self._wrapped_label(f"Revision {revision} (no preview available for this file type)")

        objects = picture_annotation_objects(preview)
        has_drawover = bool(objects)
        if has_drawover:
            pixmap = render_annotations(pixmap, objects, preview.get("width"), preview.get("height"))

        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        caption = f"Revision {revision}" + (" (with drawover)" if has_drawover else "")
        container_layout.addWidget(QLabel(caption))

        if has_drawover:
            canvas_button = QPushButton("Add to Canvas")
            canvas_button.setToolTip("Add this drawover to the canvas as a layer")
            canvas_button.clicked.connect(
                lambda _checked, p=preview, o=objects: self._on_add_drawover_to_canvas_clicked(p, o)
            )
            container_layout.addWidget(canvas_button)

        image_label = QLabel()
        image_label.setPixmap(
            pixmap.scaledToWidth(320, Qt.SmoothTransformation) if pixmap.width() > 320 else pixmap
        )
        image_label.setToolTip(file_name)
        container_layout.addWidget(image_label)

        return container

    def _on_add_drawover_to_canvas_clicked(self, preview, objects):
        document = Krita.instance().activeDocument()
        if document is None:
            QMessageBox.warning(self, "Kitsu", "No active document to add a layer to.")
            return

        width, height = document.width(), document.height()
        blank = QPixmap(width, height)
        blank.fill(Qt.transparent)
        composited = render_annotations(blank, objects, preview.get("width"), preview.get("height"))
        image = composited.toImage().convertToFormat(QImage.Format_ARGB32)

        revision = preview.get("revision", "?")
        layer_name = f"Feedback_{revision}"

        root = document.rootNode()
        existing = next((node for node in root.childNodes() if node.name() == layer_name), None)
        if existing is not None:
            existing.remove()

        layer = document.createNode(layer_name, "paintlayer")
        root.addChildNode(layer, None)

        pixel_data = image.bits()
        pixel_data.setsize(image.byteCount())
        layer.setPixelData(QByteArray(pixel_data.asstring()), 0, 0, width, height)
        document.refreshProjection()

        self._set_status(f"Added '{layer_name}' layer with the drawover.")

    def _ensure_preview_downloaded(self, preview):
        preview_id = preview.get("id")
        if preview_id in self._preview_cache:
            cached_path = self._preview_cache[preview_id]
            if os.path.exists(cached_path):
                return cached_path

        cache_dir = os.path.join(tempfile.gettempdir(), "kitsu_previews")
        os.makedirs(cache_dir, exist_ok=True)

        name = preview.get("original_name")
        if not (name and "." in name):
            name = f"{preview_id}.{preview.get('extension') or 'png'}"
        target_path = os.path.join(cache_dir, f"{preview_id}_{name}")

        self.session.download_preview(preview, target_path)
        self._preview_cache[preview_id] = target_path
        return target_path

    def _ensure_attachment_downloaded(self, attachment):
        attachment_id = attachment.get("id")
        if attachment_id in self._attachment_cache:
            cached_path = self._attachment_cache[attachment_id]
            if os.path.exists(cached_path):
                return cached_path

        cache_dir = os.path.join(tempfile.gettempdir(), "kitsu_attachments")
        os.makedirs(cache_dir, exist_ok=True)

        file_name = attachment.get("name") or str(attachment_id)
        target_path = os.path.join(cache_dir, f"{attachment_id}_{file_name}")

        self.session.download_attachment(attachment, target_path)
        self._attachment_cache[attachment_id] = target_path
        return target_path

    def _on_download_attachment_clicked(self):
        item = self.comments_list.currentItem()
        if item is None:
            QMessageBox.information(self, "Kitsu", "Select a comment first.")
            return

        comment = item.data(Qt.UserRole)
        attachments = comment.get("attachment_files") or []
        if not attachments:
            QMessageBox.information(self, "Kitsu", "This comment has no attachments.")
            return

        saved_paths = []
        for attachment in attachments:
            file_name = attachment.get("name") or str(attachment.get("id"))
            try:
                saved_paths.append(self._ensure_attachment_downloaded(attachment))
            except Exception as exc:
                QMessageBox.warning(self, "Kitsu", f"Could not download {file_name}: {exc}")

        if saved_paths:
            QMessageBox.information(self, "Kitsu", "Saved to:\n" + "\n".join(saved_paths))

    def _on_add_publish_image_clicked(self):
        document = Krita.instance().activeDocument()
        if document is None:
            QMessageBox.warning(self, "Kitsu", "No active document to export.")
            return

        document_path = document.fileName()
        base_name = (
            os.path.splitext(os.path.basename(document_path))[0]
            if document_path
            else "kitsu_publish_variation"
        )
        index = len(self._pending_publish_images) + 1
        export_path = os.path.join(tempfile.gettempdir(), f"{base_name}_variation{index}.png")
        document.exportImage(export_path, InfoObject())

        self._pending_publish_images.append(export_path)
        self.publish_images_list.addItem(os.path.basename(export_path))
        self._set_status(
            f"Added image {index} to this publish ({len(self._pending_publish_images)} queued)."
        )

    def _on_clear_publish_images_clicked(self):
        self._pending_publish_images = []
        self.publish_images_list.clear()

    def _on_publish_clicked(self):
        if not (self.session and self.task):
            QMessageBox.warning(self, "Kitsu", "Detect a task before publishing.")
            return

        status_data = self.status_combo.currentData()
        if status_data is None:
            QMessageBox.warning(self, "Kitsu", "No task status selected.")
            return

        comment_text = self.comment_edit.toPlainText().strip()

        if not self._pending_publish_images:
            # Nothing was explicitly queued to show — post the status
            # change/comment alone, with no preview. Never fall back to
            # silently exporting the canvas: what gets shown on a task is
            # the artist's call, made explicitly via "Add Image", not an
            # automatic side effect of a status change like switching to
            # WIP just to signal starting work.
            try:
                self.session.add_comment(self.task, status_data, comment=comment_text)
            except Exception as exc:
                QMessageBox.warning(self, "Kitsu", f"Publish failed: {exc}")
                return

            self.comment_edit.clear()
            self._set_status("Published (status/comment only, no preview).")
            self._load_comments()
            return

        document = Krita.instance().activeDocument()
        document_path = document.fileName() if document else None
        revision = parse_version_number(document_path)
        main_path, *extra_paths = self._pending_publish_images

        try:
            new_comment, preview_file = self.session.publish_preview(
                self.task,
                status_data,
                comment=comment_text,
                preview_file_path=main_path,
                revision=revision,
            )
            for extra_path in extra_paths:
                self.session.add_extra_preview(self.task, new_comment, preview_file, extra_path)
        except Exception as exc:
            QMessageBox.warning(self, "Kitsu", f"Publish failed: {exc}")
            return

        self.comment_edit.clear()
        self._pending_publish_images = []
        self.publish_images_list.clear()
        status_message = "Published."
        if revision is not None:
            status_message = f"Published as revision {revision}."
        self._set_status(status_message)
        self._load_comments()
