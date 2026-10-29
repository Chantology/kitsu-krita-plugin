# Kitsu Krita Plugin

A Krita 5.2.x Python plugin that connects to a self-hosted Kitsu/Zou (CG Wire)
instance: it detects the task a document belongs to from its folder
structure, shows feedback/comments (including overpaint attachments), and
publishes new versions with a comment and status change.

All Kitsu/Zou protocol handling (auth, endpoints, uploads/downloads) is
delegated to CGWire's official [`gazu`](https://github.com/cgwire/gazu)
Python client. Krita's bundled Python doesn't have `gazu` — or, on many
installs, even `requests` — so both are vendored inside
`kitsu_krita_plugin/vendor/` along with `requests`'s own dependencies
(`urllib3`, `certifi`, `idna`). No manual `pip install` into Krita's
Python is required.

## Folder layout

Everything under `pykrita/` is exactly what gets copied into Krita's
plugin folder:

```
pykrita/
  kitsu_krita_plugin.desktop     # plugin descriptor Krita reads
  kitsu_krita_plugin/            # the plugin package
    __init__.py
    kitsu_krita_plugin.py        # registers the dock widget + menu entry with Krita
    extension.py                 # adds the "Kitsu/Zou" menu entry
    docker.py                    # the dock's UI (login, task, feedback, publish)
    collapsible_section.py       # collapsible header used for each dock section
    kitsu_api.py                 # wraps vendored gazu for login/tasks/comments
    task_detection.py            # resolves asset/task from folder structure
    settings.py                  # persists server URL / email / project id / collapsed sections
    manual.html                  # shown inline in the Python Plugin Manager
    vendor/
      gazu/                      # vendored CGWire gazu client
      requests/, urllib3/,       # vendored gazu runtime dependencies
      certifi/, idna/
      typing_extensions.py       # shim (Krita's Python already has typing.Literal)
```

`manual.html` has to live inside the package folder specifically — Krita's
Python Plugin Manager resolves `X-Krita-Manual` as
`<folder containing the .desktop file>/<X-KDE-Library>/<manual filename>`
(confirmed straight from Krita's own source,
`plugins/extensions/pykrita/plugin/PythonPluginManager.cpp`), not
alongside the `.desktop` file itself.

## Installation

### Automatically

Run the installer script for your OS from the repository root — each
copies `pykrita/kitsu_krita_plugin.desktop` and `pykrita/kitsu_krita_plugin/`
into Krita's resource folder for you (removing any previous install of
the plugin's own folder first, so nothing stale from an older version is
left behind):

- **Windows**: double-click `install_windows.cmd` (installs to
  `%APPDATA%\krita\pykrita`).
- **macOS**: double-click `install_mac.command` (installs to
  `~/Library/Application Support/krita/pykrita`). If it won't open ("not
  executable"), run `chmod +x install_mac.command` once in Terminal
  first.
- **Linux**: run `./install_linux.sh` in a terminal (`chmod +x
  install_linux.sh` first if needed). It detects a native, Flatpak, or
  Snap install and picks the matching resource folder.

If your Krita uses a non-standard resource folder (e.g. a portable
install), find it via **Settings → Manage Resources → Open Resource
Folder** and pass its `pykrita` subfolder as an argument to the script
instead, e.g. `install_linux.sh /path/to/your/pykrita`.

### Manually

1. In Krita, go to **Settings → Manage Resources → Open Resource Folder**.
   This opens Krita's resource directory, which contains a `pykrita/`
   subfolder (create it if it doesn't exist).
2. Copy **both** `pykrita/kitsu_krita_plugin.desktop` and the
   `pykrita/kitsu_krita_plugin/` folder from this repo into that
   `pykrita/` resource folder.
3. Restart Krita.
4. Go to **Settings → Configure Krita → Python Plugin Manager**, check
   "Kitsu Krita Plugin" (its description panel should show this plugin's
   manual), and restart Krita once more for the change to take effect.
5. After restarting, the plugin is reachable two ways:
   - A **"Kitsu/Zou"** entry in the main menu bar, next to Help.
   - **Settings → Dockers → Kitsu**.

   Enabling a plugin in the Plugin Manager does not open anything by
   itself — you need to click one of the entries above to see the dock.

### If nothing appears after enabling it

That almost always means the plugin failed to import, and Krita didn't
show it anywhere (no dialog, no menu entry, no docker entry). To see the
actual Python error:

- **Windows**: open a command prompt and run `krita.com` (not
  `krita.exe`) from Krita's install folder — import errors print there.
- **Linux/macOS**: launch `krita` from a terminal.

Since `gazu` and `requests` (plus its own dependencies) are fully
vendored, the dock and menu entry should appear regardless of what's
installed in Krita's Python — importing them is deferred until you
actually click **Login**, so a problem there only fails the login
attempt with a specific message, not the whole plugin.

## Using the plugin

Each section (Connection, Task, Feedback, Publish) has a clickable title
that collapses/expands it — handy for reclaiming vertical space for
other dockers (Layers, brush presets, ...) once you don't need, say, the
Connection or Task section visible anymore. Collapsed/expanded state is
remembered per section across Krita restarts.

1. Open the Kitsu dock and enter your server URL (e.g.
   `https://kitsu.yourstudio.com`), email + password (or an API token),
   and the project ID, then click **Login**.
   - The server URL is normalized to end in `/api` automatically (gazu's
     convention), matching how the official CGWire `gazu` client expects
     the host to be configured.
   - If login fails, the dock shows the *specific* reason (rejected
     credentials, a 404 route, a server error, etc.) instead of a generic
     failure — if you get a 404, it usually means the Zou API for this
     Kitsu instance lives at a different host/path than the web UI, and
     you'll need to ask whoever administers the server for the correct
     API URL.
   - Once logged in, the project's **thumbnail and name** show at the top
     of the **Task** section — not Connection, since it's read-only
     resolved info, not something you enter — so you don't have to keep
     track of which hash belongs to which project. The thumbnail only
     appears if the project actually has one set on Kitsu. Editing the
     Project ID field afterwards clears both until you log in again, so
     they never show info that doesn't match the current ID.
   - Below the Login button, your own avatar and a "Welcome, &lt;name&gt;"
     greeting appear once logged in, confirming which account you're
     actually connected as. Editing the Email or API token field
     afterwards clears both, since a different account may be about to
     log in.
   - After logging in with email/password, the plugin saves the **JWT
     refresh token** Kitsu issued (not your password) and uses it to
     silently restore the session the next time you open Krita — no
     re-entering your password every launch. This token is bounded and
     revocable: by default it expires ~15 days after login (configurable
     by whoever administers your Kitsu instance), and unlike a password
     it can't be used to change the account's password or email. If it's
     expired or invalid, the dock shows a clear message and falls back to
     asking you to log in again — it never gets stuck silently failing.
2. Click **Detect task from current document**. Two folder structures are
   supported — the plugin tries assets first, then shots — matched
   case-insensitively against Kitsu's names:
   - **Assets**: `.../<AssetType>/<Asset>/<TaskType>/file.kra`, e.g.
     `assets/characters/Hero/design/Hero_design_v002.kra`.
   - **Shots**: `.../<Sequence>/<Shot>/<TaskType>/file.kra` for
     movies/shorts/commercials, e.g.
     `sequences/SEQ01/SH010/animation/SH010_anim_v002.kra`. For a TV
     series, an episode level goes in front —
     `.../<Episode>/<Sequence>/<Shot>/<TaskType>/file.kra` — but this is
     only needed if two episodes reuse the same sequence name; if
     sequence names are unique in your project, the plain 3-level shot
     path already resolves correctly without it.
   - If neither matches, the dock shows a **task picker** instead of just
     failing, along with a message naming exactly what was checked (asset
     types, sequences, episodes). The picker also appears when the asset or
     shot was found but its task-type folder matches no task in Kitsu. It
     has cascading **Episode → Sequence → Shot** dropdowns (the episode row
     only appears for projects with episodes) and a **Task Type**
     dropdown; for an asset whose task type didn't match, only the Task
     Type row is shown. **Change Task...** opens the same picker at any
     time, so you can switch to a different shot or task type by hand.
   - Manual picks are remembered for that exact file path. They take
     precedence over folder detection on later Detect Task clicks, and the
     breadcrumb is marked "(set manually)". **Reset to Folder Detection**
     in the picker forgets them.
   - Once a task is detected, its **status** and **due date** show below
     it (if set on Kitsu), along with a **timesheet**: a slider from 0 to
     12 hours (in 0.5h steps) plus 1h/4h/8h preset buttons, pre-filled
     with whatever you've already logged today. Click **Submit Time** to
     set today's total on Kitsu — submitting again with a different value
     replaces today's total rather than adding to it.
   - The slider also **advances on its own** once you're logged in — 1
     real hour after login, it's at 1h; 2 hours later, 2h; and so on, up
     to the 12h cap — so it tracks roughly how long you've been working
     without needing to set it yourself. Dragging it or clicking a preset
     button overrides this at any time; auto-advance then continues
     climbing from *that* value instead of overriding your choice, so you
     can always nudge it and keep working. Switching to a *different* task
     resets it to that task's already-logged time for today and continues
     from there — but clicking **Detect Task** again for the *same* task
     (e.g. just to refresh comments) leaves the running clock untouched,
     it doesn't snap back down. This only updates the on-screen slider —
     it never contacts Kitsu until you click **Submit Time**.
3. Existing comments for that task show up in the **Feedback** section,
   each shown as `Status - Person: comment text` — or, when that comment
   published a new revision (e.g. via this plugin's Publish button),
   `Revision N - Status - Person: comment text`, so a new version stands
   out from a plain comment at a glance.
   The area below the comment list is hidden entirely when the selected
   comment has no checklist, revisions, or attachments — no empty box
   taking up space for a plain-text comment. Otherwise, select a comment
   to see, below the list:
   - Its **checklist**, if it has one, as tickable checkboxes — ticking or
     unticking one syncs that change back to Kitsu immediately. If the
     sync fails (e.g. connection drop), the checkbox reverts and you'll
     get a warning, so the UI never shows a checked state that isn't
     actually saved on the server.
   - Its **revisions**, embedded inline, with any Kitsu **drawover**
     (annotations drawn during a review session — shapes, arrows,
     freehand markup, text notes) composited directly on top of the
     image, labeled "(with drawover)". If a revision has a drawover,
     click **Add drawover to canvas as a layer** to place it directly on
     your current document as a new transparent layer named
     `Feedback_<revision>` (e.g. `Feedback_2`) — scaled to fit your
     document's current resolution, so you can reference or trace over
     it while painting. Clicking it again for the same revision replaces
     that layer rather than stacking duplicates.
   - Its **attachments** (other reference images), embedded inline — no
     separate download step needed for viewing. Click **Save
     attachment(s) to disk** if you want a local copy (e.g. to open in
     Krita and paint over it).
4. To publish, pick a status, write a comment, and click **Publish** — the
   plugin exports the current canvas to a temporary PNG and publishes it
   as a proper Kitsu **preview/revision** (not just a plain comment
   attachment), so it shows up in the task's version history and can be
   drawn over in review sessions. If the Krita filename ends in a
   `_v###`-style version (e.g. `Hero_design_v002.kra`), that number is
   used as the revision number on Kitsu too, so your local version and
   the Kitsu revision always match up.
   - To publish **multiple images for the same revision** (e.g. two
     variations with different layers toggled on/off), toggle your
     layers and click **Add Image** for each variation before clicking
     Publish — each queued image is listed above the Publish button.
     When you publish, the current canvas becomes the main image and the
     queued ones are attached as extra previews on that *same* revision
     (not as plain, unversioned attachments), so all of them show up
     together under that one revision on Kitsu and each supports its own
     drawover. **Clear** empties the queue if you change your mind; the
     queue also clears automatically after a successful publish.

### Drawover rendering limitations

Drawover markup is Kitsu's own Fabric.js canvas data (rectangles,
circles, arrows, freehand strokes, text notes), re-drawn locally in
[annotations.py](pykrita/kitsu_krita_plugin/annotations.py) with
`QPainter` — this covers everything Kitsu's own drawover toolbar
produces. Not (yet) supported: the eraser tool's mask, nested groups,
and rotation on freehand strokes/lines/arrows specifically (rotation on
rectangles/circles/text works). A shape that can't be drawn is skipped
rather than breaking the rest of the drawover.

## Notes on security

The account password is never written to disk. What *is* persisted (via
`QSettings`) across Krita restarts: the server URL, email, project ID,
an optional API token, and — after a successful email/password login —
the JWT refresh token Kitsu issues. That refresh token is what lets the
plugin restore your session on the next launch without asking for the
password again; it's a bounded, revocable bearer credential (expires
~15 days after login by default, can't change your password/email), not
the password itself. If you'd rather not have even that persisted,
enter an API token instead each time and leave it blank between
sessions.
