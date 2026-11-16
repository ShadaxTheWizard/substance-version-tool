#!/usr/bin/env bash
# Build the Substance Version Tool app for Linux as an AppImage in dist/, plus its .sha256. Run by CI
# (.github/workflows/build.yml) and usable by hand:
#
#   packaging/build-linux.sh
#
# Needs curl, sha256sum and mksquashfs (squashfs-tools). The app's Python is a relocatable
# python-build-standalone build fetched by uv, not the build
# machine's, so the result does not depend on the build machine's glibc beyond what Qt needs
# (2.34). uv is used from PATH when present, otherwise downloaded (pinned,
# checksum-verified) into build/linux/tools, as is the AppImage runtime.
#
# BUILD_PYTHON=/usr/bin/python3 skips uv and builds with that interpreter instead (pip,
# same hashed requirements). The app then needs that system's glibc or newer.
#
# The AppImage is put together without appimagetool (which only has a moving "continuous"
# release): an AppImage is the runtime executable followed by a squashfs of the AppDir.
set -euo pipefail

UV_VERSION=0.13.7
# sha256 of uv-x86_64-unknown-linux-gnu.tar.gz, from the release's published checksums
UV_SHA256=c5221ff41b7892bfc223543883600125f6a47fe845093e0f5bdff4f359196d83
PYTHON_VERSION=3.13
# github.com/AppImage/type2-runtime: static, so the AppImage does not need libfuse2 on the host
RUNTIME_VERSION=20251108
RUNTIME_SHA256=2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d

root=$(cd "$(dirname "$0")/.." && pwd)
build=$root/build/linux
dist=$root/dist
tools=$build/tools
cd "$root"

version=$(sed -n "s/^__version__ = '\(.*\)'$/\1/p" spp_convert.py)
[ -n "$version" ] || { echo "could not read __version__ from spp_convert.py" >&2; exit 1; }

# On a tag build, the tag has to match the code's version.
if [ "${GITHUB_REF_TYPE:-}" = tag ] && [ "${GITHUB_REF_NAME:-}" != "v$version" ]; then
    echo "tag ${GITHUB_REF_NAME} does not match __version__ $version (expected v$version)" >&2
    exit 1
fi
[ "$(uname -m)" = x86_64 ] || { echo "only x86_64 builds are set up (got $(uname -m))" >&2; exit 1; }
command -v mksquashfs >/dev/null || { echo "mksquashfs not found (install squashfs-tools)" >&2; exit 1; }

rm -rf "$build/venv" "$build/work" "$build/out" "$build/AppDir" "$build/icon"
mkdir -p "$tools" "$build/icon"

fetch_verified() {  # url sha256 destination
    local tmp
    tmp=$(mktemp)
    curl -fsSL "$1" -o "$tmp"
    if ! echo "$2  $tmp" | sha256sum -c --quiet - >/dev/null; then
        echo "checksum mismatch for $1: got $(sha256sum "$tmp" | cut -d' ' -f1)" >&2
        rm -f "$tmp"
        exit 1
    fi
    mv "$tmp" "$3"
}

if [ -n "${BUILD_PYTHON:-}" ]; then
    echo "-- Python environment from $BUILD_PYTHON"
    "$BUILD_PYTHON" -m venv "$build/venv"
    "$build/venv/bin/pip" install -q --disable-pip-version-check --require-hashes \
        --only-binary=:all: -r packaging/requirements.txt
else
    export PATH="$tools:$PATH"
    if ! command -v uv >/dev/null; then
        echo "-- fetching uv $UV_VERSION"
        fetch_verified "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-unknown-linux-gnu.tar.gz" \
            "$UV_SHA256" "$tools/uv.tar.gz"
        tar -xzf "$tools/uv.tar.gz" -C "$tools" --strip-components=1
        rm -f "$tools/uv.tar.gz"
    fi
    echo "-- Python $PYTHON_VERSION environment"
    export UV_PYTHON_INSTALL_DIR=$build/pythons
    uv python install --managed-python "$PYTHON_VERSION"
    uv venv --quiet --managed-python --python "$PYTHON_VERSION" "$build/venv"
    uv pip install --quiet --python "$build/venv/bin/python" --require-hashes \
        --only-binary :all: -r packaging/requirements.txt
fi
py=$build/venv/bin/python

runtime=$tools/runtime-$RUNTIME_VERSION-x86_64
if [ ! -f "$runtime" ]; then
    echo "-- fetching AppImage runtime $RUNTIME_VERSION"
    fetch_verified "https://github.com/AppImage/type2-runtime/releases/download/$RUNTIME_VERSION/runtime-x86_64" \
        "$RUNTIME_SHA256" "$runtime"
fi

echo "-- icon"
QT_QPA_PLATFORM=offscreen "$py" -I packaging/icons.py png "$build/icon/substance-version-tool.png"

echo "-- bundling Substance Version Tool $version"
# A folder (not --onefile): inside an AppImage it is already one file, and this way the app
# does not unpack itself to /tmp on every start. Only QtCore/QtGui/QtWidgets are imported,
# so PyInstaller's Qt hook leaves the rest of PySide6 out.
"$py" -m PyInstaller --noconfirm --clean --log-level WARN \
    --onedir --name substance-version-tool \
    --distpath "$build/out" --workpath "$build/work" --specpath "$build" \
    --add-data "$build/icon/substance-version-tool.png:." \
    --exclude-module tkinter \
    substance_version_tool.py

echo "-- AppDir"
appdir=$build/AppDir
mkdir -p "$appdir/usr/lib" "$appdir/usr/share/applications" \
         "$appdir/usr/share/icons/hicolor/256x256/apps" "$appdir/usr/share/icons/hicolor/scalable/apps"
cp -a "$build/out/substance-version-tool" "$appdir/usr/lib/"
cp packaging/substance-version-tool.desktop "$appdir/"
cp packaging/substance-version-tool.desktop "$appdir/usr/share/applications/"
cp "$build/icon/substance-version-tool.png" "$appdir/"
cp "$build/icon/substance-version-tool.png" "$appdir/usr/share/icons/hicolor/256x256/apps/"
cp packaging/substance-version-tool.svg "$appdir/usr/share/icons/hicolor/scalable/apps/"
ln -s substance-version-tool.png "$appdir/.DirIcon"
cat > "$appdir/AppRun" <<'EOF'
#!/bin/sh
HERE=$(dirname "$(readlink -f "$0")")
exec "$HERE/usr/lib/substance-version-tool/substance-version-tool" "$@"
EOF
chmod 755 "$appdir/AppRun"

echo "-- AppImage"
name=Substance_Version_Tool-$version-x86_64.AppImage
rm -rf "$dist"
mkdir -p "$dist"
mksquashfs "$appdir" "$build/AppDir.squashfs" -root-owned -noappend -comp zstd -quiet -no-progress
cat "$runtime" "$build/AppDir.squashfs" > "$dist/$name"
chmod 755 "$dist/$name"
rm -f "$build/AppDir.squashfs"

echo "-- smoke test"
# Extract-and-run: containers have no FUSE. This runs the AppImage as shipped (runtime,
# squashfs, AppRun) and, offscreen, loads Qt's platform plugin, h5py's HDF5, numpy and mmh3
# from it and checks a written file's checksum.
APPIMAGE_EXTRACT_AND_RUN=1 "$dist/$name" --version
APPIMAGE_EXTRACT_AND_RUN=1 QT_QPA_PLATFORM=offscreen "$dist/$name" --self-test

(cd "$dist" && sha256sum "$name" > "$name.sha256")
ls -lh "$dist"
