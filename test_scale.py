#!/usr/bin/env python3
"""
Test communication with a METTLER TOLEDO IND231/IND236 terminal over
USB (virtual COM port) or RS-232.

Protocol reference: METTLER TOLEDO IND231/IND236 User's Guide
(30094013 R06, 11/2023), Appendix B.

Two terminal port modes are supported (setup F4.x.1 Connection Mode):

1. SICS (B.5) - the PC asks, the terminal answers:
     Command  : "S" + CR LF        -> send stable weight value
     Responses: "S S     0.256 kg"  stable weight
                "S I"               not executable now (no stable weight in 3 s)
                "S +" / "S -"       overload / underload
                "ES" / "EL"         syntax error / logical error

2. Toledo Continuous (B.3) - the terminal sends ~20 frames/s by itself:
     STX SWA SWB SWC WWWWWW TTTTTT CR [CHK]
     SWA = decimal point, SWB = sign/motion/net/kg, SWC = unit (g/oz) etc.
   In this mode the port also accepts CTPZ characters, where a single "S"
   means "Switch units". So in Continuous mode this script sends NOTHING.

If the script is started in SICS mode but receives Continuous frames, it
warns and switches to listen-only Continuous mode automatically.

Only dependency: pyserial.  Stop with Ctrl+C.
"""

import argparse
import re
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("pyserial is not installed. Run:  pip install -r requirements.txt")
    sys.exit(1)


STX = b"\x02"

# ---------------------------------------------------------------- SICS ---

# "S S      0.256 kg"  (ID, status, weight right-justified, unit)
WEIGHT_RE = re.compile(r"^S\s+([SD])\s+([+-]?\s*\d+(?:\.\d+)?)\s+(\S+)$")

STATUS_MESSAGES = {
    "S I": "Scale busy or in motion - no stable weight within 3 s (S I)",
    "S +": "Overload (S +)",
    "S -": "Underload (S -)",
    "ES": "Syntax error - command not recognised (ES)",
    "EL": "Logical error - command cannot be executed (EL)",
    "ET": "Transmission error (ET)",
}

PARITY = {
    "N": serial.PARITY_NONE,
    "E": serial.PARITY_EVEN,
    "O": serial.PARITY_ODD,
}


def parse_response(line):
    """Parse one SICS response line (without CR LF).

    Returns a tuple (kind, data):
      ("weight", (stable: bool, value: str, unit: str))
      ("status", message)
      ("unknown", line)
    """
    line = line.strip()
    m = WEIGHT_RE.match(line)
    if m:
        stable = m.group(1) == "S"
        value = m.group(2).replace(" ", "")
        return "weight", (stable, value, m.group(3))
    key = " ".join(line.split())  # normalise multiple spaces
    if key in STATUS_MESSAGES:
        return "status", STATUS_MESSAGES[key]
    return "unknown", line


# ---------------------------------------------------- Toledo Continuous ---

# Status byte A, bits 0-2 -> number of decimals (manual Table B-2).
# Codes 0-2 ("XXXXX00", "XXXXX0", "XXXXXX") have no decimal point; the
# transmitted digits already include the trailing zeros. Verified on a real
# IND231: 1.99 kg was sent as "   199" (code 4) and as "  1990" g (code 1).
DECIMALS = {0: 0, 1: 0, 2: 0, 3: 1, 4: 2, 5: 3, 6: 4, 7: 5}


def parse_continuous(frame):
    """Parse one Toledo Continuous frame: the bytes between STX and CR.

    Returns a dict, or None if the frame is malformed.
    """
    if len(frame) < 15:
        return None
    try:
        text = frame[:15].decode("ascii")
    except UnicodeDecodeError:
        return None
    swa, swb, swc = (ord(c) for c in text[:3])
    weight_txt, tare_txt = text[3:9], text[9:15]
    if not (weight_txt.strip().isdigit() and (tare_txt.strip().isdigit() or not tare_txt.strip())):
        return None

    decimals = DECIMALS[swa & 0b111]

    def fmt(digits):
        n = int(digits.strip() or "0")
        return f"{n / 10 ** decimals:.{decimals}f}" if decimals else str(n)

    unit_code = swc & 0b111
    if unit_code == 0:
        unit = "kg" if swb & 0b10000 else "lb"
    else:
        unit = {1: "g", 3: "oz", 7: ""}.get(unit_code, "?")

    negative = bool(swb & 0b10)
    return {
        "weight": ("-" if negative else "") + fmt(weight_txt),
        "tare": fmt(tare_txt),
        "unit": unit,
        "net": bool(swb & 0b1),
        "out_of_range": bool(swb & 0b100),
        "motion": bool(swb & 0b1000),
        "zero_not_captured": bool(swb & 0b1000000),
        "expanded": bool(swc & 0b10000),
    }


def describe_continuous(r):
    flags = []
    if r["motion"]:
        flags.append("motion")
    if r["net"]:
        flags.append(f"net, tare {r['tare']} {r['unit']}".rstrip())
    if r["out_of_range"]:
        flags.append("OUT OF RANGE")
    if r["zero_not_captured"]:
        flags.append("zero not captured")
    if r["expanded"]:
        flags.append("expanded x10 - not legal for trade")
    tag = f"  ({', '.join(flags)})" if flags else ""
    return f"Weight: {r['weight']} {r['unit']}".rstrip() + tag


# ------------------------------------------------------------- serial ---

def list_serial_ports():
    return sorted(list_ports.comports(), key=lambda p: p.device)


def port_label(p):
    desc = f"  - {p.description}" if p.description and p.description != "n/a" else ""
    return f"{p.device}{desc}"


def choose_port(ports):
    """Print available ports and let the user pick one."""
    print("Available ports:")
    if not ports:
        print("  (none found)")
        print("\nCheck the cable / USB driver, or pass the port with --port.")
        return None
    for i, p in enumerate(ports, 1):
        print(f"{i}. {port_label(p)}")
    print()

    if len(ports) == 1:
        answer = input(f"Use {ports[0].device}? [Y/n]: ").strip().lower()
        return ports[0].device if answer in ("", "y", "yes") else None

    while True:
        answer = input(f"Select port [1-{len(ports)}] (or type a port name): ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(ports):
            return ports[int(answer) - 1].device
        if answer:
            return answer  # allow a manual name like COM5 or /dev/cu.xxx
        print("Please enter a number.")


def open_port(args):
    return serial.Serial(
        port=args.port,
        baudrate=args.baud,
        bytesize=args.bytesize,
        parity=PARITY[args.parity],
        stopbits=args.stopbits,
        xonxoff=args.xonxoff,
        rtscts=False,
        dsrdtr=False,
        timeout=args.timeout,       # read timeout (S can take up to 3 s)
        write_timeout=2,
    )


class ContinuousDetected(Exception):
    """Raised when SICS mode receives Toledo Continuous frames."""


def sics_cycle(ser, args, state):
    """Send one SICS command and print the answer."""
    ser.reset_input_buffer()  # drop stale/garbage bytes before asking
    payload = (args.command + "\r\n").encode("ascii")
    ser.write(payload)
    ser.flush()
    if not args.no_raw:
        print(f"  TX: {payload!r}")

    # size cap: a SICS reply is a short line; Continuous output fills 200
    # bytes in ~0.1 s, so it is detected quickly instead of after the timeout.
    raw = ser.read_until(b"\n", 200)
    if not args.no_raw:
        shown = raw if len(raw) <= 120 else raw[:120] + b"..."
        print(f"  RX: {shown!r}")

    if STX in raw:
        raise ContinuousDetected()

    if not raw:
        state["timeouts"] += 1
        print(f"Timeout: no response within {args.timeout} s.")
        if state["timeouts"] == 3:
            print("  Hints: is the port's Connection Mode set to SICS (F4.x.1)?\n"
                  "         Do baud rate / data bits / parity match the terminal (F4.x.3)?\n"
                  "         RS-232: is TX/RX crossed correctly (null-modem)?\n"
                  "         Is this the scale's port (not e.g. 'Intel AMT SOL')?")
        return bool(raw)

    state["timeouts"] = 0
    if not raw.endswith(b"\n"):
        print("  (partial line received before timeout)")
    kind, data = parse_response(raw.decode("ascii", errors="replace").strip())
    if kind == "weight":
        stable, value, unit = data
        print(f"Weight: {value} {unit}" + ("" if stable else "  (dynamic)"))
    elif kind == "status":
        print(data)
    else:
        print(f"Unrecognised response: {data!r}")
    time.sleep(args.interval)
    return True


def continuous_cycle(ser, args, state):
    """Read one Toledo Continuous frame (listen only, never transmits)."""
    raw = ser.read_until(b"\r")
    if not raw:
        state["timeouts"] += 1
        print(f"Timeout: no data within {args.timeout} s "
              "(terminal in setup menu, or port not in Continuous mode?)")
        return False
    state["timeouts"] = 0
    if not raw.endswith(b"\r"):
        return True  # partial frame at start-up; wait for the next one

    start = raw.rfind(STX)
    if start < 0:
        if not args.no_raw:
            print(f"  RX (no STX): {raw!r}")
        return True
    frame = raw[start + 1:-1]

    # ~20 frames/s: only print every --interval seconds, or when the value changes
    result = parse_continuous(frame)
    key = None if result is None else (result["weight"], result["unit"], result["motion"])
    now = time.monotonic()
    if key == state.get("last_key") and now - state.get("last_print", 0) < args.interval:
        return True
    state["last_key"], state["last_print"] = key, now

    if not args.no_raw:
        print(f"  RX: {raw[start:]!r}")
    if result is None:
        print(f"Unrecognised frame: {frame!r}")
    else:
        print(describe_continuous(result))
    return True


def run(args):
    ser = None
    state = {"timeouts": 0}
    try:
        while True:
            # (Re)connect
            if ser is None:
                print(f"Connecting to scale on {args.port} "
                      f"({args.baud} {args.bytesize}{args.parity}{args.stopbits}, "
                      f"xonxoff={'on' if args.xonxoff else 'off'}, mode={args.mode})...")
                try:
                    ser = open_port(args)
                except (serial.SerialException, OSError) as e:
                    print(f"Could not open port: {e}")
                    if args.once:
                        return 1
                    print(f"Retrying in {args.retry} s (Ctrl+C to stop)...\n")
                    time.sleep(args.retry)
                    continue
                print("Connected successfully!\n")
                time.sleep(0.2)  # let the port settle
                if args.mode == "continuous":
                    print("Listening only (Toledo Continuous) - nothing is sent to the scale.\n")
                    ser.reset_input_buffer()

            try:
                if args.mode == "sics":
                    ok = sics_cycle(ser, args, state)
                else:
                    ok = continuous_cycle(ser, args, state)
            except ContinuousDetected:
                print("\n*** The terminal is sending Toledo Continuous frames, not SICS. ***\n"
                      "    In this mode an 'S' character means 'Switch units', so the\n"
                      "    commands just sent may have changed the display unit - check\n"
                      "    the scale display (press the units key to switch back).\n"
                      "    Switching to listen-only Continuous mode (nothing is sent).\n"
                      "    Next time use:  --mode continuous\n"
                      "    Or set F4.x.1 Connection Mode = SICS on the terminal.\n")
                args.mode = "continuous"
                ser.reset_input_buffer()
                continue
            except (serial.SerialException, OSError) as e:
                print(f"\nConnection lost: {e}")
                try:
                    ser.close()
                except Exception:
                    pass
                ser = None
                if args.once:
                    return 1
                print(f"Reconnecting in {args.retry} s (Ctrl+C to stop)...\n")
                time.sleep(args.retry)
                continue

            if args.once and (ok or args.mode == "sics"):
                return 0 if ok else 1

    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 0
    finally:
        if ser is not None and ser.is_open:
            ser.close()
            print("Port closed.")


def main():
    parser = argparse.ArgumentParser(
        description="Test communication with a METTLER TOLEDO IND231/IND236 terminal "
                    "(MT-SICS or Toledo Continuous).")
    parser.add_argument("-p", "--port", help="Serial port, e.g. COM3 or /dev/cu.usbserial-110. "
                                             "If omitted, ports are listed for selection.")
    parser.add_argument("--list", action="store_true", help="Only list serial ports and exit.")
    parser.add_argument("-m", "--mode", choices=["sics", "continuous"], default="sics",
                        help="sics = send S and read the reply (default); "
                             "continuous = listen to Toledo Continuous output, send nothing. "
                             "SICS mode switches to continuous automatically if it sees "
                             "continuous frames.")
    parser.add_argument("-b", "--baud", type=int, default=9600, help="Baud rate (default 9600).")
    parser.add_argument("--bytesize", type=int, choices=[7, 8], default=8, help="Data bits (default 8).")
    parser.add_argument("--parity", choices=["N", "E", "O"], default="N", help="Parity (default N).")
    parser.add_argument("--stopbits", type=int, choices=[1, 2], default=1, help="Stop bits (default 1).")
    parser.add_argument("--xonxoff", action="store_true", help="Enable XON/XOFF flow control (default off).")
    parser.add_argument("-c", "--command", default="S",
                        help="SICS command to send each cycle (default S = stable weight; "
                             "SI = immediate weight).")
    parser.add_argument("-i", "--interval", type=float, default=0.5,
                        help="SICS: seconds between requests. Continuous: print at most this "
                             "often unless the weight changes (default 0.5).")
    parser.add_argument("-t", "--timeout", type=float, default=5.0,
                        help="Read timeout in seconds (default 5; S may take up to 3 s).")
    parser.add_argument("--retry", type=float, default=3.0, help="Seconds before reconnecting (default 3).")
    parser.add_argument("--once", action="store_true", help="Read one weight and exit.")
    parser.add_argument("--no-raw", action="store_true", help="Hide raw TX/RX debug lines.")
    args = parser.parse_args()
    args.command = args.command.strip().upper()  # SICS commands must be upper-case

    if args.list:
        ports = list_serial_ports()
        print("Available ports:")
        for i, p in enumerate(ports, 1):
            print(f"{i}. {port_label(p)}")
        if not ports:
            print("  (none found)")
        return 0

    if not args.port:
        try:
            args.port = choose_port(list_serial_ports())
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return 0
        if not args.port:
            return 1
        print()

    return run(args)


def pause_if_double_clicked():
    """Keep the console open when the Windows .exe was started by double-click."""
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        try:
            input("\nPress Enter to close...")
        except (KeyboardInterrupt, EOFError):
            pass


if __name__ == "__main__":
    code = main()
    pause_if_double_clicked()
    sys.exit(code)
