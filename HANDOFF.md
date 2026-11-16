# Handoff: Substance Version Tool

Status as of 2026-11-16. Public on GitHub (`ShadaxTheWizard/substance-version-tool`), MIT-licensed;
the public history starts at 0.2.0. Release v0.2.0 is out (Windows installer + portable zip,
Linux AppImage), built and self-tested by GitHub Actions on the first try. Not yet done: a run of the app's real window on a desktop (only the off-screen
self-test has run), and any test on a real Windows machine.

## The app (Substance Version Tool)

`substance_version_tool.py` is a PySide6 window around `spp_convert.convert()`: a project list
(drag and drop, shows the saving release), a target selector, output next to the project or
in a folder, the texture-cache option, and the report. Conversion runs in a `QThread`; the
CLI is unchanged. `--self-test` (with `QT_QPA_PLATFORM=offscreen`) is what CI runs on each
build; it reports on stdout and in `$SPP_SELFTEST_LOG`, and turns failures into exit code 1
(the Windows build has no console, and an uncaught exception there opens a dialog).
Decisions taken: PySide6 (`PySide6-Essentials` only), PyInstaller `--onedir`, an AppImage
for Linux, an Inno Setup installer plus a portable zip for Windows, GitHub releases.

The icon (`packaging/substance-version-tool.svg`, our own, not Adobe's) is rendered to PNG / ICO
at build time with the bundled Qt (`packaging/icons.py`). Both builds use
python-build-standalone 3.13 fetched by uv, and install from the hashed
`packaging/requirements.txt` (pinned versions, at least two weeks old when chosen).

### Linux

`packaging/build-linux.sh` → `dist/Substance_Version_Tool-<version>-x86_64.AppImage` (about 75 MB).
Built in Debian 12 (`node:22-bookworm`) so bundled system libraries stay old; the glibc
floor is PySide6's 2.34. The AppImage is made without appimagetool (only a moving
"continuous" release): `mksquashfs` of the AppDir appended to the static type2 runtime
(pinned release 20251108, checksum-verified; no libfuse2 needed on the host). CI
smoke-tests it with `APPIMAGE_EXTRACT_AND_RUN=1`, since containers have no FUSE.

* PySide6 6.10+ Linux wheels are `manylinux_2_34`; under Debian's own Python 3.11 with only
  2.28 allowed, pip silently falls back to PySide6 6.9.
* A frozen app sets `LD_LIBRARY_PATH` (and `_PYI_*`) to its bundle for child processes
  (in `--onedir` too: `<AppDir>/usr/lib/substance-version-tool/_internal`). `child_env()` undoes
  that for `Painter --version` and `xdg-open`; without it Painter or the file manager would
  load the app's Qt.
* Painter prints `--version` to **stderr**.
* Widgets added to a layout that is not yet attached to a widget stay "owned by Python" in
  PySide6 while Qt also owns them, which crashes at exit (SIGBUS in `destroyQCoreApplication`).
  Attach each row layout before filling it.
* The build image has no GTK, so the gtk3 platform-theme plugin's dependencies are not
  bundled (the host's GTK is used when present). The xcb libraries are installed so they
  get bundled; Ubuntu does not ship `libxcb-cursor0` by default.

### Windows

`packaging/build-windows.sh` (Git Bash) → `Substance_Version_Tool-<version>-windows-x64-setup.exe`
(Inno Setup, `packaging/windows/substance-version-tool.iss`) and `…-windows-x64-portable.zip`.
`--windowed` (no console), with an `.ico` and a version resource. The installer is per-user
by default (no admin), and optionally adds "Convert with Substance Version Tool" to the `.spp` context
menu through `SystemFileAssociations`, so Painter keeps the double-click. Never change the
installer's `AppId`. **Never built or run on a real Windows machine yet**; to check there:

* converting, and opening the result in Painter
* non-ASCII file names, OneDrive and network folders, long paths
* a project that Painter has open (reading it; replacing an existing converted copy that
  Painter has open fails on Windows)
* `painter_executables()` knows only Linux locations, so on Windows the target is picked
  by hand. Detecting Painter there should read the `.exe` version resource or the uninstall
  registry entry rather than run it (it is a windowed program). Locations: Creative Cloud
  (`C:\Program Files\Adobe\Adobe Substance 3D Painter\`) and Steam.

**Signing.** The builds are unsigned, so SmartScreen warns ("More info → Run anyway").
Plan: apply to the SignPath Foundation (free code signing for open-source projects; the
certificate is issued to SignPath Foundation, which then shows as the publisher). Their
conditions: OSI license (MIT ✓), public repository ✓, MFA for all team members on
SignPath and GitHub, binaries built by CI from the public source, and a code-signing policy
page in the project. After approval, add their signing step to the `windows` job (sign the
`.exe` files before Inno Setup packs them, then the installer).

### CI and releases

`.github/workflows/build.yml`: jobs `linux` (container `node:22-bookworm`), `windows`
(`windows-2025`; installs Inno Setup with choco if the image lacks it) and, for tags,
`release`. Every push and pull request builds both and keeps them as run artifacts. A tag
`vX.Y.Z` (must equal `__version__`) also creates the GitHub release with all files and
their `.sha256`, notes from `packaging/release-notes.md`. Only `GITHUB_TOKEN` is used; no
secrets are stored. Actions are pinned by commit.

### Next

* macOS: GitHub's macOS runners are free for public repos. Needs a `build-macos.sh`
  (`.app` in a `.dmg`), the requirements already include `macholib`. Unsigned macOS apps
  are blocked by Gatekeeper unless the user right-clicks → Open; Apple's notarization needs
  a paid developer account.
* "Open with" for the Linux AppImage (the `install.sh` launcher does that for the CLI).

## Purpose

Makes a Substance 3D Painter project open in an older Painter release, any of 9.0 – 12.2.
The everyday case is still grading: Painter **12.0.1** (Linux) has to open projects that
students saved with 12.1 / 12.2. It also gives Painter an app-launcher entry and makes
`.spp` files open on double-click.

It is a Linux command-line tool plus launcher, and the Substance Version Tool app (section above);
the smaller open items are under "Other open items".

## Current state

| item | state |
|---|---|
| Converter (`spp_convert.py`) | targets 9.0 – 12.2. `--to VERSION`, default 12.0.1 (`SPP_TARGET_VERSION`) |
| 12.2 → 12.0 | **confirmed in Painter 12.0.1**: two real 12.2 student projects opened and were autosaved by it. Baking and viewer settings are now converted too (they were silently lost before) |
| → 11.0 / 10.1 | layouts match real 11.0 / 10.1 projects exactly; outputs open in Painter 12.0.1 as "version 82 / 81" projects. **Never opened in an actual 11.x / 10.1** |
| → 9.0 / 9.1 / 10.0 | written as data version 77, an estimate (see below). Outputs open in Painter 12.0.1. **Never opened in an actual 9.x / 10.0** |
| Install (`install.sh`) | run on the dev machine; `substance-version-tool` (+ `spp-convert`, `spp-downgrade` aliases), launcher, menu entry, MIME all active |
| `.spp` association | resolves correctly via GIO and KDE (Dolphin). `xdg-mime query filetype` still says `application/x-hdf5`, because on niri it falls back to libmagic. That is harmless. |

### Release ↔ data version

| release | document | baking.ini | viewersettings2.ini | iraysettings2.ini | posteffects.ini | source |
|---|---|---|---|---|---|---|
| 12.2.0 | 92 | 26 | 31 | 2 | 9 | student projects |
| 12.1 | 90 or 91 | ? | ? | ? | ? | unknown; written as 12.0 |
| 12.0.x | 89 | 23 | 29 | 2 | 9 | samples shipped with 12.0.1 |
| 11.1 | 83 – 88 | ? | ? | ? | ? | unknown; written as 11.0 |
| 11.0.0 | 82 | 21 | 27 | 1 | 7 | `automated_tests/resources/*.spp` in the 12.0.1 install |
| 10.1.0 | 81 | 20 | 26 (`viewersettings.ini`) | 1 (`iraysettings.ini`) | ? | `automated_tests/resources/future_binary_data.spp` |
| 10.0, 9.1, 9.0 | 77 – 81 | ? | ? | ? | ? | unknown; written as 77 |

`substance-version-tool --check FILE` prints the release and document data version of any project.
One project saved by a release marked unknown is enough to fill in its row
(`PROFILES` in `spp_convert.py`).

Why 77 for 9.x / 10.0: the field timeline below has `strokes3D` (3D path strokes) appear
in version 77, and paths are the headline feature of Painter 9.0, so 9.0 should be ≥ 77.
If that guess is wrong, Painter says "created with a newer version" and nothing is harmed;
`--data-version 73` then writes the oldest layout available.

## How the layouts were obtained

Painter's loader is **strict**. After its own upgrade steps, every class must have exactly
the fields the runtime expects. Found by experiment in 12.0.1:

* an unknown extra field → `[TRANSLATION] Members count mismatch in the source struct dict`
  or `A value has been defined for a NON-EXISTING member`
* a missing field → `[TRANSLATION] No value defined for '….field' member`
* the only field that may be absent is `uid`

So "drop what the old version does not know" requires knowing exactly what each version
knows. Painter 12.0.1 contains the upgrade chain from every older version, which makes it
an oracle: **a document stamped N with field F removed loads if and only if an upgrade
step after N adds F**. `dev/probe_fields.py` runs that test for every field (group
testing, about 900 project opens, 40 minutes) through `dev/oracle.py`, which drives a real
Painter with the plugin in `dev/oracle_plugin/`. The result is `dev/added_fields_12.0.1.json`,
and `dev/gen_schemas.py` turns it plus real projects into `schemas.py`.

Result (one feature per version):

| data version | fields added |
|---|---|
| 75 | `DataDocument.usePhysicalSizeAsDefault` |
| 76 | `Data2DTransformation.physicalSizeBoundedByScaleFactors` |
| 77 | `strokes3D` on paint / paint-proj / eraser / smudge / clone |
| 78 | `DataStroke.label` |
| 79 | `DataActionFill.uvTransferChannel` |
| 80 | `explicitPersistentCachePin` on layers and groups |
| 81 | `changeFlags` on the stroke actions; `DataSourceText.letterSpacing`, `lineSpacing` |
| 82 | `DataResolutionOverride` and the `resolutionOverride` fields using it |
| 83 | `DataActionFill.symmetry` (`DataSymmetry`) |
| 84 | `DataMaterialStack.type`, `typeMetadata` |
| 85 | `DataActionFill.changeFlags` |
| 89 | `DataGridDeformation.stickyBackupVertices`, `stickyState` |

Checks on this: the layouts derived for 81 and 82 equal the layouts in the real 10.1 and
11.0 projects (12 and 28 classes, no mismatch).

Limits of the method, i.e. where an old Painter could still refuse a converted file:

* It finds fields that were **added**. A field that was renamed, removed or retyped
  between 73 and 89 would not show up. None is visible in the real 81 / 82 projects, and a
  12.0-layout document stamped anywhere from 73 to 88 loads, so there is probably none.
* Below 73 something else changes (`Unknown struct type ''` at 70 – 72, a crash at ≤ 69,
  `Struct with non primitive member 'workflowLegacy'` at 60). 73 is the floor.
* Classes are assumed to exist in every version. A project using a layer or effect type
  newer than the target cannot be converted; the tool does not detect that.
* Classes with no 12.0 sample (`DataSourceVectorial`, `DataEmitterPhysics`: layouts from
  the 11.0 test projects; `DataActionClone`: copy of smudge) could not be probed.

## Format notes (reverse-engineered)

* An `.spp` file is HDF5 (Painter 12.0.1 links HDF5 1.14.4 statically, so output is
  written with `libver=('earliest','v114')`). Every dataset is a 1-D uint8 blob. Blobs
  under 64 KiB are contiguous; larger ones are chunked at 65536 with `maxshape=(None,)`.
* Dataset attribute `m3_x64_128` = MurmurHash3_x64_128(blob, **seed 0xF13A0239**),
  stored as two `>u8` (low, high).
* `paint/document.bin`, `paint/default_material.bin` and the settings blobs under
  `editor/` and `baking/` use Painter's "Data" serializer, which is little-endian and
  self-describing:
  `magic dd2f7c1b | u32 1 | u32 dataVersion | u32 18 | u32 classCount | root object`.
  An object is `u32 absoluteEndOffset (0 = null) | classref | fields`. A classref is
  `0xffffffff` followed by an inline definition (name and (field, typeId) list), or an
  index of an earlier definition. Type ids are listed in `sppformat.py`. Each blob kind
  has its own version counter (table above).
* Painter rejects a project when the document `dataVersion` is newer than its own (log
  message: "created with a newer version of Painter"). A too-new settings blob is only
  logged ("Data version (26) is more recent than the runtime version (23)") and replaced
  by defaults.
* **A settings blob with the right version but wrong content can crash Painter**: a
  post-effects blob "downgraded" 9 → 7 by filling the old, no longer existing sub-objects
  with nulls crashed 12.0.1 on open. The converter therefore refuses to convert a settings
  blob as soon as it would have to invent an object, and leaves it as saved.
* Changes between 89 and 92 that the converter handles:
  * `channelTypes`: type 22 (u128 mask) becomes type 12 (u64); take the low 8 bytes
  * `DataChannel.type` and `DataProceduralInput.channelType`: enum index (type 9,
    -1 = none) becomes a one-hot u64 (type 21, 0 = none). The channel numbering is
    unchanged. Same for `SettingsSolo.channelType` in the viewer settings (type 12).
  * `DataChannel.userIsColor` maps to `userIsColorManaged`
  * `sourceTransparent` on paint, eraser, smudge and clone: a single object (18)
    becomes a list (19)
  * polygon fill (`DataActionDecal` with hits): 12.2 keeps a source list plus blend and
    opacity on the action, and P and V on each hit. 12.0 keeps a `color` and
    `projViewInvMatrix = inverse(P·V)` on each hit (column-major). UV-view hits
    (`viewType 0`) have all-zero matrices in both versions.
  * 12.2-only fields are dropped; only values that differ from 12.2's defaults are
    reported (`KNOWN_DEFAULTS`, `schemas.DOC_USUAL`)
* `projectsettings.ini` is a big-endian QDataStream QVariantMap (values can be lists and
  maps, e.g. `baking/collapsed_materials`). The converter sets `versionAtLastSave/*` (and
  `versionAtCreation/*` if newer) to the target. The embedded auto-UV `anorigami` JSON is
  version 8 in 12.2, 7 in 12.0, 6 in 11.0 and `{}` in the 10.1 sample; key sets are in
  `ANORIGAMI_KEYS`.
* `texture-cache` holds the final per-channel composites:
  * header: QVariantMap {version 3, version.minor 2 → 3 in 12.2}, followed by
    (path, 8-byte tag) entries. 10.1, 11.0 and 12.0 all use 3.2.
  * channel dataset names: `1<<n` up to 12.0, `n` in 12.2
  * `list` entries: 12 bytes followed by a u64 BE mask (≤ 12.0) or a u32 BE index (12.2)
  * for 9.x / 10.0 targets the cache is dropped (its format there is unknown)
* 10.1 names three datasets without the "2": `editor/viewersettings.ini`,
  `iraysettings.ini`, `layersstackstate.bin`. The converter does not write those; 10.1 and
  older start with default viewer settings.
* Layouts for `DataActionSmudge`, `DataSourceText` and `DataTimelineKey` first came from
  older bundled presets; the converted student projects (which use them) load in 12.0.1.
* The smart materials / masks shipped with Painter (`.spsm`, `.spmsk`) carry three copies
  of their content, `preset.bin`, `preset_1.bin` (version 53) and `preset_2.bin` (54), in a
  numbering of their own. Not used by this tool.

## Desktop fixes (added 2026-11-15)

The launcher also sets `QT_SCALE_FACTOR`, preloads the eyedropper shim and adds
`painter/` to `SUBSTANCE_PAINTER_PLUGINS_PATH`. Facts found while building them:

* Painter ships only the xcb Qt platform plugin, so it always runs under XWayland. With
  xwayland-satellite the X screen is the output's physical size at 96 dpi.
* Python plugins in a `plugins/` folder are off until enabled in Painter's Python menu;
  modules in a `startup/` folder always load. That is why the plugin lives in `startup/`.
* `Camera.rotation` is XYZ Euler angles in degrees with `R = Rx * Ry * Rz`; the camera
  looks down local -Z. Measured by setting rotations, saving, and reading the quaternion
  in the project's `editor/camera.ini` (same Data serializer as `document.bin`).
* A Python-implemented `QObject` parented to Painter's main window crashes Painter on exit
  (it is destroyed after Python shuts down). Keep such objects unparented and delete them
  in `close_plugin`.
* Moving the camera through the API marks the project as modified.
* Tested by running Painter with a one-shot startup module that drives the plugin with
  synthetic key events and checks the camera, and that calls `QScreen.grabWindow(0)`.
  **Not tested with physical key presses or with the eyedropper tool itself.**

## Other open items

1. **Linux launcher: detect the installed Painter.** `install.sh` bakes `PAINTER_VERSION`
   (default 12.0.1) into the launcher. It could parse `--version` (see the top section)
   and pass the result as `--to` instead.
2. Get one project saved by each of 9.0, 9.1, 10.0, 11.1 and 12.1, run `--check` on it and
   add the release to `PROFILES`. For 9.x / 10.0 that also settles the 77 estimate.
3. Open a converted project in a real 11.x / 10.x / 9.x if one is ever at hand.
4. One converted student project (→ 9.0) showed 2 of 414 fill effects under a different
   display name in Painter 12.0.1 ('Card Diamonds' instead of 'card_diamonds'). Cause not
   found; nothing else differed.
5. A project newer than data version 92 is refused unless `--force` is given. To support a
   newer Painter: add its changes as rules in `DataConverter` (compare its layouts with
   `schemas.DOC_89`), and if the installed Painter is newer too, re-run the probe so
   `DOC_ADDED` covers the new base.

## Dev notes

* Reference files: the 12.0.1 install's `resources/samples/*.spp` and
  `resources/starter_assets/templates/*.spt` are data version 89;
  `resources/python/modules/automated_tests/resources/` has 81 and 82 projects.
* Regenerating `schemas.py` (the first command drives Painter for about 40 minutes; its
  window opens and closes projects by itself):

  ```sh
  PY=~/.local/share/spp-downgrade/venv/bin/python
  $PY dev/probe_fields.py --out dev/added_fields_12.0.1.json A.spp B.spp ...   # version-89 projects
  $PY dev/gen_schemas.py dev/added_fields_12.0.1.json "$PAINTER_DIR/resources" more.spp ...
  ```

* Do not put a pattern that matches your own shell's command line into `pkill -f` when
  scripting around the oracle; it kills the calling shell (exit code 144).
* Remote: GitHub `ShadaxTheWizard/substance-version-tool`, branch `main`.
