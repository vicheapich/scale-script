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
    if args.all and not args.no_raw:
        print(f"  TX: {payload!r}")

    # size cap: a SICS reply is a short line; Continuous output fills 200
    # bytes in ~0.1 s, so it is detected quickly instead of after the timeout.
    raw = ser.read_until(b"\n", 200)
    if STX in raw and not args.no_raw:
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
    kind, data = parse_response(raw.decode("ascii", errors="replace").strip())
    if kind == "weight":
        stable, value, unit = data
        text = f"Weight: {value} {unit}" + ("" if stable else "  (dynamic)")
        report(args, state, value, unit, stable, raw, text)
    elif args.all or kind == "unknown":
        # In event mode "S I" (scale in motion) is normal and stays quiet.
        if not args.all and not args.no_raw:
            print(f"  RX: {raw!r}")
        if not raw.endswith(b"\n"):
            print("  (partial line received before timeout)")
        print(data if kind == "status" else f"Unrecognised response: {data!r}")
    time.sleep(args.interval)
    return True


def now_stamp():
    return time.strftime("%H:%M:%S")


def report(args, state, value, unit, stable, raw, text):
    """Decide what to print for one weight reading.

    --all : print readings (throttled to --interval unless the value changes).
    default (event mode): print once when an item has settled on the scale,
    and once when the scale is empty again. Nothing is printed while the
    weight is moving or unchanged.
    """
    if args.all:
        key = (value, unit, stable)
        now = time.monotonic()
        if key == state.get("last_key") and now - state.get("last_print", 0) < args.interval:
            return
        state["last_key"], state["last_print"] = key, now
        if not args.no_raw:
            print(f"  RX: {raw!r}")
        print(text)
        return

    # Event mode: wait for the same stable value on N consecutive readings.
    key = (value, unit)
    if not stable:
        state["candidate"], state["stable_count"] = None, 0
        return
    if key != state.get("candidate"):
        state["candidate"], state["stable_count"] = key, 0
    state["stable_count"] += 1
    settle = args.settle or (5 if args.mode == "continuous" else 1)
    if state["stable_count"] < settle or key == state.get("reported"):
        return
    state["reported"] = key

    try:
        w = float(value)
    except ValueError:
        w = 0.0
    if abs(w) <= args.min_weight:
        if state.get("loaded"):
            print(f"[{now_stamp()}] Scale empty ({value} {unit}). Waiting for next item...\n")
        elif "started" not in state:
            print(f"[{now_stamp()}] Ready - scale empty ({value} {unit}). Place an item...\n")
        state["loaded"] = False
        state["need_empty"] = False  # auto-send: next tracking number may be scanned
    else:
        if not args.no_raw:
            print(f"  RX: {raw!r}")
        print(f"[{now_stamp()}] ITEM  {text}")
        state["loaded"] = True
        if args.webhook:
            if "OUT OF RANGE" in text or w < 0:
                print("  Not offered for sending: weight is out of range / negative.")
            elif args.auto_send and not state.get("armed"):
                print("  Already sent for this parcel - remove it from the scale to continue.")
            else:
                state["pending_send"] = (value, unit)  # handled in run()
    state["started"] = True


def arm_tracking(args, state):
    """Auto-send: ask for the next tracking number while the scale is empty.

    Returns False if input is closed (stop the program).
    """
    import webhook

    sent = state.setdefault("sent", {})
    default = state.pop("preset_tracking", "")
    while True:
        hint = f" [{default}]" if default else ""
        try:
            entered = input(f"[{now_stamp()}] Scan tracking number (MAILITM_FID){hint}: ").strip()
        except EOFError:
            return False
        tracking = webhook.normalize_tracking(entered or default)
        default = ""
        if not tracking:
            continue
        if not webhook.S10_RE.match(tracking):
            print(f"  '{tracking}' is not a valid tracking number "
                  "(2 letters + 9 digits + 2 letters, e.g. EE123456785KH).")
            continue
        try:
            if not webhook.s10_check_digit_ok(tracking):
                if input("  WARNING: check digit does not match (typo?). Use it anyway? [y/N]: ")\
                        .strip().lower() not in ("y", "yes"):
                    continue
            if tracking in sent:
                if input(f"  {tracking} was already sent ({sent[tracking]}). "
                         "Type 'again' to send again: ").strip().lower() != "again":
                    continue
        except EOFError:
            return False
        state["armed"] = tracking
        print(f"  Place parcel {tracking} on the scale - it is sent automatically "
              "when the weight is stable.\n")
        return True


def webhook_flow(args, state, value, unit):
    """Ask the operator for a tracking number, confirm, sign and POST.

    Only called for a stable, non-zero weight event. Nothing is sent
    without an explicit 'y' from the operator.
    """
    import webhook

    sent = state.setdefault("sent", {})  # tracking -> "2.70 kg at 13:14:12"
    if args.auto_send:
        # Tracking number was scanned (and confirmed) before the parcel was placed.
        tracking = state.pop("armed")
        state["need_empty"] = True  # one send per scan; parcel must be removed first
        try:
            payload = webhook.build_payload(tracking, value, unit)
        except ValueError as e:
            print(f"  Cannot send: {e}\n")
            return
        print(f"  AUTO-SEND {tracking}: {value} {unit}")
        post_and_show(args, webhook, payload, sent, tracking, value, unit)
        print("  Remove the parcel, then scan the next tracking number.\n")
        return

    default = state.pop("preset_tracking", "")
    print(f"\n  --- Send {value} {unit} to the webhook? ---")
    try:
        while True:
            hint = f" [{default}]" if default else ""
            entered = input(f"  Tracking number (MAILITM_FID){hint}, Enter = skip: ").strip()
            tracking = webhook.normalize_tracking(entered or default)
            default = ""
            if not tracking:
                print("  Skipped - nothing sent.\n")
                return
            if webhook.S10_RE.match(tracking):
                break
            print(f"  '{tracking}' is not a valid tracking number "
                  "(2 letters + 9 digits + 2 letters, e.g. EE123456789KH).")

        if not webhook.s10_check_digit_ok(tracking):
            print("  WARNING: the check digit does not match - possible typo.")
        try:
            payload = webhook.build_payload(tracking, value, unit)
        except ValueError as e:
            print(f"  Cannot send: {e}\n")
            return

        body = webhook.php_json_encode(payload).decode("ascii")
        print(f"  POST {args.webhook_config.url}")
        print(f"  Body: {body}")
        if tracking in sent:
            print(f"  WARNING: {tracking} was already sent successfully ({sent[tracking]}).")
            answer = input("  Send it AGAIN? Type 'again' to confirm: ").strip().lower()
            confirmed = answer == "again"
        else:
            confirmed = input("  Send now? [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        confirmed = False
    if not confirmed:
        print("  Cancelled - nothing sent.\n")
        return
    post_and_show(args, webhook, payload, sent, tracking, value, unit)
    print()


def post_and_show(args, webhook, payload, sent, tracking, value, unit):
    """POST (unless dry run) and print the result as it happens."""
    if args.webhook_dry_run:
        print(f"  Body: {webhook.php_json_encode(payload).decode('ascii')}")
        print("  DRY RUN - request NOT sent.")
        return
    print(f"  [{now_stamp()}] Sending to {args.webhook_config.url} ...", flush=True)
    started = time.monotonic()
    result = webhook.send(args.webhook_config, payload)
    took = f"{time.monotonic() - started:.1f} s"
    status = f"HTTP {result.status}" if result.status else "no HTTP response"
    if result.ok:
        print(f"  [{now_stamp()}] SUCCESS ({status}, {took}): {result.message}")
        sent[tracking] = f"{value} {unit} at {now_stamp()}"
    else:
        print(f"  [{now_stamp()}] FAILED ({status}, {took}): {result.message}")
    if result.body:
        print(f"  Response: {result.body}")


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

    result = parse_continuous(frame)
    if result is None:
        if args.all:
            print(f"Unrecognised frame: {frame!r}")
        return True
    report(args, state, result["weight"], result["unit"], not result["motion"],
           raw[start:], describe_continuous(result))
    return True


def run(args):
    ser = None
    state = {"timeouts": 0, "preset_tracking": getattr(args, "tracking", "")}
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

            if args.auto_send and not state.get("armed") and not state.get("need_empty"):
                if not arm_tracking(args, state):
                    return 0
                ser.reset_input_buffer()  # readings queued while waiting for the scan
                state["reported"] = None  # report the current weight again if a parcel is already on

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

            pending = state.pop("pending_send", None)
            if pending:
                webhook_flow(args, state, *pending)
                ser.reset_input_buffer()  # drop readings that queued up during the prompt

            if args.once and (args.mode == "sics" or "last_key" in state):
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
    parser.add_argument("-a", "--all", action="store_true",
                        help="Print every reading (debug). Default: print only when an item "
                             "settles on the scale and when the scale is empty again.")
    parser.add_argument("--settle", type=int, default=None,
                        help="Event mode: identical stable readings needed before reporting "
                             "(default 5 for continuous = ~0.25 s, 1 for SICS).")
    parser.add_argument("--min-weight", type=float, default=0.0,
                        help="Event mode: readings at or below this (in display units) count "
                             "as 'empty' (default 0).")
    parser.add_argument("--once", action="store_true", help="Read one weight and exit.")
    parser.add_argument("-w", "--webhook", action="store_true",
                        help="After each stable item weight, offer to POST it to WEBHOOK_URL "
                             "(settings in .env). Nothing is sent without confirmation.")
    parser.add_argument("--webhook-dry-run", action="store_true",
                        help="Like --webhook, but only show the request - never send it.")
    parser.add_argument("--auto-send", action="store_true",
                        help="Scan the tracking number first, then the weight is sent "
                             "automatically as soon as it is stable (one send per scan; "
                             "the scale must be empty before the next scan). Implies --webhook.")
    parser.add_argument("--tracking", default="",
                        help="Tracking number (MAILITM_FID) to pre-fill for the first item.")
    parser.add_argument("--no-raw", action="store_true", help="Hide raw TX/RX debug lines.")
    args = parser.parse_args()
    args.command = args.command.strip().upper()  # SICS commands must be upper-case
    if args.once:
        args.all = True  # --once just prints the first reading

    args.webhook = args.webhook or args.webhook_dry_run or args.auto_send
    if args.webhook:
        if args.all or args.once:
            print("--webhook works with item events only; do not combine it with --all / --once.")
            return 2
        import webhook
        try:
            args.webhook_config = webhook.WebhookConfig.from_env()
        except ValueError as e:
            print(f"Webhook configuration error: {e}")
            print("Copy .env.example to .env (next to the script/exe) and fill it in.")
            return 2
        if args.webhook_dry_run:
            mode = "DRY RUN (nothing is sent)"
        elif args.auto_send:
            mode = "AUTO-SEND (sends as soon as the weight is stable)"
        else:
            mode = "ON (asks before sending)"
        print(f"Webhook {mode}: {args.webhook_config.url}\n")

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
