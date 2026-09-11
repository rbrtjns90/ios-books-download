"""Tests for books_downloader (no device required)."""

from __future__ import annotations

import io
import plistlib
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import books_downloader as bd  # noqa: E402


CONTAINER_XML = (
    '<?xml version="1.0"?>\n'
    '<container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    "  <rootfiles>\n"
    '    <rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/>\n'
    "  </rootfiles>\n"
    "</container>\n"
)

OPF_XML = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <metadata>
    <dc:title>The OPF Title</dc:title>
    <dc:creator>The OPF Author</dc:creator>
    <dc:language>en</dc:language>
  </metadata>
</package>
"""


def make_epub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        archive.writestr("OEBPS/content.opf", OPF_XML)
        archive.writestr(
            "iTunesMetadata.plist",
            plistlib.dumps(
                {
                    "Name": "iTunes Title",
                    "Artist": "iTunes Author",
                    "IsProtected": False,
                }
            ),
        )
    return buf.getvalue()


class FakeFS:
    """Async stand-in for the AFC wrapper."""

    def __init__(
        self, files: dict[str, bytes], dirs: dict[str, list[str]] | None = None
    ):
        self.files = files
        self.dirs = dirs or {}

    async def exists(self, path: str) -> bool:
        return path in self.files or path in self.dirs

    async def read(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def listdir(self, path: str) -> list[str]:
        return self.dirs.get(path, [])

    async def isdir(self, path: str) -> bool:
        return path in self.dirs

    async def stat(self, path: str) -> dict:
        return {"st_size": len(self.files.get(path, b""))}


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_array_root(self):
        payload = plistlib.dumps(
            [{"Name": "Dune", "Artist": "Herbert", "Path": "Books/dune.epub"}]
        )
        books = bd.books_from_plist(payload, "Books/Books.plist")
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0].name, "Dune")
        self.assertEqual(books[0].artist, "Herbert")
        self.assertEqual(books[0].filename, "Dune.epub")

    async def test_dict_root_and_case_insensitive_keys(self):
        payload = plistlib.dumps(
            {
                "Books": [
                    {
                        "name": "Lower",
                        "AUTHOR": "Someone",
                        "path": "Books/l.epub",
                    }
                ]
            }
        )
        books = bd.books_from_plist(payload)
        self.assertEqual(books[0].name, "Lower")
        self.assertEqual(books[0].artist, "Someone")

    async def test_bool_and_int_coercion(self):
        payload = plistlib.dumps(
            {
                "Books": [
                    {
                        "Name": "X",
                        "IsProtected": 1,
                        "IsPDF": "true",
                        "UniqueID": 42,
                    }
                ]
            }
        )
        book = bd.books_from_plist(payload)[0]
        self.assertTrue(book.is_protected)
        self.assertTrue(book.is_pdf)
        self.assertEqual(book.unique_id, 42)

    async def test_unknown_wrapper_list(self):
        payload = plistlib.dumps({"SomeKey": [{"Name": "Y"}]})
        self.assertEqual(bd.books_from_plist(payload)[0].name, "Y")


class EpubTests(unittest.TestCase):
    ...

    def test_opf_fallback_without_itunes_file(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", OPF_XML)
        meta = bd.epub_metadata(buf.getvalue())
        self.assertEqual(meta["name"], "The OPF Title")
        self.assertEqual(meta["artist"], "The OPF Author")

    def test_garbage_is_rejected(self):
        with self.assertRaises(zipfile.BadZipFile):
            bd.epub_metadata(b"not a zip")


class PathTests(unittest.TestCase):
    ...

    def test_purchases_candidates_prefer_purchases_dir(self):
        source = "Books/Purchases/Purchases.plist"
        self.assertEqual(
            bd.remote_candidates("x.epub", source)[0], "Books/Purchases/x.epub"
        )
        self.assertIn("Books/x.epub", bd.remote_candidates("x.epub", source))

    def test_sanitize(self):
        self.assertEqual(bd.sanitize_filename('a/b:c*d?"e'), "a_b_c_d__e")
        self.assertEqual(bd.sanitize_filename("   "), "")

    def test_unique_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.epub"
            used: set[str] = set()
            first = bd.unique_destination(target, used, overwrite=False)
            second = bd.unique_destination(target, used, overwrite=False)
            self.assertEqual(first.name, "x.epub")
            self.assertEqual(second.name, "x (2).epub")


class ExportTests(unittest.IsolatedAsyncioTestCase):
    ...


class LocalSourceTests(unittest.TestCase):
    def test_iter_book_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            (root / ".hidden").mkdir()
            (root / "a.pdf").write_bytes(b"%PDF")
            (root / "sub" / "b.epub").write_bytes(b"EPUB")
            (root / ".hidden" / "c.pdf").write_bytes(b"%PDF")
            (root / "notes.txt").write_text("x")

            files = bd.iter_book_files(root)
            names = sorted(p.relative_to(root).as_posix() for p in files)
            self.assertEqual(names, ["a.pdf", "sub/b.epub"])

            pdfs = bd.iter_book_files(root, (".pdf",))
            self.assertEqual([p.name for p in pdfs], ["a.pdf"])

    def test_copy_tree_files_preserves_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            (root / "sub").mkdir(parents=True)
            (root / "a.pdf").write_bytes(b"A")
            (root / "sub" / "b.pdf").write_bytes(b"B")
            out = Path(tmp) / "out"

            copied, skipped, failed = bd.copy_tree_files(
                root, bd.iter_book_files(root), out, log=lambda *_: None
            )
            self.assertEqual((copied, skipped, failed), (2, 0, 0))
            self.assertEqual((out / "a.pdf").read_bytes(), b"A")
            self.assertEqual((out / "sub" / "b.pdf").read_bytes(), b"B")

    def test_copy_tree_files_skips_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            (root / "a.pdf").write_bytes(b"new")
            out = Path(tmp) / "out"
            out.mkdir()
            (out / "a.pdf").write_bytes(b"old")

            copied, skipped, _ = bd.copy_tree_files(
                root, bd.iter_book_files(root), out, log=lambda *_: None
            )
            self.assertEqual((copied, skipped), (0, 1))
            self.assertEqual((out / "a.pdf").read_bytes(), b"old")

            bd.copy_tree_files(
                root,
                bd.iter_book_files(root),
                out,
                overwrite=True,
                log=lambda *_: None,
            )
            self.assertEqual((out / "a.pdf").read_bytes(), b"new")

    def test_icloud_root_candidates(self):
        candidates = bd.icloud_root_candidates()
        self.assertTrue(candidates)
        self.assertTrue(all(isinstance(path, Path) for path in candidates))
        self.assertEqual(len(candidates), len(set(candidates)))

    def test_icloud_candidates_per_platform(self):
        with mock.patch.object(bd, "IS_MACOS", True), mock.patch.object(
            bd, "IS_WINDOWS", False
        ):
            mac = bd.icloud_root_candidates()
        self.assertTrue(any("Mobile Documents" in str(p) for p in mac))

        with mock.patch.object(bd, "IS_MACOS", False), mock.patch.object(
            bd, "IS_WINDOWS", True
        ):
            windows = bd.icloud_root_candidates()
            hint = bd.icloud_missing_hint()
        self.assertTrue(any(p.name == "iCloudDrive" for p in windows))
        self.assertIn("iCloud for Windows", hint)

        with mock.patch.object(bd, "IS_MACOS", False), mock.patch.object(
            bd, "IS_WINDOWS", False
        ):
            linux = bd.icloud_root_candidates()
        self.assertTrue(linux)
