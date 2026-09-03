# Third-party notices

The code in this repository is licensed as described in `LICENSE` and `NOTICE`.
The release archives built by `.github/workflows/release-*.yml` additionally
bundle the third-party software listed below. Each bundled archive is
downloaded from the pinned URL and verified against the pinned SHA-256
**before** extraction or execution; the workflows fail closed on any mismatch.

## Bundled in the Windows archives

### Python (embeddable package), bundled as `PythonPortable/`

- Version: 3.14.7 (`python-3.14.7-embed-amd64.zip`)
- Source: <https://www.python.org/ftp/python/3.14.7/python-3.14.7-embed-amd64.zip>
- SHA-256: `d297e5ff019966817ad8502465176139f2d3d840fa4ed84b13bed399a6ab1f15`
- Copyright: © 2001 Python Software Foundation; © 1995-2001 Corporation for
  National Research Initiatives; © 1991-1995 Stichting Mathematisch Centrum
- License: Python Software Foundation License Version 2 (PSF-2.0).
  The full text is included in the archive as `PythonPortable/LICENSE.txt`.

### MinGit (Git for Windows), bundled as `PortableGit/`

- Version: 2.55.0.5 (`MinGit-2.55.0.5-64-bit.zip`, tag `v2.55.0.windows.5`)
- Source: <https://github.com/git-for-windows/git/releases/download/v2.55.0.windows.5/MinGit-2.55.0.5-64-bit.zip>
- SHA-256: `56d7b226b7693196cfc71fef26568f536c4a021ab6c37ff2db4287bed908e96e`
- Copyright: © Junio C Hamano and the Git project contributors;
  © the Git for Windows project contributors
- License: GNU General Public License version 2.0 (GPL-2.0-only).
  The full text is included in the archive as `PortableGit/LICENSE.txt`;
  licenses of components shipped inside MinGit are under
  `PortableGit/mingw64/share/licenses/`.
- Corresponding source: <https://github.com/git-for-windows/git>
  (tag `v2.55.0.windows.5`). The binaries are redistributed unmodified from
  the official Git for Windows release.

## macOS archives

The macOS archives bundle no third-party binaries (they run on the
system-provided `python3` and `git`). Only this repository's own files,
`LICENSE`, `NOTICE`, and this document are included.

## Updating the pinned versions

When bumping a bundled component, update the URL and SHA-256 both here and in
the workflow `env:` blocks, and record the verification (download + local
`shasum -a 256`, plus the upstream-published checksum when available) in the
pull request.
