# Build `fm_dab_sweep` for Windows

This folder contains a build script that turns `fm_dab_sweep.py` into a standalone Windows executable using [PyInstaller](https://pyinstaller.org/).

## Prerequisites

1. **Python 3.9 or newer** installed on the Windows build machine.
2. The required Python packages already installed:
   ```powershell
   pip install numpy pyrtlsdr==0.2.93 sqlalchemy psycopg2-binary
   ```
3. The native RTL-SDR DLLs in the same folder as this script:
   - `librtlsdr.dll`
   - `libusb-1.0.dll`

   If you do not have them, download the 64-bit Windows binaries from the
   [RTL-SDR Blog releases](https://github.com/rtlsdrblog/rtl-sdr-blog/releases)
   and the [libusb releases](https://github.com/libusb/libusb/releases), then
   place the two DLLs next to `fm_dab_sweep.py`.

## Build

Open PowerShell in this project folder and run:

```powershell
python build_fm_dab_sweep.py
```

The script will:

1. Check that the native DLLs are present.
2. Install PyInstaller if it is missing.
3. Build a one-directory bundle in `dist/fm_dab_sweep/`.
4. Bundle the DLLs so the executable can find them on any PC.

## Output

After the build finishes, you can run the executable directly:

```powershell
.\dist\fm_dab_sweep\fm_dab_sweep.exe
```

To deploy on another PC, zip the entire `dist/fm_dab_sweep/` folder and extract it there. No Python installation is needed on the target PC.

## Important compatibility notes

- **Build on the oldest Windows you need to support.** A PyInstaller executable
  built on Windows 11 may not run on Windows 7 or 8.1. For broad compatibility,
  build on Windows 10 or older.
- **Use 64-bit DLLs.** Python 3.11 is 64-bit, so the bundled DLLs must also be
  64-bit.
- **Visual C++ runtime.** If the target PC complains about `MSVCR100.dll` or
  similar, install the [Microsoft Visual C++ 2010 Redistributable (x64)](https://www.microsoft.com/en-us/download/details.aspx?id=14632).
- **Zadig driver.** The RTL-SDR device itself still needs the WinUSB/libusbK
  driver installed via [Zadig](https://zadig.akeo.ie/) on the target PC.

## Lightweight deployment tips

- The one-directory build is faster and lighter than a single-file `.exe`.
- If you need to make the package even smaller, you can delete unnecessary
  files from `dist/fm_dab_sweep/` (for example, unused translations or large
  optional binaries). However, do not delete Python core files or the DLLs.
