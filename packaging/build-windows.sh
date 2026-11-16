#!/usr/bin/env bash
# Build Substance Version Tool for Windows (x64) into dist/: an installer and a portable .zip, each
# with a .sha256. Run by CI (.github/workflows/build.yml, GitHub's Windows runners) and
# usable by hand in Git Bash (Git for Windows):
#
#   packaging/build-windows.sh              # everything below, in order
#   packaging/build-windows.sh bundle       # the app folder (PyInstaller) + self-test
#   packaging/build-windows.sh selftest     # run the self-test again (e.g. after signing)
#   packaging/build-windows.sh package      # installer + portable zip from that folder
#   packaging/build-windows.sh checksums    # (re)write the .sha256 files in dist/
#
# CI runs the stages one at a time so that SignPath can sign the app between "bundle" and
# "package", and the installer after "package" (see the workflow).
#
# Needs PowerShell (part of Windows) and Inno Setup 6 (ISCC.exe on PATH or in its default
# folder; `choco install innosetup` or https://jrsoftware.org). The app's Python is a
# python-build-standalone build fetched by uv, which is used from PATH when present,
# otherwise downloaded (pinned, checksum-verified) into build/windows/tools.
#
set -euo pipefail
# Pass arguments to Windows programs exactly as written: Git Bash would otherwise rewrite
# anything that looks like a path, including ISCC's /D options.
export MSYS2_ARG_CONV_EXCL='*'

UV_VERSION=0.13.7
# sha256 of uv-x86_64-pc-windows-msvc.zip, from the release's published checksums
UV_SHA256=0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021
PYTHON_VERSION=3.13

# C:/like/this paths work for bash and for Windows programs alike.
root=$(cygpath -m "$(cd "$(dirname "$0")/.." && pwd)")
build=$root/build/windows
dist=$root/dist
tools=$build/tools
cd "$root"

version=$(sed -n "s/^__version__ = '\(.*\)'$/\1/p" spp_convert.py)
[ -n "$version" ] || { echo "could not read __version__ from spp_convert.py" >&2; exit 1; }
if [ "${GITHUB_REF_TYPE:-}" = tag ] && [ "${GITHUB_REF_NAME:-}" != "v$version" ]; then
    echo "tag ${GITHUB_REF_NAME} does not match __version__ $version (expected v$version)" >&2
    exit 1
fi
app="$build/out/Substance Version Tool"
stage=${1:-all}

selftest() {
    echo "-- self-test"
    # The app has no console, so the result goes to a log file (see report_self_test()).
    rm -f "$build/selftest.log"
    local rc=0
    SPP_SELFTEST_LOG="$build/selftest.log" QT_QPA_PLATFORM=offscreen \
        timeout 300 "$app/Substance Version Tool.exe" --self-test || rc=$?
    cat "$build/selftest.log" 2>/dev/null || echo "(no self-test log written)"
    [ "$rc" = 0 ] || { echo "self-test failed (exit code $rc)" >&2; exit 1; }
}

checksums() {
    (cd "$dist" && rm -f ./*.sha256 && for f in *.exe *.zip; do sha256sum "$f" > "$f.sha256"; done)
    ls -l "$dist"
}

package() {
    local iscc
    iscc=$(command -v iscc || command -v ISCC || true)
    for d in "${PROGRAMFILES:-}" "${PROGRAMFILES:-} (x86)" "C:/Program Files (x86)" "${LOCALAPPDATA:-}/Programs"; do
        [ -z "$iscc" ] && [ -f "$d/Inno Setup 6/ISCC.exe" ] && iscc="$d/Inno Setup 6/ISCC.exe"
    done
    [ -n "$iscc" ] || { echo "Inno Setup 6 (ISCC.exe) not found; install it (choco install innosetup)" >&2; exit 1; }
    [ -f "$app/Substance Version Tool.exe" ] || { echo "no app in $app; run the bundle stage first" >&2; exit 1; }

    echo "-- installer"
    rm -rf "$dist"
    mkdir -p "$dist"
    "$iscc" /Qp "/DAppVersion=$version" "/DSourceDir=$app" "/DOutputDir=$dist" \
        "/DIconFile=$build/icon/substance-version-tool.ico" packaging/windows/substance-version-tool.iss

    echo "-- portable zip"
    local portable=Substance_Version_Tool-$version-windows-x64-portable.zip
    powershell -NoProfile -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::CreateFromDirectory('$app', '$dist/$portable', 'Optimal', \$true)"
    checksums
}

case $stage in
    all|bundle) ;;
    selftest) selftest; exit 0 ;;
    package) package; exit 0 ;;
    checksums) checksums; exit 0 ;;
    *) echo "unknown stage: $stage (bundle, selftest, package, checksums)" >&2; exit 1 ;;
esac

rm -rf "$build/venv" "$build/work" "$build/out" "$build/icon"
mkdir -p "$tools" "$build/icon"

export PATH="$(cygpath -u "$tools"):$PATH"
if ! command -v uv >/dev/null; then
    echo "-- fetching uv $UV_VERSION"
    curl -fsSL -o "$tools/uv.zip" \
        "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-x86_64-pc-windows-msvc.zip"
    if ! echo "$UV_SHA256  $tools/uv.zip" | sha256sum -c --quiet - >/dev/null; then
        echo "checksum mismatch for uv $UV_VERSION: got $(sha256sum "$tools/uv.zip" | cut -d' ' -f1)" >&2
        exit 1
    fi
    powershell -NoProfile -Command "Expand-Archive -Force -Path '$tools/uv.zip' -DestinationPath '$tools'"
    rm -f "$tools/uv.zip"
fi

echo "-- Python $PYTHON_VERSION environment"
export UV_PYTHON_INSTALL_DIR=$build/pythons
uv python install --managed-python "$PYTHON_VERSION"
uv venv --quiet --managed-python --python "$PYTHON_VERSION" "$build/venv"
py=$build/venv/Scripts/python.exe
uv pip install --quiet --python "$py" --require-hashes --only-binary :all: -r packaging/requirements.txt

echo "-- icons"
QT_QPA_PLATFORM=offscreen "$py" -I packaging/icons.py ico "$build/icon/substance-version-tool.ico"
QT_QPA_PLATFORM=offscreen "$py" -I packaging/icons.py png "$build/icon/substance-version-tool.png"

echo "-- version resource"
# Explorer's file properties, Task Manager and the installer read these.
IFS=. read -r v1 v2 v3 _ <<< "${version//[^0-9.]/}.0.0.0"
cat > "$build/version_info.txt" <<EOF
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($v1, $v2, $v3, 0), prodvers=($v1, $v2, $v3, 0), mask=0x3f,
                    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Shadax'),
      StringStruct('FileDescription', 'Substance Version Tool'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'Substance Version Tool'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Shadax. MIT License.'),
      StringStruct('OriginalFilename', 'Substance Version Tool.exe'),
      StringStruct('ProductName', 'Substance Version Tool'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
EOF

echo "-- bundling Substance Version Tool $version"
# --windowed: no console window. --onedir: the installer ships the folder, which also
# starts faster and trips antivirus heuristics less than a self-extracting --onefile.
"$py" -m PyInstaller --noconfirm --clean --log-level WARN \
    --onedir --windowed --name "Substance Version Tool" \
    --icon "$build/icon/substance-version-tool.ico" --version-file "$build/version_info.txt" \
    --distpath "$build/out" --workpath "$build/work" --specpath "$build" \
    --add-data "$build/icon/substance-version-tool.png;." \
    --exclude-module tkinter \
    substance_version_tool.py
selftest
[ "$stage" = all ] && package
exit 0
