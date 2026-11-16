#!/usr/bin/env python3
"""Substance Version Tool: a window around spp_convert for people who do not use a terminal.

Drop .spp projects on the window (or pass them on the command line), pick the Painter
release they have to open in, and press Convert.  Each project is written as a new file,
<name>_<version>.spp, next to the original or into a chosen folder; the original is never
modified.  The conversion itself is spp_convert.convert(), the same code the CLI runs.

    substance-version-tool [FILE.spp ...]
    substance-version-tool --version
    substance-version-tool --self-test      # for CI: checks the bundled libraries load, then exits
    substance-version-tool --check-update   # prints the newest release and how this copy updates
"""
import glob
import io
import os
import re
import subprocess
import sys
import tempfile
import threading

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QDesktopServices, QFont, QFontDatabase, QIcon, QKeySequence,
                           QPainter, QShortcut)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QRadioButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spp_convert as sc                                 # noqa: E402
import sppformat as sf                                   # noqa: E402
import updater                                           # noqa: E402

APP_NAME = 'Substance Version Tool'
PATH_ROLE = Qt.ItemDataRole.UserRole


# ---------------------------------------------------------------------------
# Finding the installed Painter
# ---------------------------------------------------------------------------
def painter_executables():
    """Painter executables on this machine, best guesses first.  Linux only so far: the
    Windows and macOS installs (and whether their --version exits without opening a
    window) have not been checked."""
    if not sys.platform.startswith('linux'):
        return []
    home = os.path.expanduser('~')
    dirs = []
    if os.environ.get('PAINTER_DIR'):
        dirs.append(os.environ['PAINTER_DIR'])
    # The launcher written by install.sh records where Painter is.
    try:
        with open(os.path.join(home, '.local/bin/substance-painter'), encoding='utf-8') as f:
            m = re.search(r'^PAINTER_DIR="(.*)"$', f.read(), re.M)
            if m:
                dirs.append(m.group(1))
    except OSError:
        pass
    for pattern in ('/opt/Adobe/Adobe_Substance_3D_Painter*',
                    '~/.local/share/Steam/steamapps/common/Substance 3D Painter*',
                    '~/.steam/steam/steamapps/common/Substance 3D Painter*',
                    '~/Documents/Substance 3D Painter*'):
        dirs += sorted(glob.glob(os.path.expanduser(pattern)))
    found = []
    for d in dirs:
        exe = os.path.join(d, 'Adobe Substance 3D Painter')
        if os.access(exe, os.X_OK) and os.path.realpath(exe) not in found:
            found.append(os.path.realpath(exe))
    return found


def child_env():
    """The environment for programs this app starts.  A PyInstaller build points
    LD_LIBRARY_PATH (and possibly Qt's plugin path) at its own bundled libraries; Painter
    or a file manager started with that would load this app's Qt instead of their own."""
    env = dict(os.environ)
    if getattr(sys, 'frozen', False):
        bundle = os.path.realpath(getattr(sys, '_MEIPASS', os.path.dirname(sys.executable)))
        for var in ('LD_LIBRARY_PATH', 'DYLD_LIBRARY_PATH'):
            orig = env.pop(var + '_ORIG', None)
            if orig is not None:
                env[var] = orig
                continue
            # Keep whatever the user had there; drop only the bundle's own directories.
            keep = [d for d in env.get(var, '').split(os.pathsep)
                    if d and not os.path.realpath(d).startswith(bundle)]
            if keep:
                env[var] = os.pathsep.join(keep)
            else:
                env.pop(var, None)
        for var in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH',
                    'PYTHONHOME', 'PYTHONPATH'):
            env.pop(var, None)
        for var in [v for v in env if v.startswith('_PYI_')]:
            del env[var]
    return env


def painter_version(exe):
    """'12.0.1' from `<exe> --version`, which prints (to stderr) e.g. 'Substance 3D Painter
    12.0.1+5292.clang-linux...' and exits without opening a window."""
    try:
        r = subprocess.run([exe, '--version'], capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL, cwd=os.path.dirname(exe), env=child_env())
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'Substance 3D Painter\s+(\d+\.\d+(?:\.\d+)?)', r.stdout + r.stderr)
    return m.group(1) if m else None


def open_external(target):
    """Open a folder or a web address with the desktop's default program."""
    if sys.platform.startswith('linux'):
        # Not QDesktopServices: it would hand the file manager this app's environment.
        try:
            subprocess.Popen(['xdg-open', target], env=child_env(), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return
        except OSError:
            pass
    QDesktopServices.openUrl(QUrl(target) if '://' in target else QUrl.fromLocalFile(target))


class PainterDetector(QThread):
    found = Signal(list)       # ['12.0.1', ...], newest first

    def run(self):
        versions = {v for v in map(painter_version, painter_executables()) if v}
        self.found.emit(sorted(versions, key=lambda v: tuple(map(int, v.split('.'))), reverse=True))


# ---------------------------------------------------------------------------
# Conversion worker
# ---------------------------------------------------------------------------
class Converter(QThread):
    started_one = Signal(str)                  # src
    finished_one = Signal(str, bool, str, str)  # src, ok, report text, dst

    def __init__(self, jobs, target, drop_cache):
        super().__init__()
        self.jobs = jobs           # [(src, dst)]
        self.target = target
        self.drop_cache = drop_cache
        self.stop = False

    def run(self):
        for src, dst in self.jobs:
            if self.stop:
                break
            self.started_one.emit(src)
            try:
                report = sc.convert(src, dst, self.target, drop_cache=self.drop_cache)
            except Exception as e:     # noqa: BLE001 - shown to the user, never fatal
                self.finished_one.emit(src, False, f'{src}\n  ERROR: {e}\n', dst)
                continue
            buf = io.StringIO()
            sc.print_report(report, src, dst, out=buf)
            self.finished_one.emit(src, True, buf.getvalue(), dst)


class UpdateSignals(QObject):
    """Results of the update check and download, which run in plain daemon threads (a
    pending network request then never holds up quitting)."""
    found = Signal(str)                 # newer version
    uptodate = Signal(str, bool)        # newest version, manual check
    failed = Signal(str, bool)          # message, manual check
    progress = Signal(int)              # percent, -1 if unknown
    ready = Signal(str)                 # downloaded file
    download_failed = Signal(str)


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------
class FileList(QTreeWidget):
    """The project list; also the drop target for files and folders."""
    dropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setHeaderLabels(['Project', 'Saved with', 'Status'])
        self.setRootIsDecorated(False)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)
        h = self.header()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setStretchLastSection(False)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            self.dropped.emit(paths)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.topLevelItemCount() == 0:
            p = QPainter(self.viewport())
            p.setPen(self.palette().placeholderText().color())
            p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                       'Drop Painter projects (.spp) here\nor use "Add projects…"')


class Window(QMainWindow):
    def __init__(self, check_updates=True):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.settings = QSettings()
        self.worker = None
        self.closing = False
        self.user_picked_target = False
        self.last_output_dir = None
        self.shown_target = None
        self.update_busy = False
        self.update_version = None
        self.banner_mode = None
        self.upd = UpdateSignals(self)
        self.upd.found.connect(self._update_found)
        self.upd.uptodate.connect(self._update_uptodate)
        self.upd.failed.connect(self._update_check_failed)
        self.upd.progress.connect(self._update_progress)
        self.upd.ready.connect(self._update_ready)
        self.upd.download_failed.connect(self._update_download_failed)

        help_menu = self.menuBar().addMenu('&Help')
        act = QAction('Check for Updates…', self)
        act.triggered.connect(lambda: self.check_updates(manual=True))
        help_menu.addAction(act)
        self.auto_update_act = QAction('Check for Updates at Start', self, checkable=True)
        self.auto_update_act.setChecked(self.settings.value('check_updates') == 'yes')
        self.auto_update_act.toggled.connect(
            lambda on: self.settings.setValue('check_updates', 'yes' if on else 'no'))
        help_menu.addAction(self.auto_update_act)
        help_menu.addSeparator()
        act = QAction(f'About {APP_NAME}', self)
        act.triggered.connect(self._about)
        help_menu.addAction(act)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Banner for the first-start question and for updates; hidden until needed.
        self.banner = QFrame()
        self.banner.setFrameShape(QFrame.Shape.StyledPanel)
        root.addWidget(self.banner)
        bar = QHBoxLayout(self.banner)
        bar.setContentsMargins(10, 6, 6, 6)
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        bar.addWidget(self.banner_label, 1)
        self.banner_buttons = []
        for i in range(3):
            b = QPushButton()
            b.clicked.connect(lambda _=False, i=i: self._banner_clicked(i))
            bar.addWidget(b)
            self.banner_buttons.append(b)
        self.banner.hide()

        # Target release
        row = QHBoxLayout()
        root.addLayout(row)     # first, so the widgets below get the window as parent
        row.addWidget(QLabel('Convert for Painter'))
        self.target_box = QComboBox()
        self._fill_targets([])
        self.target_box.activated.connect(self._target_picked)
        self.target_box.currentIndexChanged.connect(self._target_changed)
        row.addWidget(self.target_box)
        self.detect_label = QLabel('Looking for an installed Painter…')
        self.detect_label.setEnabled(False)
        row.addWidget(self.detect_label, 1)
        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        root.addWidget(self.note_label)

        # Projects and report
        split = QSplitter(Qt.Orientation.Vertical)
        self.files = FileList()
        self.files.dropped.connect(self.add_paths)
        self.files.itemSelectionChanged.connect(self._update_buttons)
        split.addWidget(self.files)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText('The conversion report appears here.')
        self.report.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        split.addWidget(self.report)
        split.setSizes([300, 180])
        root.addWidget(split, 1)

        row = QHBoxLayout()
        root.addLayout(row)     # first, so the widgets below get the window as parent
        add = QPushButton('Add projects…')
        add.clicked.connect(self._pick_files)
        row.addWidget(add)
        self.remove_btn = QPushButton('Remove')
        self.remove_btn.clicked.connect(self._remove_selected)
        row.addWidget(self.remove_btn)
        self.clear_btn = QPushButton('Clear')
        self.clear_btn.clicked.connect(self._clear)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        QShortcut(QKeySequence.StandardKey.Delete, self.files, self._remove_selected)

        # Output location and options
        row = QHBoxLayout()
        root.addLayout(row)     # first, so the widgets below get the window as parent
        row.addWidget(QLabel('Save converted copies'))
        self.out_next = QRadioButton('next to each project')
        self.out_folder = QRadioButton('in folder')
        group = QButtonGroup(self)
        group.addButton(self.out_next)
        group.addButton(self.out_folder)
        row.addWidget(self.out_next)
        row.addWidget(self.out_folder)
        self.folder_edit = QLineEdit(self.settings.value('output_dir', ''))
        self.folder_edit.setPlaceholderText('choose a folder')
        row.addWidget(self.folder_edit, 1)
        browse = QPushButton('Choose…')
        browse.clicked.connect(self._pick_folder)
        row.addWidget(browse)
        (self.out_folder if self.settings.value('output_mode') == 'folder' else self.out_next).setChecked(True)
        self.folder_edit.textChanged.connect(lambda: self.out_folder.setChecked(True))

        self.drop_cache = QCheckBox('Leave out the texture cache (Painter recomputes it on open; '
                                    'use this if a converted project shows stale textures)')
        root.addWidget(self.drop_cache)

        row = QHBoxLayout()
        root.addLayout(row)     # first, so the widgets below get the window as parent
        self.status = QLabel()
        row.addWidget(self.status, 1)
        self.show_btn = QPushButton('Show output folder')
        self.show_btn.clicked.connect(self._show_output)
        self.show_btn.setEnabled(False)
        row.addWidget(self.show_btn)
        self.convert_btn = QPushButton('Convert')
        self.convert_btn.setDefault(True)
        f = self.convert_btn.font()
        f.setWeight(QFont.Weight.Bold)
        self.convert_btn.setFont(f)
        self.convert_btn.clicked.connect(self.convert)
        row.addWidget(self.convert_btn)

        self.resize(860, 620)
        self._target_changed()

        self.detector = PainterDetector()
        self.detector.found.connect(self._painter_found)
        self.detector.start()

        if check_updates:
            QTimer.singleShot(1500, self._startup_update_check)

    # -- target -------------------------------------------------------------
    def _fill_targets(self, installed):
        """One entry per supported release, newest first.  An installed release is listed
        with its full version, so the output name says exactly which Painter it is for."""
        current = self.target_box.currentData()
        self.target_box.blockSignals(True)
        self.target_box.clear()
        by_release = {}
        for v in installed:
            by_release.setdefault(tuple(map(int, v.split('.')[:2])), v)
        for rel in reversed(sc.RELEASES):
            name = f'{rel[0]}.{rel[1]}'
            spec = by_release.get(rel, name)
            self.target_box.addItem(f'{spec}   (installed)' if rel in by_release else name, spec)
        self.target_box.blockSignals(False)
        want = current or self.settings.value('target') or sc.DEFAULT_TARGET
        self._select_target(want)

    def _select_target(self, spec):
        try:
            rel = sc.Target(spec).release
        except sc.ConversionError:
            return
        for i in range(self.target_box.count()):
            if sc.Target(self.target_box.itemData(i)).release == rel:
                self.target_box.setCurrentIndex(i)
                return

    def _target_picked(self):
        self.user_picked_target = True
        self.settings.setValue('target', self.target_box.currentData())

    def _painter_found(self, versions):
        usable = [v for v in versions if tuple(map(int, v.split('.')[:2])) in sc.RELEASES]
        if not versions:
            self.detect_label.setText('No installed Painter found; pick the release by hand.'
                                      if sys.platform.startswith('linux') else '')
            return
        self.detect_label.setText('Installed: Painter ' + ', '.join(versions))
        self._fill_targets(usable)
        if usable and not self.user_picked_target:
            self._select_target(usable[0])
        self._target_changed()

    def target(self):
        return sc.Target(self.target_box.currentData())

    def _target_changed(self, *_):
        t = self.target()
        note = t.note()
        if note:
            # The GUI has no data-version setting; that retry is for the command line.
            note = note.replace('retry with --data-version', 'convert it again with the '
                                'substance-version-tool command and --data-version')
            self.note_label.setText(note[0].upper() + note[1:] + '.')
        self.note_label.setVisible(bool(note))
        changed = self.shown_target is not None and t.label != self.shown_target
        self.shown_target = t.label
        for i in range(self.files.topLevelItemCount()):
            item = self.files.topLevelItem(i)
            if changed:
                item.result = item.converted = None
            self._update_status(item)
        self._update_buttons()

    # -- project list -------------------------------------------------------
    def add_paths(self, paths):
        have = {self.files.topLevelItem(i).data(0, PATH_ROLE)
                for i in range(self.files.topLevelItemCount())}
        for p in paths:
            p = os.path.abspath(p)
            batch = (sorted(glob.glob(os.path.join(glob.escape(p), '*.spp')))
                     if os.path.isdir(p) else [p])
            for f in batch:
                if f in have:
                    continue
                have.add(f)
                item = QTreeWidgetItem([os.path.basename(f), '', ''])
                item.setData(0, PATH_ROLE, f)
                item.setToolTip(0, f)
                self._read_info(item)
                self.files.addTopLevelItem(item)
        self._update_buttons()

    def _read_info(self, item):
        path = item.data(0, PATH_ROLE)
        try:
            info = sc.ProjectInfo(path)
        except (sc.ConversionError, sf.FormatError, OSError) as e:
            item.info, item.error = None, str(e) if not isinstance(e, OSError) else 'not a Painter project'
            item.setText(1, '—')
        else:
            item.info, item.error = info, None
            item.setText(1, f'{info.saved_label()}  (data {info.data_version})')
        item.result = item.converted = None
        self._update_status(item)

    def _update_status(self, item):
        if item.result:
            text, tip = item.result
        elif item.error:
            text, tip = 'cannot read', item.error
        elif item.info.opens_in(self.target()):
            text, tip = 'opens as it is', f'Painter {self.target().name} opens this project without conversion.'
        else:
            text, tip = 'needs conversion', ''
        item.setText(2, text)
        item.setToolTip(2, tip)

    def _pending(self):
        t = self.target()
        out = []
        for i in range(self.files.topLevelItemCount()):
            item = self.files.topLevelItem(i)
            if item.info and not item.error and not item.converted and not item.info.opens_in(t):
                out.append(item)
        return out

    def _update_buttons(self):
        busy = self.worker is not None
        n = len(self._pending())
        self.convert_btn.setText(f'Convert {n} project{"s" if n != 1 else ""}' if n else 'Convert')
        self.convert_btn.setEnabled(n > 0 and not busy)
        self.remove_btn.setEnabled(bool(self.files.selectedItems()) and not busy)
        self.clear_btn.setEnabled(self.files.topLevelItemCount() > 0 and not busy)
        self.target_box.setEnabled(not busy)

    def _pick_files(self):
        start = self.settings.value('last_dir', os.path.expanduser('~'))
        paths, _ = QFileDialog.getOpenFileNames(self, 'Add Painter projects', start,
                                                'Painter projects (*.spp);;All files (*)')
        if paths:
            self.settings.setValue('last_dir', os.path.dirname(paths[0]))
            self.add_paths(paths)

    def _remove_selected(self):
        if self.worker:
            return
        for item in self.files.selectedItems():
            self.files.takeTopLevelItem(self.files.indexOfTopLevelItem(item))
        self._update_buttons()

    def _clear(self):
        self.files.clear()
        self.report.clear()
        self.status.clear()
        self._update_buttons()

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, 'Save converted copies in',
                                             self.folder_edit.text() or os.path.expanduser('~'))
        if d:
            self.folder_edit.setText(d)
            self.out_folder.setChecked(True)

    # -- converting ---------------------------------------------------------
    def _destination(self, src, target):
        dst = sc.default_output(src, target)
        if self.out_folder.isChecked():
            dst = os.path.join(self.folder_edit.text(), os.path.basename(dst))
        return dst

    def convert(self):
        items = self._pending()
        if not items or self.worker:
            return
        target = self.target()
        if self.out_folder.isChecked():
            folder = self.folder_edit.text().strip()
            if not folder or not os.path.isdir(folder):
                QMessageBox.warning(self, APP_NAME, 'Choose an existing folder for the converted '
                                    'copies, or save them next to each project.')
                return
            self.settings.setValue('output_dir', folder)
        self.settings.setValue('output_mode', 'folder' if self.out_folder.isChecked() else 'next')

        jobs = [(it.data(0, PATH_ROLE), self._destination(it.data(0, PATH_ROLE), target)) for it in items]
        dsts = [d for _, d in jobs]
        clash = len(set(dsts)) != len(dsts)
        if clash:
            QMessageBox.warning(self, APP_NAME, 'Two of the projects have the same name, so their '
                                'converted copies would overwrite each other in the output folder. '
                                'Save the copies next to each project instead.')
            return
        existing = [d for d in dsts if os.path.exists(d)]
        if existing:
            names = '\n'.join(os.path.basename(d) for d in existing[:8])
            more = f'\n… and {len(existing) - 8} more' if len(existing) > 8 else ''
            if QMessageBox.question(self, APP_NAME, f'These converted copies already exist:\n\n'
                                    f'{names}{more}\n\nReplace them?') != QMessageBox.StandardButton.Yes:
                return

        self.items_by_path = {it.data(0, PATH_ROLE): it for it in items}
        self.done = self.failed = 0
        self.total = len(jobs)
        self.report.clear()
        self.worker = Converter(jobs, target, self.drop_cache.isChecked())
        self.worker.started_one.connect(self._started_one)
        self.worker.finished_one.connect(self._finished_one)
        self.worker.finished.connect(self._all_done)
        self._update_buttons()
        self.worker.start()

    def _started_one(self, src):
        item = self.items_by_path[src]
        item.setText(2, 'converting…')
        self.status.setText(f'Converting {self.done + self.failed + 1} of {self.total}: '
                            f'{os.path.basename(src)}')

    def _finished_one(self, src, ok, text, dst):
        item = self.items_by_path[src]
        if ok:
            self.done += 1
            item.result = (f'converted → {os.path.basename(dst)}', dst)
            item.converted = dst
            self.last_output_dir = os.path.dirname(dst)
        else:
            self.failed += 1
            item.result = ('failed (see report)', text.strip())
        self._update_status(item)
        self.report.appendPlainText(text)

    def _all_done(self):
        self.worker.deleteLater()
        self.worker = None
        parts = [f'{self.done} converted']
        if self.failed:
            parts.append(f'{self.failed} failed')
        if self.done + self.failed < self.total:
            parts.append(f'{self.total - self.done - self.failed} not started')
        self.status.setText('Done: ' + ', '.join(parts) + '.')
        self.show_btn.setEnabled(self.last_output_dir is not None)
        self._update_buttons()
        if self.closing:
            self.close()

    def _show_output(self):
        if self.last_output_dir:
            open_external(self.last_output_dir)

    # -- updates --------------------------------------------------------------
    # Nothing is sent anywhere unless the user agreed: the first start asks whether to
    # check for updates (Help menu to change it later). A check is one request to
    # github.com; see updater.py.
    def _show_banner(self, mode, text, buttons):
        self.banner_mode = mode
        self.banner_label.setText(text)
        for b, label in zip(self.banner_buttons, buttons + [None] * 3):
            b.setVisible(bool(label))
            b.setEnabled(True)
            if label:
                b.setText(label)
        self.banner.show()

    def _startup_update_check(self):
        choice = self.settings.value('check_updates')
        if choice is None:
            self._show_banner('ask', f'Should {APP_NAME} check GitHub for a newer version each time '
                              'it starts? Nothing else is sent.', ['Yes', 'No'])
        elif choice == 'yes':
            self.check_updates(manual=False)

    def check_updates(self, manual=False):
        if self.update_busy:
            return
        self.update_busy = True
        current = sc.__version__

        def work():
            try:
                latest = updater.latest_version()
            except updater.UpdateError as e:
                self.upd.failed.emit(str(e), manual)
                return
            if updater.version_tuple(latest) > updater.version_tuple(current):
                self.upd.found.emit(latest)
            else:
                self.upd.uptodate.emit(latest, manual)
        threading.Thread(target=work, daemon=True).start()

    def _update_found(self, version):
        self.update_busy = False
        self.update_version = version
        can_install = updater.install_kind() in ('installer', 'appimage')
        self._show_banner('update', f'<b>Version {version} is available</b> (you have {sc.__version__}).',
                          ['Update and restart' if can_install else 'Download…', "What's new", 'Later'])

    def _update_uptodate(self, latest, manual):
        self.update_busy = False
        if manual:
            QMessageBox.information(self, APP_NAME, f'You have the newest version ({sc.__version__}).')

    def _update_check_failed(self, message, manual):
        self.update_busy = False
        if manual:
            QMessageBox.warning(self, APP_NAME, f'Could not check for updates: {message}')

    def _banner_clicked(self, i):
        mode, version = self.banner_mode, self.update_version
        if mode == 'ask':
            self.banner.hide()
            self.auto_update_act.setChecked(i == 0)    # also stores the choice
            self.settings.setValue('check_updates', 'yes' if i == 0 else 'no')
            if i == 0:
                self.check_updates(manual=False)
        elif mode == 'update':
            if i == 0:
                self.start_update()
            elif i == 1:
                open_external(updater.release_page(version))
            else:
                self.banner.hide()
        elif mode == 'failed':
            if i == 0:
                open_external(updater.release_page(version))
            self.banner.hide()

    def start_update(self):
        version, kind = self.update_version, updater.install_kind()
        if kind not in ('installer', 'appimage'):
            open_external(updater.release_page(version))
            return
        if self.worker:
            QMessageBox.information(self, APP_NAME, 'Wait until the conversion has finished, then update.')
            return
        for b in self.banner_buttons:
            b.setEnabled(False)
        self.banner_label.setText(f'Downloading version {version}…')

        def work():
            last = [None]

            def progress(done, total):
                pct = done * 100 // total if total else -1
                if pct != last[0]:
                    last[0] = pct
                    self.upd.progress.emit(pct)
            try:
                self.upd.ready.emit(updater.download(version, kind, progress))
            except updater.UpdateError as e:
                self.upd.download_failed.emit(str(e))
        threading.Thread(target=work, daemon=True).start()

    def _update_progress(self, pct):
        if self.banner_mode == 'update':
            more = f' {pct}%' if pct >= 0 else ''
            self.banner_label.setText(f'Downloading version {self.update_version}…{more}')

    def _update_ready(self, path):
        kind = updater.install_kind()
        self.banner_label.setText(f'Installing version {self.update_version}; {APP_NAME} restarts by itself…')
        QApplication.processEvents()
        try:
            updater.apply(path, kind, env=child_env())
        except (OSError, updater.UpdateError) as e:
            self._update_download_failed(str(e))
            return
        QTimer.singleShot(300, QApplication.quit)

    def _update_download_failed(self, message):
        self._show_banner('failed', f'The update did not work: {message}',
                          ['Download it yourself…', 'Close'])

    def _about(self):
        QMessageBox.about(self, f'About {APP_NAME}', (
            f'<h3>{APP_NAME} {sc.__version__}</h3>'
            '<p>Makes Substance 3D Painter projects open in an older Painter release.</p>'
            f'<p><a href="https://github.com/{updater.REPO}">github.com/{updater.REPO}</a><br>'
            'MIT License.</p>'
            '<p>Substance 3D Painter is a product of Adobe. This is an independent tool, '
            'not affiliated with or endorsed by Adobe.</p>'))

    def closeEvent(self, e):
        if self.worker:
            # Let the project being written finish; a half-written copy is never left behind,
            # but stopping mid-file would leave a temporary file.
            self.worker.stop = True
            self.closing = True
            self.status.setText('Finishing the current project, then closing…')
            e.ignore()
            return
        e.accept()


# ---------------------------------------------------------------------------
def self_test():
    """Exercise what a frozen build could be missing: Qt's platform plugin, h5py's HDF5,
    numpy and mmh3.  Run with QT_QPA_PLATFORM=offscreen when there is no display.
    Returns the line to report; raises on failure."""
    app = QApplication.instance()
    w = Window(check_updates=False)
    w.show()
    # The update check needs TLS: make sure the bundle has it and its certificates load.
    import ssl
    ssl.create_default_context()
    assert updater.version_tuple('v0.10.0') > updater.version_tuple('0.9.9') > updater.version_tuple('0.9')
    assert updater.install_kind() in ('installer', 'appimage', 'portable', 'source')
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'not-a-project.spp')
        import h5py
        with h5py.File(path, 'w', libver=('earliest', 'v114')) as f:
            sc._put(f, 'other/blob.bin', b'x' * 70000)
        with h5py.File(path, 'r') as f:
            ds = f['other/blob.bin']
            assert list(ds.attrs['m3_x64_128']) == list(sf.content_hash(bytes(ds[()])))
        w.add_paths([d])
        item = w.files.topLevelItem(0)
        assert item is not None and item.error, 'a file without paint/document.bin must be refused'
    assert w.target_box.count() == len(sc.RELEASES)
    w.detector.wait(60000)
    app.processEvents()
    w.close()
    app.processEvents()
    return (f'{APP_NAME} {sc.__version__}: self-test ok '
            f'(Qt platform "{app.platformName()}", {w.target_box.count()} releases; '
            f'{w.detect_label.text() or "no Painter detection on this system"}; '
            f'updates: {updater.install_kind()})')


def report_self_test():
    """Run self_test() and report on stdout and, if set, in the file $SPP_SELFTEST_LOG.
    The Windows build has no console (stdout is None) and shows a dialog for an uncaught
    exception, which would hang CI; so failures become an exit code and a log."""
    try:
        text, rc = self_test(), 0
    except BaseException:                      # noqa: BLE001 - reported, not raised
        import traceback
        text, rc = f'{APP_NAME} {sc.__version__}: self-test FAILED\n{traceback.format_exc()}', 1
    if sys.stdout is not None:
        print(text)
    if os.environ.get('SPP_SELFTEST_LOG'):
        with open(os.environ['SPP_SELFTEST_LOG'], 'w', encoding='utf-8') as f:
            f.write(text + '\n')
    return rc


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if '--version' in argv:
        if sys.stdout is not None:
            print(f'{APP_NAME} {sc.__version__}')
        return 0
    if '--check-update' in argv:
        try:
            latest = updater.latest_version()
        except updater.UpdateError as e:
            latest = f'unknown ({e})'
        if sys.stdout is not None:
            print(f'current {sc.__version__}, newest release {latest}, '
                  f'this copy updates as: {updater.install_kind()}')
        return 0
    app = QApplication(sys.argv[:1])
    app.setOrganizationName('substance-version-tool')
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(sc.__version__)
    app.setDesktopFileName('substance-version-tool')
    # The bundle carries a PNG (rendered at build time); a checkout has the SVG.
    # (On Windows the taskbar icon comes from the .exe itself.)
    for icon in (os.path.join(getattr(sys, '_MEIPASS', ''), 'substance-version-tool.png'),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), 'packaging', 'substance-version-tool.svg')):
        if os.path.isfile(icon):
            app.setWindowIcon(QIcon(icon))
            break
    if '--self-test' in argv:
        return report_self_test()
    w = Window()
    w.show()
    files = [a for a in argv if not a.startswith('-')]
    if files:
        QTimer.singleShot(0, lambda: w.add_paths(files))
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
