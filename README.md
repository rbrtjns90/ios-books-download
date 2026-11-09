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

