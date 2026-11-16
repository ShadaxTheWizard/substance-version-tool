# Privacy policy

Substance Version Tool works only on the project files on your computer. This program will
not transfer any information to other networked systems unless specifically requested by the
user or the person installing or operating it.

## The update check (optional)

The only network access is the update check, and only when you have chosen it:

* in the Windows installer, with the option *Check GitHub for a new version when the app
  starts* (shown together with this policy during installation), or
* when the app asks on its first start (portable and Linux versions), or
* at any time with *Help → Check for Updates…*.

You can turn it off at any time with *Help → Check for Updates at Start*.

What happens: the app asks `github.com` which release is the newest. If you then choose to
update, it downloads that release's file and its checksum from `github.com`. The requests carry
nothing but the app's version (in the User-Agent header). No account, no identifier and no
information about your files is sent, and nothing is sent to the author of this program.

These requests go to GitHub, whose privacy statement applies to them:
<https://docs.github.com/site-policy/privacy-policies/github-general-privacy-statement>

## Stored on your computer

The app remembers its settings (the chosen Painter version, the output folder, the
update-check choice) locally: on Windows in the registry under
`HKEY_CURRENT_USER\Software\substance-version-tool`, on Linux in
`~/.config/substance-version-tool/`. Uninstalling on Windows removes them.

## Contact

Questions about this policy: open an issue at
<https://github.com/ShadaxTheWizard/substance-version-tool/issues>.
