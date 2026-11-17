Substance Version Tool {version} makes a Substance 3D Painter project open in an older Painter release (9.0 – 12.2). It writes a converted copy; the original is never changed.

### Windows (x64)

* **Installer:** `Substance_Version_Tool-{version}-windows-x64-setup.exe`. Installs for your user without admin rights and can add *Convert with Substance Version Tool* to the right-click menu of `.spp` files.
* **Portable:** `Substance_Version_Tool-{version}-windows-x64-portable.zip`. Unzip anywhere and run `Substance Version Tool.exe`.

<!-- unsigned -->
These builds are not code-signed yet, so Windows SmartScreen may say "Windows protected your PC": choose *More info* → *Run anyway*.
<!-- /unsigned -->

### Linux (x86_64)

`Substance_Version_Tool-{version}-x86_64.AppImage`: make it executable and run it.

```sh
chmod +x Substance_Version_Tool-{version}-x86_64.AppImage
./Substance_Version_Tool-{version}-x86_64.AppImage
```

`--open FILE.spp` opens a project in the installed Painter and converts it first when a newer release saved it; the AppImage's `.desktop` entry runs it for `.spp` files.

Needs glibc 2.34 or newer (Ubuntu 22.04, Debian 12, Fedora 35, RHEL 9 and later). Without FUSE, add `--appimage-extract-and-run`.

### Checking a download

Each file has a `.sha256` next to it: `sha256sum -c <file>.sha256` (Linux) or `Get-FileHash <file>` (PowerShell) and compare.

### Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by [SignPath Foundation](https://signpath.org). See the [code signing policy](https://github.com/ShadaxTheWizard/substance-version-tool#code-signing-policy) and the [privacy policy](https://github.com/ShadaxTheWizard/substance-version-tool/blob/main/PRIVACY.md).
<!-- unsigned -->
The application to the SignPath Foundation is pending: the Windows files of this release are not signed yet.
<!-- /unsigned -->

Substance 3D Painter is a product of Adobe. Substance Version Tool is an independent tool and is not affiliated with or endorsed by Adobe.
