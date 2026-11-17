#!/usr/bin/env bash
# Sets up, for the current user:
#   * substance-version-tool - CLI converter: makes an .spp open in an older Painter
#                            (9.0 - 12.2). "spp-convert" and "spp-downgrade" are kept as aliases.
#   * substance-painter    - Painter launcher; .spp files newer than the installed Painter
#                            (PAINTER_VERSION below) are converted automatically
#   * an app-launcher entry, the Painter icon and the .spp file association
#   * desktop fixes applied by the launcher (see painter/): UI scale, a working
#     eyedropper under XWayland, and the keyboard-navigation plugin
# Re-run any time (e.g. after moving Painter: PAINTER_DIR=/new/path ./install.sh).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAINTER_DIR="${PAINTER_DIR:-$HOME/Documents/Substance 3D Painter 2026}"
PAINTER_BIN="$PAINTER_DIR/Adobe Substance 3D Painter"
# Version of the installed Painter: what the launcher converts newer projects to.
PAINTER_VERSION="${PAINTER_VERSION:-12.0.1}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
BIN="$HOME/.local/bin"
VENV="$DATA/spp-downgrade/venv"
SHIM="$DATA/spp-downgrade/libpainter-screengrab.so"
KWIN_GRAB="$DATA/spp-downgrade/painter-kwin-screengrab"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/substance-painter.conf"
APP_ID="adobe-substance-3d-painter"
MIME="application/x-substance-painter-project"

[ -x "$PAINTER_BIN" ] || { echo "Painter executable not found: $PAINTER_BIN (set PAINTER_DIR)" >&2; exit 1; }

echo "* Python environment ($VENV)"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --disable-pip-version-check h5py numpy mmh3

mkdir -p "$BIN" "$DATA/applications" "$DATA/mime/packages" \
         "$DATA/icons/hicolor/256x256/apps" "$DATA/icons/hicolor/256x256/mimetypes"

echo "* $BIN/substance-version-tool (and the spp-convert / spp-downgrade aliases)"
cat > "$BIN/substance-version-tool" <<EOF
#!/bin/sh
exec "$VENV/bin/python" -I "$HERE/spp_convert.py" "\$@"
EOF
chmod +x "$BIN/substance-version-tool"
rm -f "$BIN/spp-convert"
ln -sf substance-version-tool "$BIN/spp-convert"
ln -sf substance-version-tool "$BIN/spp-downgrade"

echo "* eyedropper fix ($SHIM)"
if command -v g++ >/dev/null && pkg-config --exists Qt6Widgets 2>/dev/null; then
    g++ -shared -fPIC -O2 -std=c++17 $(pkg-config --cflags Qt6Widgets) \
        -o "$SHIM" "$HERE/painter/screengrab_shim.cpp" -ldl
else
    rm -f "$SHIM"
    echo "  skipped: needs g++ and the Qt 6 headers (Fedora: sudo dnf install gcc-c++ qt6-qtbase-devel)"
fi

# KDE Plasma: the screenshot comes from KWin, which only serves programs that a
# .desktop file authorises for its screenshot interface.
rm -f "$KWIN_GRAB" "$DATA/applications/painter-kwin-screengrab.desktop"
if [ -f "$SHIM" ] && command -v kwin_wayland >/dev/null && pkg-config --exists Qt6DBus 2>/dev/null; then
    echo "* KWin screenshot helper ($KWIN_GRAB)"
    g++ -O2 -std=c++17 -fPIC $(pkg-config --cflags --libs Qt6DBus Qt6Gui) \
        -o "$KWIN_GRAB" "$HERE/painter/kwin_screengrab.cpp"
    cat > "$DATA/applications/painter-kwin-screengrab.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Substance 3D Painter eyedropper screenshot helper
Exec=$KWIN_GRAB
NoDisplay=true
X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2
EOF
fi

if [ ! -e "$CONF" ]; then
    echo "* $CONF"
    cat > "$CONF" <<'CONF_EOF'
# Settings for the substance-painter launcher (a shell fragment).

# Size of Painter's interface. "auto" follows niri's output scale and leaves the
# size alone elsewhere (KDE scales XWayland programs itself); or give a number,
# e.g. 1.5 for a larger UI, 1 for Painter's native size.
#PAINTER_UI_SCALE=auto

# Set to 0 to turn off the eyedropper (screen colour picker) fix.
#PAINTER_EYEDROPPER_FIX=1
CONF_EOF
fi

echo "* $BIN/substance-painter"
cat > "$BIN/substance-painter" <<EOF
#!/usr/bin/env bash
# Launch Substance 3D Painter. Project files saved by a newer Painter are first
# converted to a "<name>_$PAINTER_VERSION.spp" copy (see $HERE).
PAINTER_DIR="$PAINTER_DIR"
LOG="\${XDG_CACHE_HOME:-\$HOME/.cache}/spp-downgrade/launch.log"
mkdir -p "\$(dirname "\$LOG")"
notify() { command -v notify-send >/dev/null && notify-send -a "Substance 3D Painter" -i $APP_ID "\$@" || true; }
args=()
for a in "\$@"; do
    if [[ "\$a" == file://* ]]; then
        a="\$("$VENV/bin/python" -I -c 'import sys,urllib.parse as u;print(u.unquote(u.urlparse(sys.argv[1]).path))' "\$a")"
    fi
    if [[ "\${a,,}" == *.spp && -f "\$a" ]]; then
        a="\$(realpath "\$a")"
        if out="\$("$BIN/substance-version-tool" --to "$PAINTER_VERSION" --launch "\$a" 2>"\$LOG")"; then
            if [[ "\$out" != "\$a" ]]; then
                notify "Opening converted copy" "\$(basename "\$a") was saved by a newer Painter.
Opening \$(basename "\$out") instead."
            fi
            a="\$out"
        else
            notify -u critical "Could not convert \$(basename "\$a")" "\$(tail -n 3 "\$LOG")"
            exit 1
        fi
    fi
    args+=("\$a")
done

# ---- desktop fixes (settings: $CONF) ----
[ -f "$CONF" ] && . "$CONF"

# Painter runs through XWayland, which ignores the compositor's output scale, so
# its UI comes out tiny on a scaled display. Scale it up in Qt instead.
scale="\${PAINTER_UI_SCALE:-auto}"
if [[ "\$scale" == auto ]]; then
    scale=""
    if [[ -z "\${QT_SCALE_FACTOR:-}" && -n "\${WAYLAND_DISPLAY:-}" ]] && command -v niri >/dev/null; then
        scale="\$(niri msg outputs 2>/dev/null | sed -n 's/^ *Scale: *//p' | sort -g | tail -n 1)"
    fi
fi
[[ "\$scale" =~ ^[0-9]+(\.[0-9]+)?\$ ]] && export QT_SCALE_FACTOR="\$scale"

# Keyboard-navigation plugin (painter/startup/keyboard_nav.py).
export SUBSTANCE_PAINTER_PLUGINS_PATH="$HERE/painter\${SUBSTANCE_PAINTER_PLUGINS_PATH:+:\$SUBSTANCE_PAINTER_PLUGINS_PATH}"

# Eyedropper: replace Painter's (black) X11 screen grab with a real screenshot,
# and keep its per-screen pick overlays on their own monitors.
if [[ -n "\${WAYLAND_DISPLAY:-}" && -f "$SHIM" && "\${PAINTER_EYEDROPPER_FIX:-1}" != 0 ]]; then
    export PAINTER_SCREENGRAB_HELPER="$HERE/painter/painter-screengrab"
    export PAINTER_KWIN_SCREENGRAB="$KWIN_GRAB"
    export LD_PRELOAD="$SHIM\${LD_PRELOAD:+:\$LD_PRELOAD}"
fi

cd "\$PAINTER_DIR" || exit 1
exec "./Adobe Substance 3D Painter" "\${args[@]}"
EOF
chmod +x "$BIN/substance-painter"

echo "* icon"
cp -f "$PAINTER_DIR/resources/python-doc/_static/pt_appicon_256.png" "$DATA/icons/hicolor/256x256/apps/$APP_ID.png"
cp -f "$PAINTER_DIR/resources/python-doc/_static/pt_appicon_256.png" \
      "$DATA/icons/hicolor/256x256/mimetypes/application-x-substance-painter-project.png"

echo "* $DATA/applications/$APP_ID.desktop"
cat > "$DATA/applications/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Substance 3D Painter
GenericName=3D Texture Painting
Comment=Adobe Substance 3D Painter $PAINTER_VERSION (newer .spp files are converted automatically)
Exec="$BIN/substance-painter" %f
Icon=$APP_ID
Terminal=false
Categories=Graphics;3DGraphics;
Keywords=Adobe;Substance;Painter;texturing;PBR;
MimeType=$MIME;
StartupNotify=true
StartupWMClass=Adobe Substance 3D Painter
EOF

echo "* .spp MIME type"
cat > "$DATA/mime/packages/substance-painter.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="$MIME">
    <comment>Substance 3D Painter project</comment>
    <sub-class-of type="application/x-hdf5"/>
    <icon name="application-x-substance-painter-project"/>
    <glob pattern="*.spp" weight="80"/>
  </mime-type>
</mime-info>
EOF

update-mime-database "$DATA/mime" >/dev/null
update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true
gtk-update-icon-cache -q -t -f "$DATA/icons/hicolor" 2>/dev/null || true
xdg-mime default "$APP_ID.desktop" "$MIME"
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
echo "Done."
