import argparse
import os
import sys
import time

import green_api

def _watch_csv(csv_path):
    """Watch the CSV file for new rows without sending WhatsApp alerts."""
    print(f"👁️ Watching {csv_path} for new signals...")

    while not os.path.exists(csv_path):
        time.sleep(1)

    # Record the EXACT size when wp.py starts — anything already in the file
    # is considered "old" and must be ignored.
    start_pos = os.path.getsize(csv_path)
    print(f"📌 Ignoring existing data. Only reading from byte {start_pos} onwards.")

    # Use binary mode for reliable seeking on Windows (text mode + CRLF can
    # produce off-by-one seek positions).
    with open(csv_path, "rb") as file:
        file.seek(start_pos)
        buf = b""

        while True:
            chunk = file.read(4096)
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.decode("utf-8", errors="ignore").strip()
                    if not line:
                        continue

                    row = line.split(",")
                    if len(row) >= 3 and row[0] != "Timestamp":
                        try:
                            timestamp = row[0]
                            frequency = row[1]
                            power = float(row[2])
                            print(f"{timestamp} | {frequency} MHz | {power:.2f} dBFS")
                        except ValueError:
                            continue
            else:
                time.sleep(0.5)


def _send_test(phones, message=None):
    if not green_api.is_configured():
        print("❌ Green API is not configured. Set GREEN_API_ID and GREEN_API_TOKEN in config.env")
        sys.exit(1)
    try:
        result = green_api.send_test(phones, message)
    except Exception as exc:
        print(f"❌ Failed to send test message: {exc}")
        sys.exit(1)
    print(f"📤 Sent {result['sent']}/{result['total']}")
    for item in result["results"]:
        icon = "✅" if item.get("ok") else "❌"
        print(f"  {icon} {item['phone']} - {item.get('idMessage') or item.get('detail')}")
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WhatsApp / Green API helper")
    parser.add_argument(
        "--test",
        nargs="*",
        metavar="PHONE",
        help="Send a test message to the given phone numbers (or all saved numbers if none)",
    )
    parser.add_argument("--message", default=None, help="Custom test message text")
    parser.add_argument(
        "--watch",
        default=r"C:\projects\CGrep-v3\Logs\sweepdash_20260530.csv",
        help="CSV file to watch without sending alerts",
    )
    args = parser.parse_args()

    if args.test is not None:
        _send_test(args.test or None, args.message)
    else:
        _watch_csv(args.watch)
