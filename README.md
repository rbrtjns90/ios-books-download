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

