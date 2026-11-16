Substance Version Tool {version} makes a Substance 3D Painter project open in an older Painter release (9.0 – 12.2). It writes a converted copy; the original is never changed.

### Windows (x64)

* **Installer:** `Substance_Version_Tool-{version}-windows-x64-setup.exe`. Installs for your user without admin rights and can add *Convert with Substance Version Tool* to the right-click menu of `.spp` files.
* **Portable:** `Substance_Version_Tool-{version}-windows-x64-portable.zip`. Unzip anywhere and run `Substance Version Tool.exe`.

These builds are not code-signed yet, so Windows SmartScreen may say "Windows protected your PC": choose *More info* → *Run anyway*.

### Linux (x86_64)

`Substance_Version_Tool-{version}-x86_64.AppImage`: make it executable and run it.

```sh
chmod +x Substance_Version_Tool-{version}-x86_64.AppImage
./Substance_Version_Tool-{version}-x86_64.AppImage
```

Needs glibc 2.34 or newer (Ubuntu 22.04, Debian 12, Fedora 35, RHEL 9 and later). Without FUSE, add `--appimage-extract-and-run`.

### Checking a download

Each file has a `.sha256` next to it: `sha256sum -c <file>.sha256` (Linux) or `Get-FileHash <file>` (PowerShell) and compare.

Substance 3D Painter is a product of Adobe. Substance Version Tool is an independent tool and is not affiliated with or endorsed by Adobe.
