"""Keyboard navigation for Substance 3D Painter's 3D viewport.

Painter only navigates with Alt + mouse buttons, which is awkward on a laptop
touchpad. This plugin adds a toggle (Viewport menu, the arrows button in the plugin
toolbar, or the ` key) that turns a few keys into camera controls:

    arrows / W A S D          orbit around the model
    Shift + arrows / WASD     pan
    + / -   E / Q   PgDn/PgUp zoom in / out
    Home                      frame the whole model
    Ctrl + left/right or A/D  rotate the light (environment)

While the toggle is on these keys no longer reach Painter (so S, D, etc. lose
their usual meaning) and the Ctrl / Shift shortcut-hint popup is hidden; typing
in text fields is unaffected. Toggle it off to get
Painter's normal shortcuts back. Only the 3D view is driven; the 2D view has no
scripting interface.
"""
import math
import time

import shiboken6
from PySide6 import QtCore, QtGui, QtWidgets

import substance_painter.display
import substance_painter.logging
import substance_painter.project
import substance_painter.ui

# ---- settings ---------------------------------------------------------------
TOGGLE_SHORTCUT = "`"
ORBIT_SPEED = 100.0     # degrees per second
PAN_SPEED = 0.9         # view heights per second
ZOOM_SPEED = 1.6        # e-folds per second
LIGHT_SPEED = 90.0      # degrees per second
INVERT_ORBIT = False    # False: the camera moves the way the key points
INVERT_PAN = False
MAX_ELEVATION = 89.0    # degrees; stops the view flipping over the poles
TICK_MS = 16

# Camera.rotation is (x, y, z) Euler angles in degrees. Painter composes them as
# R = Rx * Ry * Rz (measured against the quaternion Painter saves in a project's
# editor/camera.ini); the camera looks down its local -Z with +Y up.
EULER_ORDER = "XYZ"

HELPER_NAME = "keyboard_help_frame"   # Painter's modifier-key hint popup

Qt = QtCore.Qt
_K = Qt.Key
KEYS = {
    _K.Key_Left: "left", _K.Key_A: "left",
    _K.Key_Right: "right", _K.Key_D: "right",
    _K.Key_Up: "up", _K.Key_W: "up",
    _K.Key_Down: "down", _K.Key_S: "down",
    _K.Key_Plus: "in", _K.Key_Equal: "in", _K.Key_E: "in", _K.Key_PageDown: "in",
    _K.Key_Minus: "out", _K.Key_Underscore: "out", _K.Key_Q: "out",
    _K.Key_PageUp: "out",
    _K.Key_Home: "frame",
}
CTRL_KEYS = {  # with Ctrl held
    "left": "light-", "right": "light+",
}
_BLOCKING_MODS = Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier


# ---- quaternion maths, (x, y, z, w) ------------------------------------------
def q_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def q_axis(axis, degrees):
    h = math.radians(degrees) / 2.0
    q = [0.0, 0.0, 0.0, math.cos(h)]
    q["XYZ".index(axis)] = math.sin(h)
    return tuple(q)


def q_normalized(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def q_matrix(q):
    x, y, z, w = q
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))


def q_rotate(q, v):
    m = q_matrix(q)
    return tuple(m[r][0] * v[0] + m[r][1] * v[1] + m[r][2] * v[2] for r in range(3))


def euler_to_quat(euler, order=EULER_ORDER):
    """(x, y, z) degrees -> quaternion, R = R[order[0]] * R[order[1]] * R[order[2]]."""
    q = (0.0, 0.0, 0.0, 1.0)
    for axis in order:
        q = q_mul(q, q_axis(axis, euler["XYZ".index(axis)]))
    return q


def quat_to_euler(q, order=EULER_ORDER):
    """Inverse of euler_to_quat; returns [x, y, z] in degrees."""
    m = q_matrix(q)
    i, j, k = ("XYZ".index(c) for c in order)
    s = 1.0 if (j - i) % 3 == 1 else -1.0
    out = [0.0, 0.0, 0.0]
    sin_mid = max(-1.0, min(1.0, s * m[i][k]))
    out[j] = math.degrees(math.asin(sin_mid))
    if abs(sin_mid) < 0.999999:
        out[i] = math.degrees(math.atan2(-s * m[j][k], m[k][k]))
        out[k] = math.degrees(math.atan2(-s * m[i][j], m[i][i]))
    else:  # gimbal lock: put everything on the first axis
        out[i] = math.degrees(math.atan2(s * m[k][j], m[j][j]))
    return out


def _add(a, b, scale=1.0):
    return tuple(x + y * scale for x, y in zip(a, b))


# ---- navigation --------------------------------------------------------------
class Navigator(QtCore.QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = False
        self._held = {}          # native scan code -> action
        self._pose = None        # (quat, pivot, distance) while keys are held
        self._last = 0.0
        self._light_slider = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    def set_enabled(self, on):
        self.enabled = bool(on)
        if not on:
            self._stop()
        for widget in QtWidgets.QApplication.allWidgets():
            if widget.objectName() == HELPER_NAME:
                self._collapse_helper(widget, self.enabled)

    @staticmethod
    def _collapse_helper(widget, collapse):
        """Keep Painter's Ctrl / Shift shortcut-hint popup out of sight.

        The viewports show it whenever a modifier is held, which would be every
        time you pan or turn the light. Squeezing it to zero size leaves
        Painter's own "show keyboard helper" preference untouched.
        """
        size = 0 if collapse else 16777215  # QWIDGETSIZE_MAX
        widget.setMaximumSize(size, size)

    def _stop(self):
        self._held.clear()
        self._pose = None
        self._timer.stop()

    # -- input --
    def eventFilter(self, obj, event):
        if not self.enabled:
            return False
        kind = event.type()
        if kind == QtCore.QEvent.Type.Show:  # a helper created after the toggle
            if obj.objectName() == HELPER_NAME and obj.maximumHeight() != 0:
                self._collapse_helper(obj, True)
            return False
        if kind in (QtCore.QEvent.Type.KeyPress, QtCore.QEvent.Type.KeyRelease,
                    QtCore.QEvent.Type.ShortcutOverride):
            return self._key_event(kind, event)
        if kind in (QtCore.QEvent.Type.ApplicationDeactivate,
                    QtCore.QEvent.Type.WindowDeactivate) and self._held:
            self._stop()
        return False

    def _key_event(self, kind, event):
        scan = event.nativeScanCode()
        if kind == QtCore.QEvent.Type.KeyRelease:
            if scan not in self._held:
                return False
            if not event.isAutoRepeat():
                del self._held[scan]
            event.accept()
            return True
        action = KEYS.get(event.key())
        if action is None or event.modifiers() & _BLOCKING_MODS:
            return False
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            action = CTRL_KEYS.get(action)
            if action is None or event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                return False
        if scan not in self._held and not self._may_navigate():
            return False
        event.accept()
        if kind == QtCore.QEvent.Type.ShortcutOverride or event.isAutoRepeat():
            return True
        if action == "frame":
            self._guarded(self._frame)
            return True
        self._held[scan] = action
        if not self._timer.isActive():
            self._last = time.monotonic()
            self._timer.start()
        return True

    @staticmethod
    def _may_navigate():
        """True unless a dialog is up, no project is open, or the user is typing."""
        if QtWidgets.QApplication.activeModalWidget() or QtWidgets.QApplication.activePopupWidget():
            return False
        if not substance_painter.project.is_open():
            return False
        focus = QtGui.QGuiApplication.focusObject()
        if focus is None:
            return True
        if isinstance(focus, QtWidgets.QWidget):
            if isinstance(focus, (QtWidgets.QLineEdit, QtWidgets.QTextEdit,
                                  QtWidgets.QPlainTextEdit, QtWidgets.QAbstractSpinBox)):
                return False
            if isinstance(focus, QtWidgets.QComboBox) and focus.isEditable():
                return False
            if not focus.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled):
                return True
        query = QtGui.QInputMethodQueryEvent(Qt.InputMethodQuery.ImEnabled)
        QtCore.QCoreApplication.sendEvent(focus, query)
        return not query.value(Qt.InputMethodQuery.ImEnabled)

    # -- camera --
    def _guarded(self, func):
        try:
            func()
        except Exception as exc:  # no project, camera gone, ... : just stop moving
            self._stop()
            substance_painter.logging.warning(f"keyboard_nav: {exc}")

    @staticmethod
    def _camera():
        return substance_painter.display.Camera.get_default_camera()

    @staticmethod
    def _view_height(cam, distance):
        """World-space height of the view at the pivot."""
        if cam.projection_type == substance_painter.display.CameraProjectionType.Orthographic:
            return cam.orthographic_height
        return 2.0 * distance * math.tan(math.radians(cam.field_of_view) / 2.0)

    def _read_pose(self, cam):
        quat = q_normalized(euler_to_quat(cam.rotation))
        pos = tuple(cam.position)
        box = substance_painter.project.get_scene_bounding_box()
        forward = q_rotate(quat, (0.0, 0.0, -1.0))
        to_center = _add(box.center, pos, -1.0)
        radius = max(box.radius, 1e-6)
        distance = max(sum(a * b for a, b in zip(to_center, forward)), 0.1 * radius)
        return quat, _add(pos, forward, distance), distance

    @staticmethod
    def _write_pose(cam, quat, pivot, distance):
        cam.rotation = quat_to_euler(quat)
        cam.position = list(_add(pivot, q_rotate(quat, (0.0, 0.0, 1.0)), distance))

    def _tick(self):
        if not self._held or (QtGui.QGuiApplication.applicationState()
                              != Qt.ApplicationState.ApplicationActive):
            self._stop()
            return
        self._guarded(self._step)

    def _step(self):
        now = time.monotonic()
        dt = min(now - self._last, 0.05)
        self._last = now
        held = set(self._held.values())
        dx = ("right" in held) - ("left" in held)
        dy = ("up" in held) - ("down" in held)
        dz = ("in" in held) - ("out" in held)
        dl = ("light+" in held) - ("light-" in held)
        if dl:
            self._rotate_light(dl * LIGHT_SPEED * dt)
        if not (dx or dy or dz):
            return

        cam = self._camera()
        if self._pose is None:
            self._pose = self._read_pose(cam)
        quat, pivot, distance = self._pose
        shift = bool(QtGui.QGuiApplication.keyboardModifiers()
                     & Qt.KeyboardModifier.ShiftModifier)

        if (dx or dy) and shift:
            step = self._view_height(cam, distance) * PAN_SPEED * dt
            step = -step if INVERT_PAN else step
            pivot = _add(pivot, q_rotate(quat, (1.0, 0.0, 0.0)), dx * step)
            pivot = _add(pivot, q_rotate(quat, (0.0, 1.0, 0.0)), dy * step)
        elif dx or dy:
            step = ORBIT_SPEED * dt * (-1.0 if INVERT_ORBIT else 1.0)
            back = q_rotate(quat, (0.0, 0.0, 1.0))
            elevation = math.degrees(math.asin(max(-1.0, min(1.0, back[1]))))
            target = max(-MAX_ELEVATION, min(MAX_ELEVATION, elevation + dy * step))
            quat = q_mul(q_axis("Y", dx * step), quat)             # yaw about world up
            quat = q_mul(quat, q_axis("X", -(target - elevation)))  # pitch about camera right
            quat = q_normalized(quat)

        if dz:
            factor = math.exp(-dz * ZOOM_SPEED * dt)
            if cam.projection_type == substance_painter.display.CameraProjectionType.Orthographic:
                cam.orthographic_height = cam.orthographic_height * factor
            else:
                radius = max(substance_painter.project.get_scene_bounding_box().radius, 1e-6)
                distance = max(distance * factor, radius * 1e-3)

        self._pose = (quat, pivot, distance)
        self._write_pose(cam, quat, pivot, distance)

    def _rotate_light(self, degrees):
        """Turn the environment map, like Shift + right-drag.

        Painter has no scripting call for this, so drive the "Environment
        Rotation" slider of the Display settings panel (it exists even while
        that panel is closed).
        """
        slider = self._light_slider
        if slider is None or not shiboken6.isValid(slider):
            slider = next((w for w in QtWidgets.QApplication.allWidgets()
                           if w.objectName() == "envRotation"), None)
            self._light_slider = slider
            if slider is None:
                raise RuntimeError("environment rotation control not found")
        value = (float(slider.property("value")) + degrees) % 360.0
        if not QtCore.QMetaObject.invokeMethod(slider, "setValue", QtCore.Q_ARG(float, value)):
            slider.setProperty("value", value)

    def _frame(self):
        cam = self._camera()
        quat = q_normalized(euler_to_quat(cam.rotation))
        box = substance_painter.project.get_scene_bounding_box()
        radius = max(box.radius, 1e-6)
        if cam.projection_type == substance_painter.display.CameraProjectionType.Orthographic:
            cam.orthographic_height = 2.2 * radius
            distance = 3.0 * radius
        else:
            half_fov = math.radians(min(cam.field_of_view, 170.0)) / 2.0
            distance = 1.1 * radius / math.sin(half_fov)
        self._pose = None
        self._write_pose(cam, quat, tuple(box.center), distance)


# ---- plugin entry points -----------------------------------------------------
_nav = None
_action = None
_button = None


def _settings():
    return QtCore.QSettings("spp-tools", "keyboard_nav")


def _make_icon():
    """Four arrowheads around a dot; blue while navigation is on."""
    icon = QtGui.QIcon()
    for state, colour in ((QtGui.QIcon.State.Off, "#c8c8c8"), (QtGui.QIcon.State.On, "#4c9aff")):
        pixmap = QtGui.QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(colour))
        painter.translate(32, 32)
        painter.drawEllipse(QtCore.QPointF(0, 0), 6, 6)
        arrow = QtGui.QPolygonF([QtCore.QPointF(0, -28), QtCore.QPointF(11, -14),
                                 QtCore.QPointF(-11, -14)])
        for _ in range(4):
            painter.drawPolygon(arrow)
            painter.rotate(90)
        painter.end()
        icon.addPixmap(pixmap, QtGui.QIcon.Mode.Normal, state)
    return icon


def _toggled(on):
    _nav.set_enabled(on)
    _settings().setValue("enabled", bool(on))


def start_plugin():
    global _nav, _action, _button
    app = QtWidgets.QApplication.instance()
    window = substance_painter.ui.get_main_window()

    # No Qt parent: Painter tears its window down after Python has shut down, and
    # a Python-implemented QObject that is still alive then crashes the exit.
    _nav = Navigator()
    app.installEventFilter(_nav)

    _action = QtGui.QAction("Keyboard Navigation", window)
    _action.setCheckable(True)
    _action.setIcon(_make_icon())
    _action.setShortcut(QtGui.QKeySequence(TOGGLE_SHORTCUT))
    _action.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
    _action.setToolTip(
        f"Keyboard navigation ({TOGGLE_SHORTCUT})\n"
        "Arrows / WASD: orbit\nShift + arrows / WASD: pan\n"
        "+ / -, E / Q, PgDn / PgUp: zoom\nHome: frame model\n"
        "Ctrl + left / right or A / D: rotate light")
    _action.toggled.connect(_toggled)
    substance_painter.ui.add_action(substance_painter.ui.ApplicationMenu.Viewport, _action)

    _button = QtWidgets.QToolButton()
    _button.setDefaultAction(_action)
    _button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    substance_painter.ui.add_plugins_toolbar_widget(_button)

    if _settings().value("enabled", False, type=bool):
        _action.setChecked(True)


def close_plugin():
    global _nav, _action, _button
    app = QtWidgets.QApplication.instance()
    if _nav is not None:
        _nav.set_enabled(False)
        if app is not None:
            app.removeEventFilter(_nav)
    for element in (_button, _action, _nav):
        if element is not None:
            substance_painter.ui.delete_ui_element(element)
    _nav = _action = _button = None


if __name__ == "__main__":
    start_plugin()
