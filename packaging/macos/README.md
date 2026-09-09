# macOS app packaging

`build.sh` turns the Python package into a standalone `Taprivo.app` bundle, signs
and notarizes it with a Developer ID, and wraps it in a DMG. Everything lands in
`.build/macos/`, which is git-ignored.

Apple Silicon only. Run the commands from the repository root.

## Prerequisites

- **Xcode Command Line Tools** — `xcode-select --install`. Provides `codesign`,
  `spctl`, `ditto`, `hdiutil` and `xcrun notarytool`.
- **[uv](https://docs.astral.sh/uv/)** — used to create the isolated build
  virtualenv (`.build/macos/.venv`, Python 3.12) and install PyInstaller.
- **A "Developer ID Application" certificate** in the login keychain, for
  signing. List what you have with:

  ```bash
  security find-identity -v -p codesigning
  ```

  Export the full identity string before signing:

  ```bash
  export TAPRIVO_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
  ```

- **A notarytool keychain profile**, created once. With an App Store Connect API
  key:

  ```bash
  xcrun notarytool store-credentials taprivo-notary \
      --key /path/to/AuthKey_XXXXXXXX.p8 --key-id XXXXXXXX \
      --issuer 00000000-0000-0000-0000-000000000000
  ```

  Or with an Apple ID and an app-specific password:

  ```bash
  xcrun notarytool store-credentials taprivo-notary \
      --apple-id you@example.com --team-id TEAMID --password abcd-efgh-ijkl-mnop
  ```

  Then:

  ```bash
  export TAPRIVO_NOTARY_PROFILE=taprivo-notary
  ```

## Commands

```bash
packaging/macos/build.sh build      # PyInstaller bundle -> .build/macos/dist/Taprivo.app
packaging/macos/build.sh sign       # inside-out codesign + verify (needs TAPRIVO_SIGN_IDENTITY)
packaging/macos/build.sh notarize   # submit, wait, staple (needs TAPRIVO_NOTARY_PROFILE)
packaging/macos/build.sh dmg        # .build/macos/Taprivo-<version>.dmg + SHA256SUMS
packaging/macos/build.sh all        # build -> sign -> notarize -> dmg
```

`all` skips signing and notarization when the two environment variables are not
set, prints a loud warning and produces an unsigned DMG.

`build` reuses `.build/macos/.venv` if it already exists; delete `.build/` for a
guaranteed-clean rebuild.

## Expected output

| Artifact | Size |
| --- | --- |
| `.build/macos/dist/Taprivo.app` | ≈ 359 MB |
| `.build/macos/Taprivo-<version>.dmg` | ≈ 150 MB |

Most of the weight is OpenCV (≈ 136 MB), mediapipe (≈ 85 MB) and Qt (≈ 70 MB).
The spec drops the Qt frameworks nothing in Taprivo loads — the QtQml/QtQuick
stack, QtPdf, QtVirtualKeyboard and QtWebEngine — which saves about 19 MB over a
default PySide6 collection.

A full `build` takes roughly a minute on an M-series machine. The first launch of
a freshly built bundle takes about 4 s while the file cache warms up; after that
`Taprivo --version` returns in about 1 s and the HUD plus the MCP endpoint are up
in about 2 s.

Smoke-test a bundle without installing it:

```bash
.build/macos/dist/Taprivo.app/Contents/MacOS/Taprivo --version
.build/macos/dist/Taprivo.app/Contents/MacOS/Taprivo doctor --json
```

## Icon

`icon.svg` is the only source of the app icon. `Taprivo.icns` (used by the
bundle) and `src/taprivo/resources/icon.png` (used by the running app's windows
and the Dock) are both committed and both generated from it:

```bash
uv run python packaging/macos/make_icon.py
```

The script renders every `.iconset` size with Qt, folds them up with `iconutil`
and removes the temporary iconset. Regenerate and commit both files after
editing the SVG.

## Camera permission

The bundle asks for camera access under its own bundle identifier,
`com.sinanyorulmaz.taprivo`, not under the terminal that launched it. macOS
remembers the grant per identifier *and* per signing identity, so re-signing the
app with a different identity resets it — the next launch prompts again. To clear
the grant by hand:

```bash
tccutil reset Camera com.sinanyorulmaz.taprivo
```

The prompt text comes from `NSCameraUsageDescription` in the spec, and
`com.apple.security.device.camera` in `entitlements.plist` keeps the hardened
runtime from blocking capture.

## Known limitations

- **Apple Silicon only.** mediapipe and OpenCV publish arm64 macOS wheels only,
  so there is no x86_64 or universal2 build. `build.sh` refuses to run on Intel.
- **Unsigned builds are blocked by Gatekeeper.** A bundle built without
  `TAPRIVO_SIGN_IDENTITY` carries only PyInstaller's ad-hoc signature. Open it
  with right-click › Open (once) instead of a double-click, or macOS will report
  it as damaged.
- The hardened runtime needs
  `com.apple.security.cs.allow-unsigned-executable-memory` and
  `com.apple.security.cs.disable-library-validation`: CPython's frozen bundle
  loads unsigned extension modules that library validation would otherwise
  reject.

### Notarization credentials

Either store a keychain profile once (`xcrun notarytool store-credentials taprivo-notary --key AuthKey.p8 --key-id <ID> --issuer <UUID>`) and export `TAPRIVO_NOTARY_PROFILE=taprivo-notary`, or point the script straight at the App Store Connect API key: `TAPRIVO_NOTARY_KEY=~/.private_keys/AuthKey_<ID>.p8`, `TAPRIVO_NOTARY_KEY_ID=<ID>`, `TAPRIVO_NOTARY_ISSUER=<UUID>`. The key-file form is the one to use from CI or from a shell that cannot read the login keychain.
