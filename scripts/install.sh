#!/bin/sh
# install.sh — universal installer for the `ww` CLI.
#
# Usage:
#
#   curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh
#   curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh -s -- --version v0.5.0
#   curl -fsSL https://github.com/witwave-ai/witwave/releases/latest/download/install.sh | sh -s -- --prefix "$HOME/.local"
#
# Or download + inspect first, then run:
#
#   curl -fsSL -o install.sh https://github.com/witwave-ai/witwave/releases/latest/download/install.sh
#   less install.sh
#   sh install.sh
#
# Flags (all optional, env var equivalents in parens):
#
#   --version <tag>     Pin to a specific release tag (e.g. v0.5.0). Default: latest stable.
#                       (env: WW_VERSION)
#   --channel <c>       'stable' (default) or 'beta'. Beta includes -beta.N / -rc.N tags.
#                       (env: WW_CHANNEL)
#   --prefix <dir>      Install root. Binary lands in <prefix>/bin. Default: /usr/local
#                       or $HOME/.local depending on writability.
#                       (env: WW_INSTALL_DIR — sets the bin dir directly, overriding --prefix.)
#   --use-sudo          Allow `sudo` for /usr/local writes. Default: skip sudo silently and
#                       fall back to $HOME/.local/bin.
#                       (env: WW_USE_SUDO=1)
#   --no-verify         Skip SHA256 verification. NOT recommended.
#                       (env: WW_NO_VERIFY=1)
#   --verify-signature  Also verify the cosign signature. Requires `cosign` on PATH.
#                       (env: WW_VERIFY_SIGNATURE=1)
#   --dry-run           Print what would happen, change nothing.
#                       (env: WW_DRY_RUN=1)
#   --quiet             Suppress progress output (errors still go to stderr).
#                       (env: WW_QUIET=1)
#   --force             Reinstall even when the same version is already
#                       present at the destination. Default: detect an
#                       existing same-version install and exit 0 with a
#                       no-op message. Different-version installs always
#                       proceed (no --force needed for upgrades).
#                       (env: WW_FORCE=1)
#   --uninstall         Remove the installed binary + the .ww.install-info marker.
#   --help              Print this help and exit 0.
#
# Advanced (for local snapshot testing — not part of the public install
# flow):
#
#   WW_BASE_URL=<url>   Override the release-asset base URL. Default:
#                       https://github.com/witwave-ai/witwave/releases/download/<tag>
#                       Setting this lets you point the script at a
#                       `goreleaser release --snapshot --clean` dist/ served
#                       over a local http server (see clients/ww/README.md
#                       "Testing the installer locally" for the recipe).
#                       The marker file's install_url= still points at the
#                       canonical install URL — the override is for the
#                       fetch only, not for what `ww update` will re-run.
#
# Exit codes:
#
#   0   success
#   1   generic failure
#   2   unsupported platform (OS / arch)
#   3   download failure
#   4   verification failure (checksum or signature)
#   5   install location not writable and --use-sudo not granted
#   6   no http client (curl or wget) found
#
# Repo: https://github.com/witwave-ai/witwave
# License: Apache-2.0 (see LICENSE in the same release)

set -eu

# ---- constants -------------------------------------------------------------

REPO_OWNER="witwave-ai"
REPO_NAME="witwave"
GITHUB_REPO="${REPO_OWNER}/${REPO_NAME}"
BIN_NAME="ww"
MARKER_NAME=".${BIN_NAME}.install-info"

# Canonical install URL — used in self-update advice baked into the marker
# file. Keep in sync with whatever the README documents.
INSTALL_URL="https://github.com/${GITHUB_REPO}/releases/latest/download/install.sh"

# ---- defaults --------------------------------------------------------------

ww_version="${WW_VERSION:-}"
ww_channel="${WW_CHANNEL:-stable}"
ww_prefix=""
ww_install_dir="${WW_INSTALL_DIR:-}"
ww_use_sudo="${WW_USE_SUDO:-}"
ww_no_verify="${WW_NO_VERIFY:-}"
ww_verify_sig="${WW_VERIFY_SIGNATURE:-}"
ww_dry_run="${WW_DRY_RUN:-}"
ww_quiet="${WW_QUIET:-}"
ww_force="${WW_FORCE:-}"
ww_uninstall=""

# WW_BASE_URL is the dev-only escape hatch for pointing the script at a
# local goreleaser --snapshot dist/. Strip a trailing slash so the URL
# concat below doesn't double up. Empty = use the canonical release URL.
ww_base_url="${WW_BASE_URL:-}"
ww_base_url="${ww_base_url%/}"

# ---- io helpers ------------------------------------------------------------

log() {
  if [ -z "$ww_quiet" ]; then
    printf '%s\n' "$*"
  fi
}

warn() {
  printf 'ww-install: warning: %s\n' "$*" >&2
}

die() {
  code="$1"
  shift
  printf 'ww-install: error: %s\n' "$*" >&2
  exit "$code"
}

usage() {
  # Print the comment block at the top of this file (everything between
  # the shebang and the first non-comment line). Saves us maintaining
  # two copies of the help text.
  sed -n '2,/^[^#]/p' "$0" | sed 's/^# \{0,1\}//'
}

# ---- arg parsing -----------------------------------------------------------

while [ $# -gt 0 ]; do
  case "$1" in
  --version)
    [ $# -ge 2 ] || die 1 "--version requires an argument"
    ww_version="$2"
    shift 2
    ;;
  --version=*)
    ww_version="${1#*=}"
    shift
    ;;
  --channel)
    [ $# -ge 2 ] || die 1 "--channel requires an argument"
    ww_channel="$2"
    shift 2
    ;;
  --channel=*)
    ww_channel="${1#*=}"
    shift
    ;;
  --prefix)
    [ $# -ge 2 ] || die 1 "--prefix requires an argument"
    ww_prefix="$2"
    shift 2
    ;;
  --prefix=*)
    ww_prefix="${1#*=}"
    shift
    ;;
  --install-dir)
    [ $# -ge 2 ] || die 1 "--install-dir requires an argument"
    ww_install_dir="$2"
    shift 2
    ;;
  --install-dir=*)
    ww_install_dir="${1#*=}"
    shift
    ;;
  --use-sudo)
    ww_use_sudo=1
    shift
    ;;
  --no-verify)
    ww_no_verify=1
    shift
    ;;
  --verify-signature)
    ww_verify_sig=1
    shift
    ;;
  --dry-run)
    ww_dry_run=1
    shift
    ;;
  --quiet | -q)
    ww_quiet=1
    shift
    ;;
  --force)
    ww_force=1
    shift
    ;;
  --uninstall)
    ww_uninstall=1
    shift
    ;;
  -h | --help)
    usage
    exit 0
    ;;
  --)
    shift
    break
    ;;
  -*) die 1 "unknown flag: $1 (try --help)" ;;
  *) die 1 "unexpected positional arg: $1" ;;
  esac
done

case "$ww_channel" in
stable | beta) : ;;
*) die 1 "unknown channel '$ww_channel' (expected 'stable' or 'beta')" ;;
esac

# ---- dependency probe -------------------------------------------------------

# Pick the http client once; fall back order is curl → wget. We never need
# both. tar + uname + sed + grep are assumed (POSIX baseline + busybox).
if command -v curl >/dev/null 2>&1; then
  http_client=curl
elif command -v wget >/dev/null 2>&1; then
  http_client=wget
else
  die 6 "neither curl nor wget found on PATH — install one and retry"
fi

# Pick the SHA256 tool once. GNU coreutils ships sha256sum; macOS / BSD
# ships shasum (with -a 256). busybox provides sha256sum.
if command -v sha256sum >/dev/null 2>&1; then
  sha256_cmd="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
  sha256_cmd="shasum -a 256"
else
  if [ -z "$ww_no_verify" ]; then
    die 4 "no sha256sum/shasum found and --no-verify not set; cannot verify download"
  fi
fi

# ---- http helpers -----------------------------------------------------------

# fetch <url> <dest>  — downloads, fails non-zero on http error.
fetch() {
  _url="$1"
  _dest="$2"
  case "$http_client" in
  curl) curl -fsSL --retry 3 --retry-delay 2 -o "$_dest" "$_url" ;;
  wget) wget -q --tries=3 --waitretry=2 -O "$_dest" "$_url" ;;
  esac
}

# fetch_url_effective <url>  — follow redirects, print the final URL.
# Used to discover the latest stable release tag without an API call
# (sidesteps the GitHub API rate limit for unauthenticated callers).
fetch_url_effective() {
  _url="$1"
  case "$http_client" in
  curl) curl -fsSLI -o /dev/null -w '%{url_effective}' "$_url" ;;
  wget)
    # wget doesn't have a clean equivalent; parse the redirect
    # chain from --max-redirect=20 -S output. Last 'Location:'
    # header wins; if there are none, echo the input URL.
    _hdrs="$(wget -q -S --max-redirect=20 --method=HEAD -O /dev/null "$_url" 2>&1 || true)"
    _loc="$(printf '%s\n' "$_hdrs" | awk '/^[[:space:]]*Location:/ {print $2}' | tail -1)"
    if [ -n "$_loc" ]; then
      printf '%s' "$_loc"
    else
      printf '%s' "$_url"
    fi
    ;;
  esac
}

# ---- platform detection -----------------------------------------------------

# detect_platform  — sets ww_os (linux|darwin) and ww_arch (amd64|arm64)
# from `uname -s` / `uname -m`. Dies with exit code 2 on anything else,
# matching the OS/arch matrix the goreleaser config publishes archives
# for.
detect_platform() {
  _os="$(uname -s)"
  _arch="$(uname -m)"

  case "$_os" in
  Linux) ww_os=linux ;;
  Darwin) ww_os=darwin ;;
  *) die 2 "unsupported OS: $_os (supported: Linux, Darwin)" ;;
  esac

  case "$_arch" in
  x86_64 | amd64) ww_arch=amd64 ;;
  aarch64 | arm64) ww_arch=arm64 ;;
  *) die 2 "unsupported architecture: $_arch (supported: x86_64/amd64, aarch64/arm64)" ;;
  esac
}

# ---- version resolution -----------------------------------------------------

# resolve_version  — sets ww_version to a concrete vX.Y.Z[-suffix] tag.
# When the caller passed --version explicitly we trust it as-is. Otherwise
# we look up the channel's latest tag.
resolve_version() {
  if [ -n "$ww_version" ]; then
    # Normalise: allow callers to pass either "v0.5.0" or "0.5.0".
    case "$ww_version" in
    v*) : ;;
    *) ww_version="v$ww_version" ;;
    esac
    return
  fi

  if [ "$ww_channel" = "stable" ]; then
    # /releases/latest never returns prereleases — perfect for stable.
    # Resolve via the redirect target (no API rate limit, no auth).
    _final="$(fetch_url_effective "https://github.com/${GITHUB_REPO}/releases/latest")"
    # Final URL shape: https://github.com/<owner>/<repo>/releases/tag/vX.Y.Z
    ww_version="${_final##*/}"
    case "$ww_version" in
    v*) : ;;
    *) die 3 "could not resolve latest stable tag (got '$ww_version' from $_final)" ;;
    esac
    return
  fi

  # Beta channel: hit the API and pick the first tag (the API returns
  # releases sorted newest-first by created_at).
  _api="https://api.github.com/repos/${GITHUB_REPO}/releases?per_page=20"
  _tmp="$(mktemp)"
  if ! fetch "$_api" "$_tmp"; then
    rm -f "$_tmp"
    die 3 "failed to query $_api (rate limited? set WW_VERSION=vX.Y.Z to skip the lookup)"
  fi
  # Pluck "tag_name": "vX.Y.Z..." with grep+sed; avoids a jq dependency.
  ww_version="$(grep -E '"tag_name"' "$_tmp" | head -n 1 | sed -E 's/.*"tag_name"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/')"
  rm -f "$_tmp"
  case "$ww_version" in
  v*) : ;;
  *) die 3 "could not parse latest beta tag from $_api" ;;
  esac
}

# ---- install path resolution ------------------------------------------------

# resolve_install_dir  — sets ww_bindir to the directory we'll mv the
# binary into. Honors --install-dir > --prefix > /usr/local fallback >
# ~/.local fallback. Sets ww_use_sudo_cmd to "" or "sudo" depending on
# whether we'll need elevation.
resolve_install_dir() {
  ww_use_sudo_cmd=""

  if [ -n "$ww_install_dir" ]; then
    ww_bindir="$ww_install_dir"
  elif [ -n "$ww_prefix" ]; then
    ww_bindir="${ww_prefix}/bin"
  else
    # Default policy: prefer /usr/local/bin if writable as the current
    # user. If not, use sudo when explicitly granted; otherwise fall
    # back to ~/.local/bin (no-sudo, always works for the current user).
    if [ -w /usr/local/bin ] || { [ ! -e /usr/local/bin ] && [ -w /usr/local ]; }; then
      ww_bindir="/usr/local/bin"
    elif [ -n "$ww_use_sudo" ] && command -v sudo >/dev/null 2>&1; then
      ww_bindir="/usr/local/bin"
      ww_use_sudo_cmd="sudo"
    else
      ww_bindir="${HOME}/.local/bin"
      # We don't need to mark "didn't exist" — the PATH check at
      # the end of do_install fires whenever ww_bindir isn't on
      # PATH, regardless of whether we created it just now.
    fi
  fi

  # If the caller pointed us somewhere we can't write, surface that
  # before we download anything.
  if [ -e "$ww_bindir" ] && [ ! -w "$ww_bindir" ] && [ -z "$ww_use_sudo_cmd" ]; then
    die 5 "$ww_bindir is not writable (re-run with --use-sudo, or pass --prefix=$HOME/.local)"
  fi
}

# ---- download + verify ------------------------------------------------------

# download_and_verify  — fetches the release archive matching ww_os /
# ww_arch / ww_version into ww_workdir and (unless --no-verify) checks
# the sha256 entry from the release's checksums.txt. With
# --verify-signature, additionally cosign-verifies the checksums file
# against the canonical GitHub Actions OIDC issuer; skipped silently
# when WW_BASE_URL points at an unsigned local snapshot. Falls through
# to a tar -xzf and asserts the BIN_NAME file landed in ww_workdir.
download_and_verify() {
  # goreleaser archive name template:
  #   ww_<version_no_v>_<os>_<arch>.tar.gz
  _ver_noprefix="${ww_version#v}"
  _archive="${BIN_NAME}_${_ver_noprefix}_${ww_os}_${ww_arch}.tar.gz"
  _checksums="checksums.txt"

  if [ -n "$ww_base_url" ]; then
    # Local snapshot mode: caller pointed us at their own URL prefix
    # (e.g. an `python3 -m http.server` over goreleaser's dist/).
    # Skip the canonical /releases/download/<tag>/ path entirely;
    # snapshots don't have one.
    _base="$ww_base_url"
  else
    _base="https://github.com/${GITHUB_REPO}/releases/download/${ww_version}"
  fi
  _archive_url="${_base}/${_archive}"
  _checksums_url="${_base}/${_checksums}"

  log "Downloading ${_archive_url}"
  if ! fetch "$_archive_url" "${ww_workdir}/${_archive}"; then
    die 3 "failed to download ${_archive_url} (does the tag '${ww_version}' have a ${ww_os}/${ww_arch} build?)"
  fi

  if [ -z "$ww_no_verify" ]; then
    log "Verifying SHA256"
    if ! fetch "$_checksums_url" "${ww_workdir}/${_checksums}"; then
      die 4 "failed to download checksums.txt from $_checksums_url"
    fi
    # Find the line matching our archive and verify just that one.
    # POSIX grep + filter to keep busybox happy.
    _line="$(grep " ${_archive}\$" "${ww_workdir}/${_checksums}" || true)"
    if [ -z "$_line" ]; then
      die 4 "checksums.txt has no entry for ${_archive}"
    fi
    printf '%s\n' "$_line" >"${ww_workdir}/${_archive}.sha256"
    (cd "$ww_workdir" && $sha256_cmd -c "${_archive}.sha256") >/dev/null 2>&1 ||
      die 4 "SHA256 verification failed for ${_archive}"
  else
    warn "skipping SHA256 verification (--no-verify)"
  fi

  if [ -n "$ww_verify_sig" ] && [ -n "$ww_base_url" ]; then
    warn "skipping cosign verification: WW_BASE_URL is set (snapshot artifacts are unsigned)"
    ww_verify_sig=""
  fi

  if [ -n "$ww_verify_sig" ]; then
    if ! command -v cosign >/dev/null 2>&1; then
      die 4 "--verify-signature requested but 'cosign' is not on PATH"
    fi
    log "Verifying cosign keyless signature on checksums.txt"
    _sig_url="${_base}/${_checksums}.sig"
    _cert_url="${_base}/${_checksums}.pem"
    fetch "$_sig_url" "${ww_workdir}/${_checksums}.sig" || die 4 "failed to download ${_sig_url}"
    fetch "$_cert_url" "${ww_workdir}/${_checksums}.pem" || die 4 "failed to download ${_cert_url}"
    cosign verify-blob \
      --certificate "${ww_workdir}/${_checksums}.pem" \
      --signature "${ww_workdir}/${_checksums}.sig" \
      --certificate-identity-regexp "https://github.com/${GITHUB_REPO}/" \
      --certificate-oidc-issuer https://token.actions.githubusercontent.com \
      "${ww_workdir}/${_checksums}" >/dev/null 2>&1 ||
      die 4 "cosign signature verification failed"
  fi

  log "Extracting ${_archive}"
  (cd "$ww_workdir" && tar -xzf "$_archive")
  if [ ! -f "${ww_workdir}/${BIN_NAME}" ]; then
    die 1 "extracted archive does not contain '${BIN_NAME}' binary"
  fi
}

# ---- existing-install detection --------------------------------------------

# read_marker_version <bindir>  — prints the `version=` line value from
# the marker file in <bindir>, or empty if no marker / no version line.
# Pure POSIX, no jq/awk juggling. Used by check_existing_install to
# decide whether the requested install would be a no-op.
read_marker_version() {
  _marker="$1/${MARKER_NAME}"
  [ -f "$_marker" ] || {
    printf ''
    return
  }
  # Strip 'version=' prefix from the matching line. Whitespace around
  # the value is allowed by the marker spec.
  grep -E '^[[:space:]]*version[[:space:]]*=' "$_marker" 2>/dev/null |
    head -n 1 |
    sed -E 's/^[[:space:]]*version[[:space:]]*=[[:space:]]*//' |
    sed -E 's/[[:space:]]*$//'
}

# check_existing_install  — if <ww_bindir>/<BIN_NAME> exists AND its
# marker reports the same version we're about to install AND --force
# wasn't passed, print a "no-op" message and exit 0. Different-version
# installs print an "upgrading from X to Y" line and proceed (no
# --force needed for upgrades — that's the common, expected flow).
check_existing_install() {
  [ -n "$ww_force" ] && return 0
  [ -e "${ww_bindir}/${BIN_NAME}" ] || return 0

  _existing="$(read_marker_version "$ww_bindir")"
  if [ -z "$_existing" ]; then
    # Binary present but no marker — could be a hand-installed
    # tarball or a previous install from before the marker existed.
    # Don't surprise the user by silently overwriting; warn and
    # proceed (they got what they asked for either way).
    warn "${ww_bindir}/${BIN_NAME} exists but has no install marker — replacing it."
    return 0
  fi

  if [ "$_existing" = "$ww_version" ]; then
    log "${BIN_NAME} ${ww_version} is already installed at ${ww_bindir}/${BIN_NAME}."
    log "Pass --force (or set WW_FORCE=1) to reinstall."
    # Exit cleanly so curl|sh callers don't see a non-zero status.
    # Trap will clean up the workdir on its way out.
    exit 0
  fi

  log "Upgrading ${BIN_NAME}: ${_existing} → ${ww_version}"
}

# ---- install ----------------------------------------------------------------

# do_install  — moves the verified binary from ww_workdir to
# ${ww_bindir}/${BIN_NAME} atomically (cp + chmod 0755 + mv onto a
# sibling tmp path), then writes a sibling install marker recording
# installer/version/channel/install_url/installed_at for `ww update`
# to consume. Honours --dry-run (logs the would-be actions and
# returns), --use-sudo (prefixes every privileged step via
# ww_use_sudo_cmd), and emits a shell-aware PATH advisory if ww_bindir
# isn't already on PATH.
do_install() {
  _dest="${ww_bindir}/${BIN_NAME}"
  _tmp_dest="${ww_bindir}/.${BIN_NAME}.new.$$"

  if [ -n "$ww_dry_run" ]; then
    log "[dry-run] would install ${ww_workdir}/${BIN_NAME} → ${_dest}"
    log "[dry-run] would write ${ww_bindir}/${MARKER_NAME}"
    return
  fi

  # Make sure the bin dir exists. We mkdir -p it; if that needs sudo
  # the caller already opted in via --use-sudo (and ww_use_sudo_cmd is
  # set). Otherwise bail with a clear message.
  if [ ! -d "$ww_bindir" ]; then
    if ! ${ww_use_sudo_cmd} mkdir -p "$ww_bindir" 2>/dev/null; then
      die 5 "cannot create $ww_bindir — re-run with --use-sudo or --prefix=\$HOME/.local"
    fi
  fi

  # Atomic install: copy to a sibling tmp path, chmod, then mv. The mv
  # within the same filesystem is atomic on POSIX; either the old or
  # the new binary is at $_dest at all times — no half-written state.
  ${ww_use_sudo_cmd} cp "${ww_workdir}/${BIN_NAME}" "$_tmp_dest"
  ${ww_use_sudo_cmd} chmod 0755 "$_tmp_dest"
  ${ww_use_sudo_cmd} mv "$_tmp_dest" "$_dest"

  # Drop a sibling marker so `ww update` knows it was installed via
  # this script and can re-run the curl|sh pipeline. Keep the schema
  # forward-compatible: simple key=value lines.
  _marker="${ww_bindir}/${MARKER_NAME}"
  _marker_tmp="${ww_workdir}/marker.$$"
  {
    printf 'installer=curl\n'
    printf 'version=%s\n' "$ww_version"
    printf 'channel=%s\n' "$ww_channel"
    printf 'install_url=%s\n' "$INSTALL_URL"
    printf 'installed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } >"$_marker_tmp"
  ${ww_use_sudo_cmd} mv "$_marker_tmp" "$_marker"

  log "Installed ${BIN_NAME} ${ww_version} → ${_dest}"

  # Run the binary so the user gets the same `ww version` line they'd
  # see otherwise — confirms the install actually works (right arch,
  # not corrupted) and shows the commit + build date inline.
  # Don't fail the overall install on a `ww version` non-zero exit
  # (defensive — this should never happen for a real release binary).
  if [ -x "$_dest" ]; then
    log ""
    "$_dest" version 2>&1 || true
  fi

  # PATH advisory when the install dir isn't on PATH. SHELL-aware:
  # show only the line that matches the user's current shell, with
  # an unconditional "add for the current process" hint that works
  # everywhere. Falls through to listing all options when SHELL is
  # unset/unknown (e.g. running under cron, a CI runner, etc.).
  case ":$PATH:" in
  *":${ww_bindir}:"*) : ;;
  *)
    log ""
    warn "${ww_bindir} is not on your PATH."
    warn "Add it for the current shell:"
    warn "  export PATH=\"${ww_bindir}:\$PATH\""
    _shell_name=""
    [ -n "${SHELL:-}" ] && _shell_name="$(basename "$SHELL")"
    case "$_shell_name" in
    bash)
      warn "Persist it (bash):"
      warn "  echo 'export PATH=\"${ww_bindir}:\$PATH\"' >> ~/.bashrc"
      ;;
    zsh)
      warn "Persist it (zsh):"
      warn "  echo 'export PATH=\"${ww_bindir}:\$PATH\"' >> ~/.zshrc"
      ;;
    fish)
      warn "Persist it (fish):"
      warn "  fish_add_path ${ww_bindir}"
      ;;
    *)
      warn "Persist it (pick the file matching your shell):"
      warn "  echo 'export PATH=\"${ww_bindir}:\$PATH\"' >> ~/.bashrc"
      warn "  echo 'export PATH=\"${ww_bindir}:\$PATH\"' >> ~/.zshrc"
      warn "  fish_add_path ${ww_bindir}    # fish"
      ;;
    esac
    ;;
  esac
}

# ---- uninstall path ---------------------------------------------------------

do_uninstall() {
  # Locate a previously-installed ww. Prefer the marker file if we can
  # find one; otherwise fall back to `command -v ww`.
  _candidates="/usr/local/bin ${HOME}/.local/bin"
  _found=""
  for _d in $_candidates; do
    if [ -f "${_d}/${MARKER_NAME}" ] && [ -f "${_d}/${BIN_NAME}" ]; then
      _found="$_d"
      break
    fi
  done
  if [ -z "$_found" ]; then
    _bin="$(command -v ${BIN_NAME} 2>/dev/null || true)"
    if [ -n "$_bin" ]; then
      _found="$(dirname "$_bin")"
    fi
  fi
  if [ -z "$_found" ]; then
    die 1 "could not find an installed ${BIN_NAME} to uninstall"
  fi

  _sudo=""
  if [ ! -w "$_found" ] && [ -n "$ww_use_sudo" ] && command -v sudo >/dev/null 2>&1; then
    _sudo="sudo"
  fi

  if [ -n "$ww_dry_run" ]; then
    log "[dry-run] would remove ${_found}/${BIN_NAME} and ${_found}/${MARKER_NAME}"
    return
  fi

  ${_sudo} rm -f "${_found}/${BIN_NAME}" "${_found}/${MARKER_NAME}"
  log "Uninstalled ${BIN_NAME} from ${_found}"
  log "Note: config and state under ~/.config/ww are NOT removed (rm -rf ~/.config/ww to wipe)."
}

# ---- main -------------------------------------------------------------------

main() {
  if [ -n "$ww_uninstall" ]; then
    do_uninstall
    return
  fi

  detect_platform
  resolve_version
  resolve_install_dir
  # Existing-install short-circuit: only after we know what version
  # the user is asking for AND where it would land. May exit 0
  # without doing anything if the same version is already there.
  check_existing_install

  log "Installing ${BIN_NAME} ${ww_version} (${ww_os}/${ww_arch}) → ${ww_bindir}"
  if [ -n "$ww_use_sudo_cmd" ]; then
    log "Using sudo for writes to ${ww_bindir}"
  fi

  # Workdir: per-run temp dir cleaned up on exit (success or failure).
  ww_workdir="$(mktemp -d 2>/dev/null || mktemp -d -t "${BIN_NAME}-install")"
  # shellcheck disable=SC2064  # intentional early-binding of $ww_workdir
  trap "rm -rf '$ww_workdir'" EXIT INT HUP TERM

  if [ -n "$ww_dry_run" ]; then
    log "[dry-run] would download ww ${ww_version} for ${ww_os}/${ww_arch}"
    do_install
    return
  fi

  download_and_verify
  do_install
  # do_install execs the binary itself now, so no separate
  # "Run 'ww version' to confirm" tail is needed here.
}

main
