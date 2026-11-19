#!/usr/bin/env python3
"""
Test communication with a METTLER TOLEDO IND231/IND236 terminal over
USB (virtual COM port) or RS-232, using the MT-SICS protocol.

Protocol reference: METTLER TOLEDO IND231/IND236 User's Guide
(30094013 R06, 11/2023), Appendix B.5 "Standard Interface Command Set (SICS)".

  Command  : "S" + CR LF      -> send stable weight value
  Responses: "S S     0.256 kg"  stable weight
             "S I"               command understood, not executable now
                                 (the terminal waits up to 3 s for no-motion)
             "S +"               overload
             "S -"               underload
             "ES" / "EL"         syntax error / logical error

The terminal port must be configured for SICS (setup F4.x.1 Connection
Mode = SICS). The factory default connection mode is "Print", which does
NOT answer commands.

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


def list_serial_ports():
    return sorted(list_ports.comports(), key=lambda p: p.device)


def choose_port(ports):
    """Print available ports and let the user pick one."""
    print("Available ports:")
    if not ports:
        print("  (none found)")
        print("\nCheck the cable / USB driver, or pass the port with --port.")
        return None
    for i, p in enumerate(ports, 1):
        desc = f"  - {p.description}" if p.description and p.description != "n/a" else ""
        print(f"{i}. {p.device}{desc}")
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


def read_weight(ser, command, verbose_raw=True):
    """Send one command and read one response line. Returns the decoded line or None."""
    ser.reset_input_buffer()  # drop stale/garbage bytes before asking
    payload = (command + "\r\n").encode("ascii")
    ser.write(payload)
    ser.flush()
    if verbose_raw:
        print(f"  TX: {payload!r}")

    raw = ser.read_until(b"\n")
    if verbose_raw:
        print(f"  RX: {raw!r}")
    if not raw:
        return None
    if not raw.endswith(b"\n"):
        print("  (partial line received before timeout)")
    return raw.decode("ascii", errors="replace").strip()


def run(args):
    ser = None
    timeouts_in_a_row = 0
    try:
        while True:
            # (Re)connect
            if ser is None:
                print(f"Connecting to scale on {args.port} "
                      f"({args.baud} {args.bytesize}{args.parity}{args.stopbits}, "
                      f"xonxoff={'on' if args.xonxoff else 'off'})...")
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

            # Request weight
            try:
                line = read_weight(ser, args.command, not args.no_raw)
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

            if line is None:
                timeouts_in_a_row += 1
                print(f"Timeout: no response within {args.timeout} s.")
                if timeouts_in_a_row == 3:
                    print("  Hints: is the port's Connection Mode set to SICS (F4.x.1)?\n"
                          "         Do baud rate / data bits / parity match the terminal (F4.x.3)?\n"
                          "         RS-232: is TX/RX crossed correctly (null-modem)?")
            else:
                timeouts_in_a_row = 0
                kind, data = parse_response(line)
                if kind == "weight":
                    stable, value, unit = data
                    tag = "" if stable else "  (dynamic)"
                    print(f"Weight: {value} {unit}{tag}")
                elif kind == "status":
                    print(data)
                else:
                    print(f"Unrecognised response: {data!r}")

            if args.once:
                return 0 if line else 1
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopped by user.")
        return 0
    finally:
        if ser is not None and ser.is_open:
            ser.close()
            print("Port closed.")


def main():
    parser = argparse.ArgumentParser(
        description="Test MT-SICS communication with a METTLER TOLEDO IND231/IND236 terminal.")
    parser.add_argument("-p", "--port", help="Serial port, e.g. COM3 or /dev/cu.usbserial-110. "
                                             "If omitted, ports are listed for selection.")
    parser.add_argument("--list", action="store_true", help="Only list serial ports and exit.")
    parser.add_argument("-b", "--baud", type=int, default=9600, help="Baud rate (default 9600).")
    parser.add_argument("--bytesize", type=int, choices=[7, 8], default=8, help="Data bits (default 8).")
    parser.add_argument("--parity", choices=["N", "E", "O"], default="N", help="Parity (default N).")
    parser.add_argument("--stopbits", type=int, choices=[1, 2], default=1, help="Stop bits (default 1).")
    parser.add_argument("--xonxoff", action="store_true", help="Enable XON/XOFF flow control (default off).")
    parser.add_argument("-c", "--command", default="S",
                        help="SICS command to send each cycle (default S = stable weight; "
                             "SI = immediate weight).")
    parser.add_argument("-i", "--interval", type=float, default=0.5,
                        help="Seconds between requests (default 0.5).")
    parser.add_argument("-t", "--timeout", type=float, default=5.0,
                        help="Read timeout in seconds (default 5; S may take up to 3 s).")
    parser.add_argument("--retry", type=float, default=3.0, help="Seconds before reconnecting (default 3).")
    parser.add_argument("--once", action="store_true", help="Send one request and exit.")
    parser.add_argument("--no-raw", action="store_true", help="Hide raw TX/RX debug lines.")
    args = parser.parse_args()
    args.command = args.command.strip().upper()  # SICS commands must be upper-case

    if args.list:
        ports = list_serial_ports()
        print("Available ports:")
        for i, p in enumerate(ports, 1):
            desc = f"  - {p.description}" if p.description and p.description != "n/a" else ""
            print(f"{i}. {p.device}{desc}")
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
