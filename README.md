# Substance Version Tool

Makes a Substance 3D Painter project open in an **older** Painter release — any release
from **9.0 to 12.2**. Support for Substance 3D Designer is planned.

Painter refuses a project saved by a newer release, and it checks the saved data strictly:
one setting it does not know and the project fails to load. This tool writes a converted
copy in the exact format of the release you name, `<name>_<version>.spp`, next to the
original. The original is never modified.

Going the other way needs no tool: a newer Painter opens an older project by itself.

It comes two ways: an app with a window for Windows and Linux, and a Linux command-line
tool (`substance-version-tool`) with a Painter launcher that converts on open.

> Substance 3D Painter is a product of Adobe. This is an independent tool, not affiliated
> with or endorsed by Adobe. It works from the project files alone and contains no Adobe code.

## The app

Download it from the [Releases](https://github.com/ShadaxTheWizard/substance-version-tool/releases) page.

**Windows:** run `Substance_Version_Tool-<version>-windows-x64-setup.exe` (installs for your user, no
admin rights needed, and can add *Convert with Substance Version Tool* to the right-click menu of
`.spp` files), or unzip the portable `.zip` anywhere and run `Substance Version Tool.exe`.
Until code signing is in place (see [Code signing policy](#code-signing-policy)), SmartScreen
may say "Windows protected your PC": choose *More info* → *Run anyway*.

**Linux:** make `Substance_Version_Tool-<version>-x86_64.AppImage` executable and run it:

```sh
chmod +x Substance_Version_Tool-*-x86_64.AppImage
./Substance_Version_Tool-*-x86_64.AppImage            # or pass .spp files to load them right away
```

It is one self-contained file (no Python needed) and runs on any 64-bit Linux with glibc
2.34 or newer (Debian 12, Ubuntu 22.04, RHEL 9, Fedora 35 and later). Without FUSE, add
`--appimage-extract-and-run`. AppImage managers (Gear Lever, AppImageLauncher) can add it
to the app launcher.

1. Drop `.spp` projects (or folders of them) on the window, or use *Add projects…*. Each
   one shows the Painter release that saved it.
2. Pick the Painter release they have to open in. On Linux the installed Painter is found
   and preselected; on Windows pick it by hand for now.
3. Press *Convert*. The copies are written next to each project, or into a folder you
   choose, as `<name>_<version>.spp`; the report lists what the older release cannot store.

**Opening in Painter (Linux).** `--open FILE.spp` starts the installed Painter with the
project. A project saved by a newer release than that Painter is converted first and Painter
opens the copy, `<name>_<version>.spp` next to the original; the copy is reused until the
original changes. The AppImage's `.desktop` entry runs `--open` for `.spp` files, so with the
tool as the default program for `.spp` a double-click does the same:

```sh
xdg-mime default substance-version-tool.desktop application/x-substance-painter-project
```

(The `.spp` MIME type has to exist; `install.sh` defines it.) No window appears unless the
conversion takes a moment. Without an installed Painter, or when the conversion fails, the
main window opens with the project listed. On Windows Painter is not detected yet, so
`--open` always shows the window.

**Updates.** The Windows installer offers to let the app check GitHub for a newer version
each time it starts; the portable and Linux versions ask on their first start (*Help → Check
for Updates at Start* changes that; *Help → Check for Updates…* checks once; see the
[privacy policy](PRIVACY.md)). When there is one, a bar offers to update: the installed Windows app
and the AppImage download it, check it, replace themselves and restart; the portable zip
and a checkout open the release page instead.

Without the download, from a checkout: `pip install h5py numpy mmh3 PySide6-Essentials`,
then `python3 substance_version_tool.py`.

## Install

Requires Linux, Python 3 and Painter (the launcher part assumes 12.0.x).

```sh
git clone https://github.com/ShadaxTheWizard/substance-version-tool.git
cd substance-version-tool
./install.sh        # or: PAINTER_DIR="/path/to/Substance 3D Painter" ./install.sh
```

This creates the `substance-version-tool` command (also available as `spp-convert` and
`spp-downgrade`, its earlier names), a `substance-painter` launcher, an app-launcher
entry with the Painter icon, and makes `.spp` files open in Painter on double-click.

## Everyday use

* **Double-click any `.spp`** (or open it from the app launcher's *Substance 3D Painter*).
  Files newer than the installed Painter are converted automatically, and Painter opens
  the copy. A converted copy is reused until the original changes.

  The launcher converts for Painter **12.0.1**. After installing a different Painter,
  re-run `PAINTER_VERSION=12.1 ./install.sh` (with `PAINTER_DIR=...` if it moved).
* Command line:

  ```sh
  substance-version-tool student.spp                # -> student_12.0.1.spp (the default target)
  substance-version-tool --to 11.0 student.spp      # -> student_11.0.spp
  substance-version-tool --to 9 *.spp               # several at once, for Painter 9.0
  substance-version-tool --check *.spp              # which Painter saved each file; does it need converting?
  substance-version-tool --drop-cache student.spp   # if a converted file shows stale textures
  ```

  The default target is 12.0.1; set `SPP_TARGET_VERSION` to change it.

The launcher's last conversion log is at `~/.cache/spp-downgrade/launch.log`.

## Supported releases

| target | written as | how sure |
|---|---|---|
| 12.2 | — | newest known format; nothing to convert |
| 12.1 | 12.1 format (data version 91) | format read from 12.1.0 – 12.1.5 projects; not tested in Painter 12.1 itself |
| 12.0 | 12.0 format (data version 89) | format read from 12.0 projects; **converted files tested in Painter 12.0.1** |
| 11.1 | 11.1 format (data version 88) | format read from an 11.1 project; not tested in Painter 11.1 itself |
| 11.0 | 11.0 format (data version 82) | format read from 11.0 projects; not tested in Painter 11.0 itself |
| 10.1 | 10.x format (data version 81) | format read from a 10.1 project; not tested in Painter 10.1 itself |
| 10.0 | 10.x format (data version 81) | format from real 10.0 projects (via Universal SPP, see below); not tested in Painter 10.0 itself |
| 9.1 | 9.1 format (data version 80) | format read from a 9.1 project; not tested in Painter 9.1 itself |
| 9.0 | 9.0 format (data version 78) | format from real 9.0 projects (via Universal SPP); not tested in Painter 9.0 itself |

Painter 10.x and older store project data in an older binary encoding than 11.0 and later,
with each object's field names written out in full. The tool writes that encoding for
those targets. It reproduces a real 9.1 project byte for byte, and a converted 9.1 copy
has exactly the datasets and layouts of a real one.

Only Painter 12.0.1 was available for testing. For 12.1, 11.1 and 9.1 the layouts come from
projects saved by those releases. For 9.0 and 10.0 they come from the field lists of
[Universal SPP](https://github.com/Yeusepe/Universal-Painter-Files), a separate MIT-licensed
converter whose profiles were made from real 9.0 and 10.0 projects. The rest come from
Painter 12.0.1's own upgrade code (see `HANDOFF.md`), and Painter 12.0.1 accepts every
converted file as a genuine project of that age. No actual 9.x / 10.x / 11.x / 12.1 install
has opened one.

## What changes in the copy

Everything the target release can represent is carried over: layers, masks, fills,
generators, filters, paint/erase/smudge/clone strokes, polygon fills, text, resources and
bakes. Settings the target has no place for are dropped, and the tool lists the ones the
project actually used. The older the target, the longer that list can get:

| not available before | what is dropped |
|---|---|
| 12.2 | polygon-fill blending / opacity / rectangle paint-through (fills become plain colour) |
| 12.1 | texture-set shader model, channel description, background colour and colour-profile overrides |
| 12.0 | warp-projection sticky points |
| 11.1 | fill-layer symmetry, layer-stack type |
| 11.0 | resolution override of procedural / text / vector sources |
| 10.0 | text letter and line spacing |
| 9.1 | layer cache pins, the fill layers' UV-transfer channel |

Viewer, shader and baking settings are converted for every target; entries a target has
no place for (newer bakers, tweaks, bake templates) are left out. Post-effects settings
cannot be rebuilt in the layout before 12.0, so for 9.x – 11.x Painter uses its default
post effects (unless the project comes from 11.1). For 11.x and newer, settings that
cannot be converted are left as saved and Painter falls back to its defaults for them;
for 10.x and older they are left out, because those releases cannot decode the newer
encoding at all.

Two gaps for 9.x / 10.x: 3D path strokes (paths) are kept in today's layout, which those
releases may reject (the tool warns when a project has any), and resources such as export
presets stay in the newer format (also with a warning).

A layer type that did not exist yet in the target release cannot be converted; Painter
will refuse such a project.

## Desktop fixes (Wayland / niri)

Painter is an X11 program, so on Wayland it runs through XWayland. The `substance-painter`
launcher works around three problems with that. They only apply when Painter is started
through the launcher (app-launcher entry, double-click, or the command).

* **Readable UI.** XWayland ignores the output scale, so Painter's interface comes out
  tiny. The launcher scales it by niri's output scale. To choose a size yourself, set
  `PAINTER_UI_SCALE=1.5` (any number) in `~/.config/substance-painter.conf`.
* **Eyedropper.** Picking a colour from the screen used to show a black screen, because
  Painter copies the (empty) X11 root window. A small preloaded library swaps that copy
  for a real screenshot. Without `grim` installed the screenshot comes from niri and takes
  about a second; `sudo dnf install grim` makes it quicker. Needs `g++` and
  `qt6-qtbase-devel` at install time; set `PAINTER_EYEDROPPER_FIX=0` to turn it off.
* **Keyboard navigation.** Press **`** (backtick), or use *Viewport > Keyboard Navigation*
  or the arrows button in the plugin toolbar, to toggle keyboard camera controls for the 3D view:

  | keys | action |
  |---|---|
  | arrows or W A S D | orbit |
  | Shift + arrows or W A S D | pan |
  | `+` / `-`, E / Q, PgDn / PgUp | zoom in / out |
  | Home | frame the whole model |
  | Ctrl + left / right or A / D | rotate the light (environment) |

  While it is on, those keys do not reach Painter's own shortcuts and the shortcut-hint
  popup that appears when holding Ctrl or Shift is hidden; typing in text fields still works. The toggle state is remembered. Speeds, the toggle key and the orbit
  direction are constants at the top of `painter/startup/keyboard_nav.py`. The 2D view is
  not affected (Painter has no scripting access to it).

## Builds and releases

GitHub Actions (`.github/workflows/build.yml`) builds the app for Linux
(`packaging/build-linux.sh`, an AppImage) and Windows (`packaging/build-windows.sh`, an
installer and a portable zip) on every push and pull request, with PyInstaller and the
pinned, hashed dependencies in `packaging/requirements.txt`. Each build is started once
headless as a self-test and kept on the run's page (*Artifacts*).

To publish a release, bump `__version__` in `spp_convert.py`, commit, then tag and push:

```sh
git tag -a vX.Y.Z -m "Substance Version Tool X.Y.Z" && git push origin main vX.Y.Z
```

The tag build creates the GitHub release with every build and its `.sha256` attached, using
the notes in `packaging/release-notes.md`. The build scripts also work by hand and leave
their output in `dist/` (Linux needs `mksquashfs`; Windows needs Git Bash and Inno Setup 6).

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org).

> The application to the SignPath Foundation is pending. Until it is approved, the Windows
> builds are not signed.

* Committers and reviewers: [Shadax](https://github.com/ShadaxTheWizard)
* Approvers: [Shadax](https://github.com/ShadaxTheWizard)

What is signed: the Windows program (`Substance Version Tool.exe`) and the Windows installer
attached to this repository's
[GitHub releases](https://github.com/ShadaxTheWizard/substance-version-tool/releases). They
are built by GitHub Actions from this repository's source (`.github/workflows/build.yml`),
never on a developer's machine, and every signing request is approved by hand. The
open-source libraries bundled with the app (Python, Qt / PySide6, HDF5 / h5py, NumPy, mmh3)
are shipped as their projects publish them and are not signed by this project.

Privacy: this program will not transfer any information to other networked systems unless
specifically requested by the user or the person installing or operating it. The one such
transfer, the optional update check against github.com, is described in the
[privacy policy](PRIVACY.md).

## Uninstalling

* Windows, installed: *Settings → Apps → Installed apps → Substance Version Tool →
  Uninstall*. This also removes the app's settings and the right-click menu entry.
* Windows, portable: delete the folder (settings: registry key
  `HKEY_CURRENT_USER\Software\substance-version-tool`).
* Linux: delete the AppImage (settings: `~/.config/substance-version-tool/`).

## Files

| file | purpose |
|---|---|
| `spp_convert.py` | converter + CLI (`convert(src, dst, Target("11.0"))` is the entry point for other front ends) |
| `PRIVACY.md` | privacy policy (the optional update check is the only network access) |
| `updater.py` | update check, download and install (standard library only) |
| `substance_version_tool.py` | the Substance Version Tool app (PySide6) |
| `packaging/` | app builds (`build-linux.sh`, `build-windows.sh`, `windows/substance-version-tool.iss`), pinned build requirements, icon, `.desktop` file, release notes, SignPath artifact configurations (`signpath/`) |
| `.github/workflows/build.yml` | CI: build, self-test and release the app |
| `sppformat.py` | readers/writers for Painter's binary formats |
| `schemas.py` | class layouts per data version (generated; see `dev/`) |
| `install.sh` | (re)installs the CLI, launcher, menu entry and `.spp` association |
| `painter/startup/keyboard_nav.py` | keyboard-navigation plugin, loaded by the launcher |
| `painter/screengrab_shim.cpp`, `painter/painter-screengrab` | eyedropper fix (preloaded library and its screenshot helper) |
| `dev/` | the scripts that produced `schemas.py` by probing a real Painter |
| `HANDOFF.md` | format notes, verification status and open items for whoever picks this up next |

## License

MIT; see `LICENSE`.
