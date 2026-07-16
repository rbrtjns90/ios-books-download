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
    raise NotImplementedError


class FakeFS:
    ...


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    ...


class EpubTests(unittest.TestCase):
    ...


class PathTests(unittest.TestCase):
    ...


class ExportTests(unittest.IsolatedAsyncioTestCase):
    ...


class LocalSourceTests(unittest.TestCase):
    ...
