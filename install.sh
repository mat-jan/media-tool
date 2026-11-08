#!/bin/sh
# media-tool installer for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/mat-jan/media-tool/main/install.sh | sh
#
# Files downloaded with curl are not quarantined by macOS, so there is no Gatekeeper warning.
# Installs into ~/.local/share/media-tool and links ~/.local/bin/media-tool. No root needed.
#
# Options (environment variables):
#   MEDIA_TOOL_VERSION=v1.0.0-beta.1   install a specific release (default: newest, including betas)
#   MEDIA_TOOL_INSTALL_DIR=...         install directory
#   MEDIA_TOOL_BIN_DIR=...             where to put the `media-tool` command
# Uninstall:  curl -fsSL .../install.sh | sh -s -- --uninstall
set -eu

REPO="mat-jan/media-tool"
PREFIX="${MEDIA_TOOL_INSTALL_DIR:-$HOME/.local/share/media-tool}"
BIN_DIR="${MEDIA_TOOL_BIN_DIR:-$HOME/.local/bin}"
MARKER=".media-tool-install"

say() { printf '%s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "required tool not found: $1"; }

# never delete a directory this installer did not create
safe_to_replace() {
  [ ! -e "$1" ] && return 0
  [ -f "$1/$MARKER" ] && return 0
  [ -d "$1" ] && [ -z "$(ls -A "$1" 2>/dev/null)" ] && return 0
  return 1
}

if [ "${1:-}" = "--uninstall" ]; then
  if [ -e "$PREFIX" ]; then
    [ -f "$PREFIX/$MARKER" ] || die "$PREFIX was not created by this installer - refusing to delete it"
    rm -rf "$PREFIX"
    say "Removed $PREFIX"
  fi
  [ -L "$BIN_DIR/media-tool" ] || [ -f "$BIN_DIR/media-tool" ] && rm -f "$BIN_DIR/media-tool" && say "Removed $BIN_DIR/media-tool"
  exit 0
fi

need curl; need unzip; need uname; need mktemp

os=$(uname -s); arch=$(uname -m)
case "$os" in
  Darwin) case "$arch" in
            arm64) asset=macos-arm64 ;;
            x86_64) asset=macos-x64 ;;
            *) die "unsupported Mac architecture: $arch" ;;
          esac ;;
  Linux)  case "$arch" in
            x86_64|amd64) asset=linux-x64 ;;
            *) asset=universal
               say "No prebuilt binary for $arch - installing the universal package (needs Python 3.9+)." ;;
          esac ;;
  *) die "unsupported system: $os (on Windows use install.ps1)" ;;
esac

tag="${MEDIA_TOOL_VERSION:-}"
if [ -z "$tag" ]; then
  say "Looking up the newest release..."
  tag=$(curl -fsSL "https://api.github.com/repos/$REPO/releases" |
        sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n 1) || true
  [ -n "$tag" ] || die "could not determine the newest release (GitHub API rate limit?). Set MEDIA_TOOL_VERSION=vX.Y.Z"
fi
base="${MEDIA_TOOL_BASE_URL:-https://github.com/$REPO/releases/download/$tag}"

tmp=$(mktemp -d) || die "cannot create a temporary directory"
trap 'rm -rf "$tmp"' EXIT INT TERM

say "Downloading media-tool $tag ($asset)..."
curl -fSL --progress-bar -o "$tmp/pkg.zip" "$base/media-tool-$asset.zip" || die "download failed: $base/media-tool-$asset.zip"

if curl -fsSL -o "$tmp/SHA256SUMS.txt" "$base/SHA256SUMS.txt" 2>/dev/null; then
  want=$(grep " media-tool-$asset.zip\$" "$tmp/SHA256SUMS.txt" | awk '{print $1}')
  if command -v sha256sum >/dev/null 2>&1; then got=$(sha256sum "$tmp/pkg.zip" | awk '{print $1}')
  else got=$(shasum -a 256 "$tmp/pkg.zip" | awk '{print $1}'); fi
  [ -n "$want" ] && [ "$want" = "$got" ] || die "checksum mismatch - the download is corrupted or was tampered with"
  say "Checksum OK."
else
  say "Note: this release has no SHA256SUMS.txt - skipping checksum verification."
fi

unzip -q "$tmp/pkg.zip" -d "$tmp/x" || die "cannot unpack the archive"
[ -d "$tmp/x/media-tool" ] || die "unexpected archive layout"

safe_to_replace "$PREFIX" || die "$PREFIX exists and was not created by this installer - choose another MEDIA_TOOL_INSTALL_DIR"
rm -rf "$PREFIX"
mkdir -p "$PREFIX" "$BIN_DIR"
cp -R "$tmp/x/media-tool/." "$PREFIX/"
: > "$PREFIX/$MARKER"
# harmless when absent; clears quarantine if the archive was fetched by a browser and passed to this script
[ "$os" = "Darwin" ] && xattr -dr com.apple.quarantine "$PREFIX" 2>/dev/null || true

if [ -x "$PREFIX/media-tool" ]; then
  ln -sf "$PREFIX/media-tool" "$BIN_DIR/media-tool"
else
  printf '#!/bin/sh\nexec python3 "%s/media-tool.pyz" "$@"\n' "$PREFIX" > "$BIN_DIR/media-tool"
  chmod +x "$BIN_DIR/media-tool"
fi

say ""
say "Installed media-tool $tag"
"$BIN_DIR/media-tool" --version || die "the installed program does not start"
say ""
say "Run it:            $BIN_DIR/media-tool          (interactive menu)"
say "Full access:       sudo $BIN_DIR/media-tool     (images, scans, speed tests need administrator rights)"
say "Double-click:      $PREFIX/Start-$( [ "$os" = Darwin ] && echo macOS.command || echo Linux.sh )"
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) say "Tip: add $BIN_DIR to your PATH to run just 'media-tool'." ;; esac
say "Uninstall:         curl -fsSL https://raw.githubusercontent.com/$REPO/main/install.sh | sh -s -- --uninstall"
