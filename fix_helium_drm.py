#!/usr/bin/env python3
"""
fix_helium_drm.py — Copy WidevineCdm from Chrome / Edge / Brave into Helium.

Helium browser ships without WidevineCdm, so DRM-protected content (YouTube,
Crunchyroll, etc.) fails.  This script finds an existing Chromium browser on
your system that already has WidevineCdm, then copies it into Helium's
version directory.

LIMITATION: Copying WidevineCdm only lets Helium *load* Widevine.  It does
not change Helium's browser identity, VMP certification, or HDCP support.
Stricter services (Netflix, Amazon Prime Video) may still refuse to play.

Usage:
    python fix_helium_drm.py [options]

Options:
    --dry-run           Show what would be done without writing anything
    --check             Detect source/target and report, no changes
    --verbose           Log each path that is probed
    --source-path PATH  Use this WidevineCdm directory instead of auto-detect
    --helium-path PATH  Use this path as the Helium WidevineCdm target
    --help              Show this message and exit
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

# ── ANSI helpers (no deps) ───────────────────────────────────────────────────
_ANSI = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _ANSI else text


def ok(msg: str) -> str:    return _c("32", f"[+] {msg}")
def fail(msg: str) -> str:  return _c("31", f"[!] {msg}")
def warn(msg: str) -> str:  return _c("33", f"[~] {msg}")
def info(msg: str) -> str:  return _c("36", f"    {msg}")
def bold(msg: str) -> str:  return _c("1",  msg)

# ── Version directory helpers ────────────────────────────────────────────────
_VER_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")


def _ver_key(name: str):
    return tuple(int(x) for x in name.split("."))


def find_version_dirs(base: Path) -> list[Path]:
    """Return immediate subdirs of *base* that look like a browser version,
    sorted highest version first."""
    if not base.is_dir():
        return []
    dirs = [base / e.name for e in base.iterdir()
            if e.is_dir() and _VER_RE.match(e.name)]
    dirs.sort(key=lambda p: _ver_key(p.name), reverse=True)
    return dirs

# ── WidevineCdm validation ───────────────────────────────────────────────────

def is_valid_widevine(d: Path) -> bool:
    """Return True if *d* looks like a complete WidevineCdm directory."""
    return (
        d.is_dir()
        and (d / "manifest.json").is_file()
        and (d / "_platform_specific" / "win_x64" / "widevinecdm.dll").is_file()
    )

# ── Source browser candidates ────────────────────────────────────────────────

def _env(var: str, fallback: str = "") -> str:
    return os.environ.get(var, fallback)


def _source_candidates() -> list[tuple[str, Path]]:
    """Return (browser_label, path_to_probe) pairs in priority order.

    Two layouts are covered:
    1. *Application layout* — browser installed to a per-version subdir:
       <Application>/<version>/WidevineCdm
    2. *User Data component layout* — CDM delivered by the component updater:
       <User Data>/WidevineCdm/<version>   (newest version wins)
    """
    pf   = _env("ProgramFiles",      r"C:\Program Files")
    pf86 = _env("ProgramFiles(x86)", r"C:\Program Files (x86)")
    lad  = _env("LOCALAPPDATA",      str(Path.home() / "AppData" / "Local"))

    # (label, base_path, layout)
    # layout "app"  → scan base for <ver>/WidevineCdm
    # layout "user" → scan base for WidevineCdm/<ver>
    ENTRIES: list[tuple[str, Path, str]] = [
        # Chrome — Application layout
        ("Chrome",        Path(pf,   "Google", "Chrome",                    "Application"), "app"),
        ("Chrome",        Path(pf86, "Google", "Chrome",                    "Application"), "app"),
        ("Chrome",        Path(lad,  "Google", "Chrome",                    "Application"), "app"),
        # Chrome — User Data component layout
        ("Chrome (CDM)",  Path(lad,  "Google", "Chrome",  "User Data", "WidevineCdm"),      "user"),
        # Edge — Application layout
        ("Edge",          Path(pf86, "Microsoft", "Edge",                   "Application"), "app"),
        ("Edge",          Path(pf,   "Microsoft", "Edge",                   "Application"), "app"),
        # Edge — User Data component layout
        ("Edge (CDM)",    Path(lad,  "Microsoft", "Edge", "User Data", "WidevineCdm"),      "user"),
        # Brave — Application layout
        ("Brave",         Path(pf,   "BraveSoftware", "Brave-Browser",      "Application"), "app"),
        ("Brave",         Path(lad,  "BraveSoftware", "Brave-Browser",      "Application"), "app"),
        # Brave — User Data component layout
        ("Brave (CDM)",   Path(lad,  "BraveSoftware", "Brave-Browser", "User Data", "WidevineCdm"), "user"),
    ]

    candidates: list[tuple[str, Path]] = []
    for label, base, layout in ENTRIES:
        if layout == "app":
            for ver_dir in find_version_dirs(base):
                candidates.append((label, ver_dir / "WidevineCdm"))
        else:  # "user" — base IS the WidevineCdm parent
            for ver_dir in find_version_dirs(base):
                candidates.append((label, ver_dir))

    return candidates


def find_source_widevine(override: str | None, verbose: bool) -> Path | None:
    """Return the path to a valid WidevineCdm directory, or None."""
    if override:
        p = Path(override)
        if is_valid_widevine(p):
            return p
        print(fail(f"--source-path is not a valid WidevineCdm directory: {p}"))
        sys.exit(1)

    for label, candidate in _source_candidates():
        if verbose:
            print(info(f"Checking {label}: {candidate}"))
        if is_valid_widevine(candidate):
            return candidate

    return None

# ── Helium target detection ──────────────────────────────────────────────────

def find_helium_target(override: str | None, verbose: bool) -> Path:
    """Return the path where WidevineCdm should be placed inside Helium."""
    if override:
        return Path(override)

    lad = _env("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    helium_app = Path(lad, "imput", "Helium", "Application")

    if verbose:
        print(info(f"Checking Helium base: {helium_app}"))

    if not helium_app.is_dir():
        print(fail("Helium browser not found."))
        print(warn("Please install Helium from: https://helium.is/"))
        sys.exit(1)

    ver_dirs = find_version_dirs(helium_app)
    if not ver_dirs:
        print(fail(f"No version folder found under {helium_app}."))
        print(warn("Launch Helium at least once so it creates its version directory."))
        sys.exit(1)

    target = ver_dirs[0] / "WidevineCdm"
    if verbose:
        print(info(f"Helium version dir: {ver_dirs[0].name} -> target: {target}"))
    return target

# ── Copy logic ───────────────────────────────────────────────────────────────

def copy_widevine(src: Path, dst: Path) -> None:
    """Replace *dst* with a fresh copy of *src*."""
    try:
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
    except PermissionError as exc:
        print(fail(f"Permission denied: {exc}"))
        print(warn("Try running this script as Administrator."))
        sys.exit(1)
    except OSError as exc:
        print(fail(f"Failed to copy WidevineCdm: {exc}"))
        sys.exit(1)

# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if sys.platform != "win32":
        print(fail("This script only supports Windows."))
        sys.exit(1)

    parser = argparse.ArgumentParser(
        prog="fix_helium_drm",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run",     action="store_true",
                        help="Show what would be done, make no changes")
    parser.add_argument("--check",       action="store_true",
                        help="Detect source/target and report, no changes")
    parser.add_argument("--verbose",     action="store_true",
                        help="Log each path probed")
    parser.add_argument("--source-path", metavar="PATH",
                        help="WidevineCdm directory from Chrome/Edge/Brave to use")
    parser.add_argument("--helium-path", metavar="PATH",
                        help="Helium WidevineCdm target directory to write into")
    args = parser.parse_args()

    print(bold("\n[*] Helium DRM Fixer\n"))

    # ── Find source ──────────────────────────────────────────────────────────
    print(info("Looking for WidevineCdm source..."))
    src = find_source_widevine(args.source_path, args.verbose)
    if src is None:
        print(fail("No WidevineCdm source found."))
        print(warn("Install Google Chrome, Microsoft Edge, or Brave Browser, then re-run."))
        print(warn("Downloads: https://www.google.com/chrome/  |  https://www.microsoft.com/edge/  |  https://brave.com/"))
        sys.exit(1)
    print(ok(f"Found WidevineCdm: {src}"))

    # ── Find Helium target ───────────────────────────────────────────────────
    print(info("Looking for Helium installation..."))
    dst = find_helium_target(args.helium_path, args.verbose)
    print(ok(f"Helium target:     {dst}"))

    # ── Early exits ─────────────────────────────────────────────────────────
    if args.check:
        print(bold("\n[+] Check complete -- fix can be applied.\n"))
        return

    if args.dry_run:
        print(bold("\n[~] Dry run -- no changes made."))
        print(info(f"Would copy: {src}"))
        print(info(f"       To:  {dst}"))
        print()
        return

    # ── Apply ────────────────────────────────────────────────────────────────
    print(info("Copying WidevineCdm..."))
    copy_widevine(src, dst)
    print(ok("WidevineCdm copied successfully!"))

    print(bold("\n[+] Done!  Restart Helium for DRM to take effect.\n"))


if __name__ == "__main__":
    main()
