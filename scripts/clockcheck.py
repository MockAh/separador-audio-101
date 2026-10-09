"""Compare time.monotonic() vs time.time() under CPU load on WSL2.

The Windows host clock, read through powershell.exe, is the independent reference.
Usage: python3 -I scripts/clockcheck.py [seconds]  (default 150)
"""

import multiprocessing as mp
import os
import subprocess
import sys
import time

DURATION_S = float(sys.argv[1]) if len(sys.argv) > 1 else 150.0
PS = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def spin(stop_at: float) -> None:
    x = 0
    while time.monotonic() < stop_at:
        x += 1


def host_ms() -> tuple[float, float, float]:
    """Windows host Unix time in s, monotonic midpoint of the call and call latency in s."""
    t0 = time.monotonic()
    out = subprocess.run(
        [PS, "-NoProfile", "-Command", "[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    t1 = time.monotonic()
    return int(out) / 1000, (t0 + t1) / 2, t1 - t0


def snap() -> tuple[float, float, float, float]:
    return (
        time.monotonic(),
        time.time(),
        time.clock_gettime(time.CLOCK_MONOTONIC_RAW),
        time.clock_gettime(time.CLOCK_BOOTTIME),
    )


def main() -> None:
    try:
        h0, hm0, lat0 = host_ms()
    except Exception as e:  # noqa: BLE001
        h0 = None
        print("host clock unavailable:", e)
    s0 = snap()
    stop_at = s0[0] + DURATION_S
    procs = [mp.Process(target=spin, args=(stop_at,)) for _ in range(os.cpu_count() or 1)]
    for p in procs:
        p.start()
    print(f"{'t_mono':>8} {'d(real-mono)':>13} {'d(raw-mono)':>12} {'d(boot-mono)':>13}")
    prev_off = 0.0
    while time.monotonic() < stop_at:
        time.sleep(1)
        s = snap()
        dm = s[0] - s0[0]
        off = (s[1] - s0[1]) - dm
        mark = "  <-- jump" if abs(off - prev_off) > 0.05 else ""
        if mark or int(dm) % 10 == 0:
            raw, boot = (s[2] - s0[2]) - dm, (s[3] - s0[3]) - dm
            print(f"{dm:8.1f} {off:+13.3f} {raw:+12.3f} {boot:+13.3f}{mark}", flush=True)
        prev_off = off
    for p in procs:
        p.join()
    s1 = snap()
    dm = s1[0] - s0[0]
    print()
    print(f"monotonic:      {dm:10.3f} s")
    print(f"time.time:      {s1[1] - s0[1]:10.3f} s")
    print(f"MONOTONIC_RAW:  {s1[2] - s0[2]:10.3f} s")
    print(f"BOOTTIME:       {s1[3] - s0[3]:10.3f} s")
    if h0 is not None:
        h1, hm1, lat1 = host_ms()
        host = h1 - h0
        mono = hm1 - hm0
        print(
            f"Windows host:   {host:10.3f} s  (vs monotonic between the same calls: {mono:.3f} s; "
            f"powershell latency {lat0:.2f}/{lat1:.2f} s)"
        )
        print(
            f"offset WSL realtime - host: start {s0[1] - h0:+.3f} s, "
            f"end {s1[1] - h1:+.3f} s (approx., ±latency/2)"
        )


if __name__ == "__main__":
    main()
