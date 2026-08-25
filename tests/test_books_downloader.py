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
    ...


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
    ...
