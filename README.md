# METTLER TOLEDO IND231 / IND236 – Serial Test Script

A small Python script that checks communication with an IND231/IND236 weighing
terminal over **USB** (virtual COM port) or **RS-232**, using the **MT-SICS**
protocol. It only tests communication. There is no API, database or web interface.

Protocol reference: *METTLER TOLEDO IND231/IND236 User's Guide*,
30094013 R06 (11/2023), **Appendix B.5 – Standard Interface Command Set (SICS)**
and **Section 3.7 – F4 Communication**.

---

## 1. Configure the terminal first (important)

The factory default **Connection Mode is `Print`**. In that mode the terminal
ignores the `S` command and the script will only report timeouts.

On the terminal, open setup and set the port you are cabling to:

| Setting | Menu | Value |
|---|---|---|
| Connection Mode | `F4.1.1` (COM1), `F4.2.1` (COM2/USB option) | **SICS** |
| Baud rate | `F4.x.3.1` | 9600 (default) |
| Data bits / parity | `F4.x.3.2` | 8 none (default) |
| Flow control | `F4.x.3.3` | Off (default) |

The script's settings must match the terminal's settings (default **9600 8N1, no flow control**).

**Cabling**
- **USB**: the terminal's USB port is a UART-to-USB bridge, so it appears as a
  virtual COM port. Windows may need the driver from the METTLER TOLEDO
  Documentation & Utilities page or Resource CD.
- **RS-232 (COM1)**: use a USB-to-RS-232 adapter on the PC side if needed.
  TX/RX must be crossed (a null-modem cable, or crossed wiring per the manual's COM1 pin-out).

---

## 2. Install

Requires **Python 3.8+** and `pyserial`.

### macOS

```bash
cd script-python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows (PowerShell or CMD)

```bat
cd script-python
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

> If PowerShell blocks `activate`, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use CMD.

---

## 3. Run

Interactive port selection (lists the detected ports, then asks which one to use):

```bash
python test_scale.py          # macOS: python3 if not in the venv
```

Specify the port directly:

```bash
# macOS (use the /dev/cu.* device, not /dev/tty.*)
python test_scale.py --port /dev/cu.usbserial-110

# Windows
python test_scale.py --port COM3
```

Only list the ports:

```bash
python test_scale.py --list
```

Press **Ctrl+C** to stop.

### Options

| Option | Default | Meaning |
|---|---|---|
| `-p, --port` | (ask) | Serial port, e.g. `COM3`, `/dev/cu.usbserial-110` |
| `-b, --baud` | 9600 | Baud rate (1200 to 115200) |
| `--bytesize` | 8 | Data bits (7 or 8) |
| `--parity` | N | `N`, `E` or `O` |
| `--stopbits` | 1 | 1 or 2 |
| `--xonxoff` | off | Enable XON/XOFF flow control |
| `-c, --command` | S | SICS command per cycle. `S` = stable weight, `SI` = immediate weight |
| `-i, --interval` | 0.5 | Seconds between requests |
| `-t, --timeout` | 5 | Read timeout. `S` can take up to 3 s while the scale settles. |
| `--retry` | 3 | Seconds to wait before reconnecting |
| `--once` | | Send one request and exit (exit code 0 if a response was received) |
| `--no-raw` | | Hide the raw `TX:` / `RX:` debug lines |

### Example output

```text
Available ports:
1. /dev/cu.usbserial-110

Use /dev/cu.usbserial-110? [Y/n]:

Connecting to scale on /dev/cu.usbserial-110 (9600 8N1, xonxoff=off)...
Connected successfully!

  TX: b'S\r\n'
  RX: b'S S         0.00 kg\r\n'
Weight: 0.00 kg
  TX: b'S\r\n'
  RX: b'S S         1.25 kg\r\n'
Weight: 1.25 kg
```

---

## 3b. Standalone executable (no Python on the test PC)

PyInstaller packs the script and Python into one file. It **cannot
cross-compile**, so build on the same OS (and CPU type) as the test machine.

**Windows `.exe` – download a ready build (no Python needed anywhere)**

GitHub Actions builds `test_scale.exe` on every push to `main`
(workflow: `.github/workflows/build-windows.yml`).

1. Open the repository on GitHub, go to **Actions → Build Windows exe**, and open the latest green run.
2. Under **Artifacts**, download **test_scale-windows**. It downloads as a `.zip`; extract `test_scale.exe` from it.
   (You need to be signed in to GitHub to download artifacts. They are kept for 90 days.)

To publish a permanent download, push a version tag. The exe will then be attached to a GitHub **Release**:

```bash
git tag v1.0.0
git push origin v1.0.0
```

**Windows `.exe` – build it yourself** (on any Windows PC that has Python installed):

```bat
build_windows.bat
```

This produces `dist\test_scale.exe`, about 8 MB. Copy only that file to the test PC, which does not need Python.
Run it from CMD (`test_scale.exe --port COM3`), or double-click it to pick the port
interactively. When started this way, the window waits for Enter before it closes.

**macOS**:

```bash
./build_macos.sh
```

This produces `dist/test_scale`. Apple Silicon builds don't run on Intel Macs, and Intel builds don't run on Apple Silicon.
If macOS blocks a copied binary, run `xattr -d com.apple.quarantine test_scale`.

Notes:
- Windows Defender/SmartScreen sometimes flags unsigned PyInstaller executables.
  Choose "More info → Run anyway", or build on the test PC itself.
- The test PC still needs the USB-serial driver for the scale or adapter.

---

## 4. Protocol summary (manual Appendix B.5)

Commands are upper-case ASCII and end with `CR LF`. The script sends `S\r\n`.

| Response | Meaning | Script prints |
|---|---|---|
| `S S      0.256 kg` | Stable weight | `Weight: 0.256 kg` |
| `S D      0.256 kg` | Dynamic weight (only from `SI`/`SIR`) | `Weight: 0.256 kg  (dynamic)` |
| `S I` | Not executable now. With `S`, the scale did not settle within 3 s. | busy/motion message |
| `S +` / `S -` | Overload / underload | message |
| `ES` / `EL` | Syntax error / logical error | message |
| anything else | | `Unrecognised response: ...` (the raw line is still shown) |

The weight field is right-aligned. It has a 10-character sign+number, followed by the unit.

---

## 5. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Timeout: no response` repeatedly | Connection Mode is not SICS. Baud rate or parity does not match. Wrong port. RS-232 TX/RX not crossed. |
| Garbled `RX:` bytes | Baud rate or data bits/parity mismatch |
| `ES` | The command was not recognised. Check that it is upper-case. |
| Constant `S I` | Scale is in motion (vibration, draught) or not ready. Try `--command SI`. |
| `Could not open port` | Wrong name, port in use by another program, or (Windows) driver missing. On macOS, check that the device appears under `/dev/cu.*`. |
| Port disappears | USB unplugged. The script retries every `--retry` seconds until Ctrl+C. |
| Data arrives without sending anything | The port is in Auto Print or Toledo Continuous mode. Set it to SICS. |
