# METTLER TOLEDO IND231 / IND236 – Serial Test Script

A small Python script that checks communication with an IND231/IND236 weighing
terminal over **USB** (virtual COM port) or **RS-232**. It supports the **MT-SICS**
protocol (the default) and **Toledo Continuous** output. It only tests communication. There is no API, database or web interface.

Protocol reference: *METTLER TOLEDO IND231/IND236 User's Guide*,
30094013 R06 (11/2023), **Appendix B.5 – Standard Interface Command Set (SICS)**,
**Appendix B.3 – Continuous Output Mode** and **Section 3.7 – F4 Communication**.

> **SICS or Continuous?** Check the terminal's `F4.x.1` Connection Mode.
> - **SICS**: run the script normally. It sends `S` and reads the reply.
> - **Toledo Continuous**: run it with `--mode continuous`. The script only listens and sends nothing.
>   In this mode the terminal treats an `S` character as **"Switch units"** (CTPZ, manual B.4),
>   so sending `S` would change the display between kg and g.
>   If the script is started in SICS mode and receives Continuous frames, it warns
>   you and switches to listen-only mode automatically.

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
| `-a, --all` | off | Print every reading, for debugging. **Default (event mode):** print one line when an item settles on the scale, and one when the scale is empty again. |
| `--settle` | 5 (continuous) / 1 (SICS) | Number of identical stable readings needed before an item is reported |
| `--min-weight` | 0 | Readings at or below this value, in display units, count as "empty" |
| `-m, --mode` | sics | `sics` sends a command and reads the reply. `continuous` only listens to Toledo Continuous output and sends nothing. |
| `-c, --command` | S | SICS command per cycle. `S` = stable weight, `SI` = immediate weight |
| `-i, --interval` | 0.5 | Seconds between requests |
| `-t, --timeout` | 5 | Read timeout. `S` can take up to 3 s while the scale settles. |
| `--retry` | 3 | Seconds to wait before reconnecting |
| `--once` | | Send one request and exit (exit code 0 if a response was received) |
| `--no-raw` | | Hide the raw `TX:` / `RX:` debug lines |

### Example output

By default the script prints only when something happens: once when an item
has settled on the scale, and once when the scale is empty again.

```text
Connecting to scale on COM4 (9600 8N1, xonxoff=off, mode=continuous)...
Connected successfully!

Listening only (Toledo Continuous) - nothing is sent to the scale.

[13:14:07] Ready - scale empty (0.00 kg). Place an item...

  RX: b'\x02,0    270   000\r'
[13:14:12] ITEM  Weight: 2.70 kg
[13:14:20] Scale empty (0.00 kg). Waiting for next item...
```

Add `--all` to see every reading, together with the raw `TX:` and `RX:` bytes.

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

## 3c. Sending the weight to the Laravel webhook (optional)

The webhook is off by default. With `--webhook`, the script asks after each stable item weight:

1. Scan or type the tracking number (`MAILITM_FID`, e.g. `EE123456785KH`), or press Enter to skip.
2. Check the JSON body that is shown, then answer `y` to send.
3. The script shows the HTTP status and the API's response.

It reports **SUCCESS** only when the response is HTTP 2xx **and** the JSON contains `"success": true`.
If you send the same tracking number again in one session, you must type `again`.

**Setup:** copy `.env.example` to `.env`, next to `test_scale.py` or `test_scale.exe`:

```env
WEBHOOK_URL=https://dev.cambodiapost.com.kh/api/webhook/notify
WEBHOOK_SECRET=<same value as Laravel IPS_SIGN_SECRET>
WEBHOOK_TIMEOUT=10
```

`.env` is git-ignored, and the secret is never printed.

```bash
python test_scale.py --port COM4 --mode continuous --webhook-dry-run   # shows the request, never sends
python test_scale.py --port COM4 --mode continuous --webhook           # asks, then sends
```

**Request**

```
POST <WEBHOOK_URL>
Content-Type: application/json
Accept: application/json
signature: <lower-case hex HMAC-SHA256>

[{"MAILITM_FID":"EE123456785KH","WEIGHT":2.35,"WEIGHT_UNIT":"kg","SOURCE":"METTLER_TOLEDO_IND231"}]
```

**Signature** (matches Laravel `App\Http\Middleware\ValidateIPS`):
Laravel computes `hash_hmac('sha256', json_encode(json_decode($body, true)), IPS_SIGN_SECRET)`.
In other words, it signs PHP's *re-encoded* JSON, not the raw bytes. `webhook.py` therefore produces the body in
PHP `json_encode` format (compact, `/` escaped as `\/`). That makes the signed bytes, the sent bytes and PHP's re-encoding identical.
The secret is used as **literal text**, not hex-decoded.
A test runs the real PHP expression to confirm this.

**Tests** (offline, with no scale and no real server):

```bash
python -m unittest discover -s tests -v
```

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

### Toledo Continuous (manual Appendix B.3)

About 20 frames per second: `STX SWA SWB SWC WWWWWW TTTTTT CR [CHK]`

| Example frame (captured from a real IND231) | Decoded |
|---|---|
| `\x02,0    199   000\r` | 1.99 kg, stable |
| `\x02,8    037   000\r` | 0.37 kg, motion |
| `\x02) !  1990    00\r` | 1990 g, stable (unit switched to grams) |
| `\x02,> ...` | negative, motion, out of range |

- **SWA** bits 0–2 give the decimal point position.
- **SWB** bit 0 = net, bit 1 = negative, bit 2 = out of range, bit 3 = motion, bit 4 = kg (otherwise lb).
- **SWC** bits 0–2 give the unit (`001` = g, `011` = oz). Bit 4 = expanded ×10.

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
| `*** The terminal is sending Toledo Continuous frames ***` | The port is in Continuous mode. The script switches to listening automatically. Next time, use `--mode continuous`, or set `F4.x.1` to SICS. Check the display unit, because the `S` that was sent may have switched it. |
| Display unit keeps changing between kg and g | Something is sending `S` while the port is in Continuous mode. Use `--mode continuous`. |
| Port list shows "Intel(R) Active Management Technology – SOL" | That is a PC management port, not the scale. Choose the USB-Serial or Prolific port. |
