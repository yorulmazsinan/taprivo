#!/usr/bin/env bash
#
# Build, sign, notarize and package Taprivo as a macOS .app / .dmg (arm64 only).
#
# Usage:
#   packaging/macos/build.sh build      # PyInstaller bundle in .build/macos/dist
#   packaging/macos/build.sh sign       # needs TAPRIVO_SIGN_IDENTITY
#   packaging/macos/build.sh notarize   # needs TAPRIVO_NOTARY_PROFILE
#   packaging/macos/build.sh dmg        # .build/macos/Taprivo-<version>.dmg
#   packaging/macos/build.sh all        # build [+ sign + notarize] + dmg
#
# See packaging/macos/README.md for the prerequisites.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

PYINSTALLER_VERSION="6.22.2"
PYTHON_VERSION="3.12"
BUILD_DIR=".build/macos"
VENV_DIR="$BUILD_DIR/.venv"
DIST_DIR="$BUILD_DIR/dist"
WORK_DIR="$BUILD_DIR/work"
STAGE_DIR="$BUILD_DIR/dmg"
APP="$DIST_DIR/Taprivo.app"
SPEC="packaging/macos/taprivo.spec"
ENTITLEMENTS="packaging/macos/entitlements.plist"

log() { printf '==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "$1 is required but not on PATH. $2"
}

require_arm64() {
    [ "$(uname -m)" = "arm64" ] || die "Taprivo packages Apple Silicon only (arm64); this machine is $(uname -m)."
}

require_app() {
    [ -d "$APP" ] || die "$APP not found. Run '$0 build' first."
}

# The version the built bundle carries, so a DMG can never be misnamed; falls
# back to pyproject.toml when the bundle is not there yet.
app_version() {
    local version=""
    if [ -f "$APP/Contents/Info.plist" ]; then
        version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' \
            "$APP/Contents/Info.plist" 2>/dev/null || true)"
    fi
    if [ -z "$version" ]; then
        version="$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)"
    fi
    printf '%s\n' "$version"
}

cmd_build() {
    require_arm64
    require_cmd uv "Install it from https://docs.astral.sh/uv/."

    mkdir -p "$BUILD_DIR"
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        log "Creating the build virtualenv ($VENV_DIR, Python $PYTHON_VERSION)"
        uv venv --python "$PYTHON_VERSION" "$VENV_DIR"
    fi

    log "Installing taprivo and pyinstaller==$PYINSTALLER_VERSION into the build virtualenv"
    uv pip install --python "$VENV_DIR/bin/python" . "pyinstaller==$PYINSTALLER_VERSION"

    log "Running PyInstaller"
    "$VENV_DIR/bin/pyinstaller" --clean --noconfirm "$SPEC" \
        --distpath "$DIST_DIR" --workpath "$WORK_DIR"

    require_app
    prune_broken_symlinks
    log "Built $APP ($(du -sh "$APP" | cut -f1))"
}

# Signs one Mach-O file (executable, .so, .dylib or framework binary).
sign_file() {
    codesign --force --options runtime --timestamp \
        --sign "$TAPRIVO_SIGN_IDENTITY" "$1"
}

prune_broken_symlinks() {
    # Frameworks trimmed by the spec leave dangling symlinks under Contents/Resources;
    # codesign --deep fails on them with "No such file or directory".
    local count
    count=$(find "$APP" -type l ! -exec test -e {} \; -print | wc -l | tr -d ' ')
    find "$APP" -type l ! -exec test -e {} \; -delete
    log "Pruned $count dangling symlinks"
}

cmd_sign() {
    require_arm64
    [ -n "${TAPRIVO_SIGN_IDENTITY:-}" ] || die \
        "TAPRIVO_SIGN_IDENTITY is not set. Export it first, for example:
  export TAPRIVO_SIGN_IDENTITY=\"Developer ID Application: Your Name (TEAMID)\"
List the identities in your keychain with: security find-identity -v -p codesigning"
    [ -f "$ENTITLEMENTS" ] || die "$ENTITLEMENTS not found."
    require_app

    log "Signing nested Mach-O files with '$TAPRIVO_SIGN_IDENTITY'"
    local count=0
    while IFS= read -r -d '' file; do
        case "$(file -b "$file")" in
            *Mach-O*)
                sign_file "$file"
                count=$((count + 1))
                ;;
        esac
    done < <(find "$APP" -type f -print0)
    log "Signed $count nested Mach-O files"

    log "Signing frameworks"
    while IFS= read -r -d '' framework; do
        sign_file "$framework"
    done < <(find "$APP" -type d -name '*.framework' -print0)

    log "Signing the main executable and the app bundle"
    codesign --force --options runtime --timestamp \
        --entitlements "$ENTITLEMENTS" \
        --sign "$TAPRIVO_SIGN_IDENTITY" "$APP/Contents/MacOS/Taprivo"
    codesign --force --options runtime --timestamp \
        --entitlements "$ENTITLEMENTS" \
        --sign "$TAPRIVO_SIGN_IDENTITY" "$APP"

    log "Verifying the signature"
    codesign --verify --deep --strict --verbose=2 "$APP"
    log "Signature OK (Gatekeeper assessment runs after notarization)"
}

cmd_notarize() {
    require_arm64
    if [ -n "${TAPRIVO_NOTARY_KEY:-}" ]; then
        [ -n "${TAPRIVO_NOTARY_KEY_ID:-}" ] && [ -n "${TAPRIVO_NOTARY_ISSUER:-}" ] || die \
            "TAPRIVO_NOTARY_KEY needs TAPRIVO_NOTARY_KEY_ID and TAPRIVO_NOTARY_ISSUER as well"
        NOTARY_AUTH=(--key "$TAPRIVO_NOTARY_KEY" --key-id "$TAPRIVO_NOTARY_KEY_ID" --issuer "$TAPRIVO_NOTARY_ISSUER")
    elif [ -n "${TAPRIVO_NOTARY_PROFILE:-}" ]; then
        NOTARY_AUTH=("${NOTARY_AUTH[@]}")
    else
        die "Set TAPRIVO_NOTARY_PROFILE (a keychain profile from 'xcrun notarytool store-credentials')
or TAPRIVO_NOTARY_KEY, TAPRIVO_NOTARY_KEY_ID and TAPRIVO_NOTARY_ISSUER (an App Store Connect API key file).
The key-file form also works in shells that cannot read the login keychain."
    fi
    require_cmd xcrun "Install the Xcode Command Line Tools: xcode-select --install"
    require_app

    local version zip
    version="$(app_version)"
    zip="$BUILD_DIR/Taprivo-$version.zip"

    log "Zipping the app for notarization"
    rm -f "$zip"
    ditto -c -k --keepParent "$APP" "$zip"

    log "Submitting to the notary service (this can take a few minutes)"
    xcrun notarytool submit "$zip" "${NOTARY_AUTH[@]}" --wait

    log "Stapling the ticket"
    xcrun stapler staple "$APP"
    xcrun stapler validate "$APP"
    spctl --assess --type execute --verbose=2 "$APP"
    rm -f "$zip"
    log "Notarized and stapled"
}

cmd_dmg() {
    require_arm64
    require_app

    local version dmg
    version="$(app_version)"
    dmg="$BUILD_DIR/Taprivo-$version.dmg"

    log "Staging the disk image contents"
    rm -rf "$STAGE_DIR"
    mkdir -p "$STAGE_DIR"
    ditto "$APP" "$STAGE_DIR/Taprivo.app"
    ln -s /Applications "$STAGE_DIR/Applications"

    log "Creating $dmg"
    rm -f "$dmg"
    hdiutil create -volname "Taprivo $version" -srcfolder "$STAGE_DIR" \
        -fs HFS+ -format UDZO -ov -quiet "$dmg"
    rm -rf "$STAGE_DIR"

    (cd "$BUILD_DIR" && shasum -a 256 "$(basename "$dmg")" > SHA256SUMS)
    log "$(cat "$BUILD_DIR/SHA256SUMS")"
}

cmd_all() {
    cmd_build
    if [ -n "${TAPRIVO_SIGN_IDENTITY:-}" ] && [ -n "${TAPRIVO_NOTARY_PROFILE:-}" ]; then
        cmd_sign
        cmd_notarize
    else
        warn "**********************************************************************"
        warn "TAPRIVO_SIGN_IDENTITY and/or TAPRIVO_NOTARY_PROFILE are not set."
        warn "Producing an UNSIGNED, UN-NOTARIZED build. Gatekeeper will refuse to"
        warn "open it by double-click; users have to right-click the app and choose"
        warn "Open. Do not publish this artifact."
        warn "**********************************************************************"
    fi
    cmd_dmg
}

usage() {
    cat <<'EOF'
Build, sign, notarize and package Taprivo as a macOS .app / .dmg (arm64 only).

Usage:
  packaging/macos/build.sh build      # PyInstaller bundle in .build/macos/dist
  packaging/macos/build.sh sign       # needs TAPRIVO_SIGN_IDENTITY
  packaging/macos/build.sh notarize   # needs TAPRIVO_NOTARY_PROFILE
  packaging/macos/build.sh dmg        # .build/macos/Taprivo-<version>.dmg
  packaging/macos/build.sh all        # build [+ sign + notarize] + dmg

See packaging/macos/README.md for the prerequisites.
EOF
}

main() {
    case "${1:-}" in
        build) cmd_build ;;
        sign) cmd_sign ;;
        notarize) cmd_notarize ;;
        dmg) cmd_dmg ;;
        all) cmd_all ;;
        -h|--help|help|"") usage ;;
        *) die "unknown command '$1'. Run '$0 --help'." ;;
    esac
}

main "$@"
