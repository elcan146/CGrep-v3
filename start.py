import os
import subprocess
import threading

BASE_DIR = r"C:\projects\CGrep-v3"
DASHBOARD_DIR = os.path.join(BASE_DIR, "sdr-dashboard")
DIST_INDEX = os.path.join(DASHBOARD_DIR, "dist", "index.html")

# Build only when dist is missing; pass --rebuild to force a build
if "--rebuild" in os.sys.argv or not os.path.isfile(DIST_INDEX):
    print("[start] Building frontend...")
    subprocess.run(
        ["npm", "run", "build"],
        cwd=DASHBOARD_DIR,
        check=True
    )
else:
    print("[start] dist/index.html exists; skipping npm run build. Use --rebuild to force.")

def stream_output(proc, prefix):
    for line in iter(proc.stdout.readline, ''):
        print(f"[{prefix}] {line}", end='')

server = subprocess.Popen(
    ["python", "server.py"],
    cwd=BASE_DIR,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True
)

# Named tunnel: run the tunnel UUID configured in Cloudflare.
# For a quick temporary tunnel use: ["cloudflared", "tunnel", "--url", "http://localhost:8000"]
cloudflared = subprocess.Popen(
    [
        "cloudflared",
        "tunnel",
        "run",
        "YOUR_CLOUDFLARE_TUNNEL_ID"
    ],
    cwd=BASE_DIR,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True
)

threading.Thread(
    target=stream_output,
    args=(server, "SERVER"),
    daemon=True
).start()

threading.Thread(
    target=stream_output,
    args=(cloudflared, "TUNNEL"),
    daemon=True
).start()

server.wait()
cloudflared.wait()