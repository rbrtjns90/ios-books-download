#!/usr/bin/env python3
"""ios-books-download: export Books/iBooks content from an iOS device.

Clean-room implementation based on the data layout documented in
``analysis/iMazing-Books.md``:

    Books/Books.plist               sideloaded / imported books
    Books/Purchases/Purchases.plist purchased (FairPlay) books

AFC (``com.apple.afc``) exposes the device media partition
(``/var/mobile/Media``), so the plists and book files are reachable
without a jailbreak. When the plists are empty the ``Books/`` and
``Books/Purchases/`` directories are scanned instead.

Purchased books are stored as *unzipped* EPUB bundle directories
(``Books/Purchases/<id>.epub/``); those are repackaged into normal
``.epub``/``.ibooks`` zips. PDFs are copied as-is, or extracted from a
bundle directory when iOS stores one that way.

The tool is read-only: nothing is written to the device. DRM-protected
files are copied as-is and are *not* decrypted.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import io
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__version__ = "1.3.0"

SOURCES = (
    "Books/Books.plist",
    "Books/Purchases/Purchases.plist",
)
IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"

SCAN_DIRS = ("Books", "Books/Purchases")
BOOK_EXTS = (".epub", ".pdf", ".ibooks")
MEDIA_PREFIXES = (
    "/private/var/mobile/media/",
    "/var/mobile/media/",
)
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


# ---------------------------------------------------------------------------
# Book model
# ---------------------------------------------------------------------------


@dataclass
class Book:
    """One catalog record, mirroring iMazing's ``Book`` object."""

    name: str = ""
    artist: str = ""
    genre: str = ""
    path: str = ""
    extension: str = ""
    mime_type: str = ""
    is_protected: bool = False
    is_purchase: bool = False
    is_pdf: bool = False
    is_sample: bool = False
    has_artwork: bool = False
    unique_id: int | None = None
    persistent_id: int | None = None
    package_hash: str = ""
    source: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_record(cls, record: dict, source: str = "") -> Book:
        """Build a book from a plist dictionary."""
        if not isinstance(record, dict):
            raise TypeError("book record is not a dict")

        return cls(
            name=_text(_get(record, "Name", "Title")),
            artist=_text(_get(record, "Artist", "Author")),
            genre=_text(_get(record, "Genre")),
            path=_text(_get(record, "Path")),
            extension=_text(_get(record, "Extension")),
            mime_type=_text(_get(record, "MimeType", "MIMEType")),
            is_protected=_bool(_get(record, "IsProtected")),
            is_purchase=_bool(_get(record, "IsPurchase")),
            is_pdf=_bool(_get(record, "IsPDF")),
            is_sample=_bool(_get(record, "IsSample")),
            has_artwork=_bool(_get(record, "HasArtwork")),
            unique_id=_int(_get(record, "UniqueID")),
            persistent_id=_int(_get(record, "PersistentID")),
            package_hash=_text(_get(record, "PackageHash")),
            source=source,
            raw=record,
        )

    @property
    def flags(self) -> str:
        """Short, human-readable status flags."""
        markers = (
            ("DRM", self.is_protected),
            ("PDF", self.is_pdf),
            ("sample", self.is_sample),
        )
        return ",".join(name for name, enabled in markers if enabled)

    @property
    def filename(self) -> str:
        """Filename to use when writing this book locally."""
        stem = sanitize_filename(self.name) or "book"
        suffix = self.extension or Path(self.path).suffix.lstrip(".")
        suffix = suffix.lstrip(".").lower()
        return f"{stem}.{suffix}" if suffix else stem

    def to_dict(self) -> dict:
        """JSON-friendly representation (drops the raw plist record)."""
        data = asdict(self)
        data.pop("raw", None)
        data["filename"] = self.filename
        return data


def _get(record: dict, *names: str) -> Any:
    """Case-insensitive lookup for the various plist spellings."""
    lowered = {str(key).lower(): value for key, value in record.items()}
    for name in names:
        key = name.lower()
        if key in lowered:
            return lowered[key]
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return False


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Catalog parsing
# ---------------------------------------------------------------------------


def books_from_plist(payload: bytes, source: str = "") -> list[Book]:
    """Parse a Books.plist / Purchases.plist payload."""
    records = _find_records(plistlib.loads(payload))
    return [Book.from_record(record, source) for record in records]


def _find_records(data: Any) -> list[dict]:
    """Locate the list of book dictionaries in a plist root object."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if isinstance(data, dict):
        for key in (
            "Books",
            "books",
            "Purchases",
            "purchases",
            "items",
            "Items",
        ):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]

        # Unknown single-array wrapper: first list of dicts wins.
        for value in data.values():
            if isinstance(value, list) and any(
                isinstance(item, dict) for item in value
            ):
                return [item for item in value if isinstance(item, dict)]

        # Some versions key books by asset ID: {id: {Name, Path, ...}}.
        keyed = [value for value in data.values() if isinstance(value, dict)]
        if keyed and any(_looks_like_book(value) for value in keyed):
            return keyed

    return []


def _looks_like_book(value: dict) -> bool:
    keys = {str(key).lower() for key in value}
    return bool(keys & {"name", "title", "path", "artist", "author"})


async def load_catalog(
    fs: Afc,
    *,
    include_scan: bool = True,
) -> tuple[list[Book], list[str]]:
    raise NotImplementedError


async def scan_books_dir(fs: Afc) -> list[Book]:
    """Enumerate book files when the catalog plists are unusable."""
    books: list[Book] = []

    for directory in SCAN_DIRS:
        try:
            entries = await fs.listdir(directory)
        except Exception:  # noqa: BLE001
            continue

        for name in entries:
            if not name.lower().endswith(BOOK_EXTS):
                continue

            remote = f"{directory}/{name}"
            meta = await _book_metadata(fs, remote, name)
            books.append(
                Book(
                    name=_text(meta.get("name")) or Path(name).stem,
                    artist=_text(meta.get("artist")),
                    genre=_text(meta.get("genre")),
                    path=remote,
                    extension=Path(name).suffix.lstrip("."),
                    mime_type=_text(meta.get("mime_type")),
                    is_protected=_bool(meta.get("is_protected")),
                    is_pdf=name.lower().endswith(".pdf"),
                    source="scan",
                )
            )

    return books


async def _book_metadata(
    fs: Afc,
    remote: str,
    name: str,
) -> dict[str, Any]:
    """Best-effort metadata for a scanned book entry."""
    if await fs.isdir(remote):
        return await bundle_metadata(fs, remote)
    if name.lower().endswith((".epub", ".ibooks")):
        try:
            return epub_metadata(await fs.read(remote))
        except Exception:  # noqa: BLE001
            return {}
    return {}


async def bundle_metadata(fs: Afc, bundle: str) -> dict[str, Any]:
    """Read ``iTunesMetadata.plist`` from an unzipped book bundle."""
    path = f"{bundle}/iTunesMetadata.plist"
    if not await fs.exists(path):
        return {}
    try:
        record = plistlib.loads(await fs.read(path))
    except Exception:  # noqa: BLE001
        return {}
    return _record_metadata(record) if isinstance(record, dict) else {}


# ---------------------------------------------------------------------------
# EPUB metadata
# ---------------------------------------------------------------------------


def epub_metadata(payload: bytes) -> dict[str, Any]:
    """Extract title/author metadata from an EPUB or .ibooks archive."""
    meta: dict[str, Any] = {}

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())

        opf_path = _opf_path(archive, names)
        if opf_path:
            root = ET.fromstring(archive.read(opf_path))
            meta["name"] = _xml_text(root, "title")
            meta["artist"] = _xml_text(root, "creator")
            meta["language"] = _xml_text(root, "language")
            meta["identifier"] = _xml_text(root, "identifier")

        if "iTunesMetadata.plist" in names:
            try:
                record = plistlib.loads(archive.read("iTunesMetadata.plist"))
            except Exception:  # noqa: BLE001
                record = None
            if isinstance(record, dict):
                meta.update(_record_metadata(record))

    return {key: value for key, value in meta.items() if value}


def _record_metadata(record: dict) -> dict[str, Any]:
    """Map an iTunesMetadata.plist record to our field names."""
    if isinstance(record.get("item"), dict):
        record = record["item"]

    meta: dict[str, Any] = {}
    for key, field_name in (
        ("Name", "name"),
        ("Artist", "artist"),
        ("Genre", "genre"),
        ("MimeType", "mime_type"),
        ("Extension", "extension"),
    ):
        value = _get(record, key)
        if value:
            meta[field_name] = str(value)

    protected = _get(record, "IsProtected")
    if protected is None:
        meta["is_protected"] = _bool(protected)
    return meta


def _opf_path(
    archive: zipfile.ZipFile,
    names: set[str],
) -> str | None:
    try:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        for element in container.iter():
            if element.tag.rsplit("}", 1)[-1] == "rootfile":
                full_path = element.get("full-path")
                if full_path and full_path in names:
                    return full_path
    except Exception:  # noqa: BLE001
        pass

    for name in names:
        if name.lower().endswith(".opf"):
            return name
    return None


def _xml_text(root: ET.Element, local_name: str) -> str:
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() != local_name.lower():
            continue
        if element.text and element.text.strip():
            return element.text.strip()
    return ""


# ---------------------------------------------------------------------------
# Path handling
# ---------------------------------------------------------------------------


def sanitize_filename(name: str, max_len: int = 180) -> str:
    """Make a book title safe as a local filename."""
    cleaned = _INVALID_CHARS.sub("_", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned[:max_len].strip()


def remote_candidates(path: str, source: str = "") -> list[str]:
    """Candidate AFC paths for a catalog Path value, best first.

    Purchases plists store bare filenames relative to
    ``Books/Purchases/``, while sideloaded entries are relative to
    ``Books/``.
    """
    remote = str(path).strip()
    lowered = remote.lower()
    for prefix in MEDIA_PREFIXES:
        if lowered.startswith(prefix):
            remote = remote[len(prefix) :]
            break
    relative = remote.lstrip("/")

    if relative.lower().startswith("books/"):
        candidates = [relative]
    elif relative.lower().startswith("purchases/"):
        candidates = [f"Books/{relative}"]
    else:
        candidates = []
        if "purchases" in source.lower():
            candidates.append(f"Books/Purchases/{relative}")
        candidates.append(f"Books/{relative}")

    candidates.append(relative)

    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def normalize_remote(path: str, source: str = "") -> str:
    """Best-guess AFC path for a catalog Path value."""
    candidates = remote_candidates(path, source)
    return candidates[0] if candidates else ""


async def resolve_book_path(fs: Afc, book: Book) -> None:
    """Point ``book.path`` at the first candidate that exists."""
    if not book.path:
        return
    for candidate in remote_candidates(book.path, book.source):
        if await fs.exists(candidate):
            book.path = candidate
            return


def unique_destination(
    path: Path,
    used: set[str],
    overwrite: bool,
) -> Path:
    """Pick a non-colliding destination filename."""
    if overwrite:
        used.add(path.name.lower())
        return path

    candidate = path
    counter = 2
    while candidate.exists() or candidate.name.lower() in used:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        counter += 1
    used.add(candidate.name.lower())
    return candidate


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


async def export_books(
    fs: Afc,
    books: Iterable[Book],
    outdir: Path,
    *,
    skip_drm: bool = False,
    dry_run: bool = False,
    overwrite: bool = False,
    log: Callable[..., None] = print,
) -> tuple[int, int, int]:
    """Copy books to ``outdir``. Return ``(exported, skipped, failed)``."""
    outdir.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    exported = skipped = failed = 0

    for book in books:
        label = book.name or book.path or "(unnamed)"

        if book.path:
            log(f"skip  {label}: no Path in catalog entry")
            skipped += 1
            continue

        if skip_drm and book.is_protected:
            log(f"skip  {label}: DRM-protected")
            skipped += 1
            continue

        candidates = remote_candidates(book.path, book.source)
        destination = unique_destination(
            outdir / book.filename, used, overwrite
        )

        if dry_run:
            shown = candidates[0]
            for candidate in candidates:
                if await fs.exists(candidate):
                    shown = candidate
                    break
            kind = "bundle" if await fs.isdir(shown) else "file"
            log(f"would export {shown} ({kind}) -> {destination}")
            exported += 1
            continue

        remote = candidates[0]
        try:
            for candidate in candidates:
                if await fs.exists(candidate):
                    remote = candidate
                    break
            size = await _export_one(fs, book, remote, destination)
        except Exception as exc:  # noqa: BLE001 - keep going per book
            log(f"FAIL  {label} ({remote}): {exc}")
            failed += 1
            continue

        log(f"ok    {label} -> {destination.name} ({size:,} bytes)")
        exported += 1

    return exported, skipped, failed


async def _export_one(
    fs: Afc,
    book: Book,
    remote: str,
    destination: Path,
) -> int:
    """Write a single book and return the number of bytes written."""
    if await fs.isdir(remote):
        if book.is_pdf or destination.suffix.lower() == ".pdf":
            return await export_pdf_bundle(fs, remote, destination)
        return await write_bundle_archive(fs, remote, destination)

    payload = await fs.read(remote)
    destination.write_bytes(payload)
    return len(payload)


async def collect_files(
    fs: Afc,
    root: str,
    prefix: str = "",
) -> list[str]:
    """Recursively list files below ``root``, relative to it."""
    files: list[str] = []
    try:
        names = await fs.listdir(root)
    except Exception:  # noqa: BLE001
        return files

    for name in names:
        relative = f"{prefix}{name}"
        full = f"{root}/{name}"
        if await fs.isdir(full):
            files.extend(await collect_files(fs, full, f"{relative}/"))
        else:
            files.append(relative)
    return files


async def export_pdf_bundle(
    fs: Afc,
    bundle: str,
    destination: Path,
) -> int:
    raise NotImplementedError


async def write_bundle_archive(
    fs: Afc,
    bundle: str,
    destination: Path,
) -> int:
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Local sources (iCloud Drive / Files sync)
# ---------------------------------------------------------------------------


def icloud_root_candidates() -> list[Path]:
    """Per-platform default locations for a synced iCloud Drive folder."""
    home = Path.home()
    candidates: list[Path]

    if IS_MACOS:
        candidates = [
            home / "Library/Mobile Documents/com~apple~CloudDocs",
            home / "Library/CloudStorage/iCloud Drive",
        ]
    elif IS_WINDOWS:
        profile = Path(os.environ.get("USERPROFILE", home))
        candidates = [
            home / "iCloudDrive",
            profile / "iCloudDrive",
            home / "Apple" / "iCloudDrive",
        ]
    else:
        # Linux has no native iCloud client; users typically mount one.
        candidates = [
            home / "iCloudDrive",
            home / "iCloud",
        ]

    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def default_icloud_root() -> Path | None:
    raise NotImplementedError


def icloud_missing_hint() -> str:
    """Platform-specific guidance when no iCloud Drive folder is found."""
    if IS_MACOS:
        return (
            "enable iCloud Drive in System Settings, "
            "or pass --root /path/to/CloudDocs"
        )
    if IS_WINDOWS:
        return (
            "install iCloud for Windows, "
            "or pass --root C:\\Users\\you\\iCloudDrive"
        )
    return (
        "iCloud Drive is not native on Linux; mount it "
        "(for example with rclone) and pass --root"
    )


def iter_book_files(
    root: Path,
    extensions: Iterable[str] = BOOK_EXTS,
) -> list[Path]:
    """Book-like files under ``root``, skipping hidden directories."""
    wanted = tuple(extension.lower() for extension in extensions)
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".")
        )
        for name in sorted(filenames):
            if name.lower().endswith(wanted):
                found.append(Path(dirpath) / name)
    return found


def spotlight_book_files(
    root: Path,
    extensions: Iterable[str] = BOOK_EXTS,
) -> list[Path] | None:
    """Find book files via Spotlight; ``None`` if the index is unavailable."""
    if not IS_MACOS or shutil.which("mdfind") is None:
        return None

    wanted = tuple(extension.lower() for extension in extensions)
    found: set[Path] = set()
    for extension in wanted:
        query = f'kMDItemFSName == "*{extension}"c'
        try:
            result = subprocess.run(
                ["mdfind", "-onlyin", str(root), query],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            candidate = Path(line)
            if candidate.suffix.lower() in wanted:
                found.add(candidate)
    return sorted(found)


def find_book_files(
    root: Path,
    extensions: Iterable[str] = BOOK_EXTS,
    *,
    use_spotlight: bool = True,
) -> list[Path]:
    raise NotImplementedError


def copy_tree_files(
    root: Path,
    files: Iterable[Path],
    outdir: Path,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
    log: Callable[..., None] = print,
) -> tuple[int, int, int]:
    """Copy ``files`` under ``root`` into ``outdir``."""
    copied = skipped = failed = 0
    for source in files:
        relative = source.relative_to(root)
        destination = outdir / relative

        if destination.exists() and not overwrite:
            log(f"skip  {relative}: exists")
            skipped += 1
            continue

        if dry_run:
            log(f"would copy {relative}")
            copied += 1
            continue

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        except OSError as exc:
            log(f"FAIL  {relative}: {exc}")
            failed += 1
            continue

        log(f"ok    {relative} ({source.stat().st_size:,} bytes)")
        copied += 1
    return copied, skipped, failed


async def cmd_icloud(args: argparse.Namespace) -> int:
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Device backup (reaches HomeDomain / app containers that AFC cannot)
# ---------------------------------------------------------------------------


def backup_selection_rules() -> list:
    """Backup rules for the Books library, its container, and iCloud Drive."""
    from pymobiledevice3.services.mobilebackup2 import BackupSelectionRule

    return [
        BackupSelectionRule("HomeDomain", "Library/Mobile Documents", True),
        BackupSelectionRule("AppDomain-com.apple.iBooks", "Documents", True),
        BackupSelectionRule("AppDomain-com.apple.iBooks", "Library", True),
        BackupSelectionRule("MediaDomain", "Books", True),
    ]


async def cmd_backup(args: argparse.Namespace) -> int:
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Device access (lazy import keeps the parsers usable offline)
# ---------------------------------------------------------------------------


class Afc:
    """Thin, testable wrapper over pymobiledevice3's async AfcService."""

    def __init__(self, service: Any):
        self._service = service

    async def exists(self, path: str) -> bool:
        try:
            return bool(await self._service.exists(path))
        except Exception:  # noqa: BLE001
            return False

    async def read(self, path: str) -> bytes:
        return bytes(await self._service.get_file_contents(path))

    async def listdir(self, path: str) -> list[str]:
        return [str(entry) for entry in await self._service.listdir(path)]

    async def stat(self, path: str) -> dict:
        return await self._service.stat(path)

    async def isdir(self, path: str) -> bool:
        try:
            info = await self._service.stat(path)
        except Exception:  # noqa: BLE001
            return False
        return info["st_ifmt"] == "S_IFDIR"

    async def close(self) -> None:
        with suppress(Exception):
            await self._service.aclose()


@asynccontextmanager
async def open_device(
    udid: str | None = None,
) -> AsyncIterator[Afc]:
    raise NotImplementedError


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_list(books: list[Book], as_json: bool) -> int:
    raise NotImplementedError


async def cmd_export(
    fs: Afc,
    books: list[Book],
    args: argparse.Namespace,
) -> int:
    raise NotImplementedError


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="books-downloader",
        description=(
            "List and export Books/iBooks content from an iOS device "
            "(read-only)."
        ),
    )
    parser.add_argument(
        "-u",
        "--udid",
        help="device UDID (default: first connected device)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list", help="list books on the device"
    )
    list_parser.add_argument(
        "--json",
        action="store_true",
        help="machine-readable output",
    )
    list_parser.add_argument(
        "--no-scan",
        action="store_true",
        help="do not scan the books directories when the plists are empty",
    )

    export_parser = subparsers.add_parser(
        "export", help="download books to this computer"
    )
    export_parser.add_argument(
        "-o",
        "--output",
        default="books-export",
        help="destination directory (default: books-export)",
    )
    export_parser.add_argument(
        "--skip-drm",
        action="store_true",
        help="do not download DRM-protected files",
    )
    export_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be copied",
    )
    export_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite existing files",
    )
    export_parser.add_argument(
        "--no-scan",
        action="store_true",
        help="do not scan the books directories when the plists are empty",
    )

    icloud_parser = subparsers.add_parser(
        "icloud",
        help="copy books from this Mac's synced iCloud Drive",
    )
    icloud_parser.add_argument(
        "-o",
        "--output",
        default="books-export",
        help="destination directory (default: books-export)",
    )
    icloud_parser.add_argument(
        "--pdf",
        action="store_true",
        help="only PDF files (default: PDF, EPUB and .ibooks)",
    )
    icloud_parser.add_argument(
        "--root",
        help="iCloud Drive folder (default: auto-detect)",
    )
    icloud_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would be copied",
    )
    icloud_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite existing files",
    )
    icloud_parser.add_argument(
        "--scan",
        action="store_true",
        help="walk the filesystem instead of using the Spotlight index",
    )

    backup_parser = subparsers.add_parser(
        "backup",
        help="extract books from a filtered device backup",
    )
    backup_parser.add_argument(
        "-o",
        "--output",
        default="books-export",
        help="destination directory (default: books-export)",
    )
    backup_parser.add_argument(
        "--backup-dir",
        default=".device-backup",
        help="where to keep the raw backup (default: .device-backup)",
    )
    backup_parser.add_argument(
        "--password",
        default="",
        help="backup password, if the device encrypts backups",
    )
    backup_parser.add_argument(
        "--full",
        action="store_true",
        help="force a full backup instead of an incremental one",
    )
    backup_parser.add_argument(
        "--pdf",
        action="store_true",
        help="only PDF files (default: PDF, EPUB and .ibooks)",
    )
    backup_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite existing files",
    )
    backup_parser.add_argument(
        "--clean",
        action="store_true",
        help="delete the raw backup after extraction",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError
