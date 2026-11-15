# ios-books-download

Read-only exporter for the books stored on an iOS device, based on the
storage layout iOS uses for its Books library. Runs on macOS, Linux and
Windows.

iOS stores two plists on the device media partition:

| Path | Contents |
|---|---|
| `Books/Books.plist` | sideloaded / imported books |
| `Books/Purchases/Purchases.plist` | purchased (FairPlay) books |

Each record carries `Name`, `Artist`, `Genre`, `Path`, `Extension`,
`IsProtected`, `IsPDF`, `PackageHash`, etc. This tool reads those plists
over AFC
(`com.apple.afc`, root `/var/mobile/Media`), falls back to scanning `Books/`
when they are empty, and copies the files to your computer.

It never writes to the device and never decrypts DRM-protected books.

## Requirements

- macOS, Linux or Windows
- Python 3.9+ (`python3` on macOS/Linux, `python` on Windows)
- `usbmuxd`: built in on macOS, the `usbmuxd` package on Linux, and
  iTunes / Apple Mobile Device Support (or `usbmuxd`) on Windows
- A trusted, unlocked iOS device

```sh
python3 -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Usage

```sh
# What is on the device?
python3 books_downloader.py list

# Machine-readable
python3 books_downloader.py list --json

# Download everything to books-export/
python3 books_downloader.py export -o books-export

# Preview, skip DRM-protected files
python3 books_downloader.py export -o books-export --dry-run --skip-drm

# Pick a specific device
python3 books_downloader.py list -u <UDID>
```

`export` prints one line per book and a summary. Existing files get a
` (2)`, ` (3)`, … suffix unless `--overwrite` is given. Exit code is non-zero
if any file failed.

## iCloud Drive mode (local folder)

PDFs you saved in Files/iCloud Drive are not part of the Books library and
are not reachable over AFC. Because iCloud Drive syncs to your computer,
copy them from there instead:

```sh
python3 books_downloader.py icloud --pdf -o PDFs
python3 books_downloader.py icloud --pdf --dry-run
```

By default it takes PDF, EPUB and `.ibooks`; `--pdf` restricts to PDFs.
Relative paths are preserved and hidden folders are skipped. `--root`
overrides auto-detection (`~/Library/Mobile Documents/com~apple~CloudDocs`).

Locations are detected per platform: macOS checks
`~/Library/Mobile Documents/com~apple~CloudDocs` (and `CloudStorage`),
Windows checks `%USERPROFILE%\iCloudDrive`, and Linux checks
`~/iCloudDrive` / `~/iCloud` (iCloud has to be mounted there, e.g. with
rclone). Pass `--root` if yours is elsewhere.

On macOS the Spotlight index is used and returns in under a second. On
Windows and Linux it falls back to a filesystem walk, which can be slow on
a large tree; `--scan` forces the walk on macOS too.

## Backup mode (phone-side)

For data AFC cannot reach — the Books app container and the iCloud Drive
cache — run a filtered backup that keeps only the Books app domain, Books
media, and `Library/Mobile Documents`:

```sh
python3 books_downloader.py backup --pdf -o PDFs
```

Caveats: the first backup is full (so allow time), the device must stay
unlocked, and iOS may exclude iCloud Drive content from backups by design.
`--backup-dir` sets where the raw backup lives, `--password` is required for
encrypted backups, and `--clean` removes the raw backup afterwards.

## Notes

- **Plists first, scan second.** If both plists are missing or empty (some iOS
  versions), the tool lists `Books/` and pulls title/author from each EPUB's
  `META-INF/container.xml` → OPF and `iTunesMetadata.plist`.
- **DRM.** Purchased books are flagged `IsProtected`; they are copied as
  encrypted FairPlay containers and cannot be opened outside Apple's readers.
  Use `--skip-drm` to leave them out.
- **Purchased books are bundle directories.** On recent iOS versions a
  purchase lives at `Books/Purchases/<id>.epub/` as an *unzipped* EPUB
  (plus `iTunesMetadata.plist` and artwork). The exporter repackages those
  into a normal `.epub` zip, with `mimetype` first and uncompressed as the
  format requires.
- **PDFs are supported too.** A plain `.pdf` entry is copied byte-for-byte.
  If iOS stores a PDF as a bundle directory, the exporter pulls the inner
  PDF out (largest match if several are present).
- **Filenames** come from the book's `Name` field, sanitised for the local
  filesystem.
- **No writing.** This tool is read-only. Importing books requires Apple's
  AirTraffic sync protocol, which is not implemented here.

