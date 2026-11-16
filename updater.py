"""Finding a newer release on GitHub and installing it.  Standard library only; the app
runs these functions in background threads.

* The newest version comes from the redirect of github.com/<repo>/releases/latest (not the
  REST API, which allows only 60 unauthenticated requests per hour per IP address: a
  classroom behind one address would run out).
* What can be updated in place depends on how the app was installed (install_kind()):
  the Windows installer build re-runs the new installer silently; the Linux AppImage
  replaces its own file.  The portable zip and a source checkout only get a link.
* Every download is checked against the .sha256 file published next to it.  That catches
  a broken download, not a compromised release (both come from the same place).  So on
  Windows, when the running app is code-signed, a new installer only runs if it carries a
  valid signature from the same publisher.  (Unsigned builds have nothing to compare with.)
"""
import hashlib
import os
import re
import subprocess
import sys
import urllib.request

REPO = 'ShadaxTheWizard/substance-version-tool'
RELEASES = f'https://github.com/{REPO}/releases'
TIMEOUT = 15


class UpdateError(Exception):
    pass


def version_tuple(v):
    """'v0.3.0' -> (0, 3, 0); suffixes such as 'rc1' are ignored."""
    nums = [int(x) for x in re.findall(r'\d+', str(v))[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def _request(url, method='GET'):
    from spp_convert import __version__
    req = urllib.request.Request(url, method=method)
    req.add_header('User-Agent', f'substance-version-tool/{__version__}')
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def latest_version():
    """The newest published release, e.g. '0.3.0'."""
    try:
        with _request(f'{RELEASES}/latest', method='HEAD') as r:
            final = r.geturl()
    except OSError as e:
        raise UpdateError(f'could not reach GitHub ({e})') from e
    m = re.search(r'/releases/tag/v?([0-9][^/?#]*)$', final)
    if not m:
        raise UpdateError(f'unexpected answer from GitHub ({final})')
    return m.group(1)


def release_page(version):
    return f'{RELEASES}/tag/v{version}'


def install_kind():
    """'installer' (Windows, installed with the setup program), 'appimage' (Linux, the
    AppImage file can be replaced), 'portable' (anything frozen that cannot update
    itself) or 'source' (running from a checkout)."""
    if not getattr(sys, 'frozen', False):
        return 'source'
    if sys.platform == 'win32':
        here = os.path.dirname(sys.executable)
        return 'installer' if os.path.exists(os.path.join(here, 'unins000.exe')) else 'portable'
    appimage = os.environ.get('APPIMAGE')
    if appimage and os.path.isfile(appimage) and os.access(os.path.dirname(appimage), os.W_OK):
        return 'appimage'
    return 'portable'


def asset_name(version, kind):
    if kind == 'installer':
        return f'Substance_Version_Tool-{version}-windows-x64-setup.exe'
    if kind == 'appimage':
        return f'Substance_Version_Tool-{version}-x86_64.AppImage'
    raise UpdateError(f'a {kind} build cannot update itself')


def download(version, kind, progress=None, stop=lambda: False):
    """Download the build for `kind` and check its .sha256; returns the file's path.
    progress(done_bytes, total_bytes) is called as it goes."""
    name = asset_name(version, kind)
    url = f'{RELEASES}/download/v{version}/{name}'
    try:
        with _request(url + '.sha256') as r:
            expected = r.read(4096).decode('ascii', 'replace').split()[0].lower()
    except (OSError, IndexError) as e:
        raise UpdateError(f'could not get the checksum of {name} ({e})') from e
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise UpdateError(f'{name}.sha256 is not a SHA-256 checksum')

    if kind == 'appimage':
        # Next to the AppImage it replaces, so the final rename stays on one file system.
        folder = os.path.dirname(os.environ['APPIMAGE'])
    else:
        import glob
        import shutil
        import tempfile
        # Installers from earlier updates are no longer needed.
        for old in glob.glob(os.path.join(tempfile.gettempdir(), 'svt-update-*')):
            shutil.rmtree(old, ignore_errors=True)
        folder = tempfile.mkdtemp(prefix='svt-update-')
    dst = os.path.join(folder, name)
    part = os.path.join(folder, f'.{name}.part')
    digest = hashlib.sha256()
    try:
        with _request(url) as r, open(part, 'wb') as f:
            total = int(r.headers.get('Content-Length') or 0)
            done = 0
            while True:
                if stop():
                    raise UpdateError('cancelled')
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        if digest.hexdigest() != expected:
            raise UpdateError(f'{name} does not match its published checksum; nothing was changed')
        os.replace(part, dst)
    except BaseException as e:
        if os.path.exists(part):
            os.unlink(part)
        if isinstance(e, OSError):
            raise UpdateError(f'download of {name} failed ({e})') from e
        raise
    return dst


def _reset_dll_directory():
    """A frozen app sets its own DLL directory, which child processes would inherit."""
    try:
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    except (ImportError, AttributeError, OSError):
        pass


def authenticode(path):
    """(status, signer subject) of a Windows file's code signature, e.g. ('Valid', 'CN=...'),
    or ('NotSigned', ''); (None, None) if it could not be read."""
    script = ('$s = Get-AuthenticodeSignature -LiteralPath $env:SVT_SIGNATURE_OF; '
              "[Console]::Out.Write([string]$s.Status + '|' + [string]$s.SignerCertificate.Subject)")
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                           capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, SVT_SIGNATURE_OF=path),
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        return None, None
    status, sep, subject = r.stdout.strip().partition('|')
    return (status, subject) if sep else (None, None)


def check_publisher(path):
    """Refuse an installer that is not signed like the running app (when the app is signed)."""
    own_status, own_subject = authenticode(sys.executable)
    if own_status != 'Valid':
        return
    status, subject = authenticode(path)
    if status != 'Valid' or subject != own_subject:
        raise UpdateError('the downloaded installer is not signed by the same publisher as this app '
                          f'({status or "unreadable"}); nothing was changed')


def apply(path, kind, env=None):
    """Start the update; the caller then quits so the files can be replaced."""
    if kind == 'installer':
        _reset_dll_directory()
        check_publisher(path)
        here = os.path.dirname(os.path.abspath(sys.executable))
        program_dirs = [os.environ.get(v) for v in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)')]
        all_users = any(d and os.path.normcase(here).startswith(os.path.normcase(d)) for d in program_dirs)
        # /SILENT shows only a progress window. /RELAUNCH is ours: the installer starts the
        # app again when it is done (see packaging/windows/substance-version-tool.iss).
        args = [path, '/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/RELAUNCH',
                '/ALLUSERS' if all_users else '/CURRENTUSER']
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(args, creationflags=flags, close_fds=True, env=env)
    elif kind == 'appimage':
        target = os.environ['APPIMAGE']
        os.chmod(path, 0o755)
        os.replace(path, target)
        subprocess.Popen([target], env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        raise UpdateError(f'a {kind} build cannot update itself')
