"""
Impedance sweep with the Hioki BT4560 via USB (virtual COM port).

Commands according to the BT4560 Communication Manual (A987-01).
Requirements: Hioki USB driver installed, pip install pyserial pandas
Usage:  python bt4560_sweep.py
"""

import os
import time
from datetime import datetime

import numpy as np
import pandas as pd
import serial

# ---------------- Settings ----------------
PORT = None              # None: find automatically, or set e.g. "COM5"
PORT_MANUFACTURER = "Prolific"   # USB-serial chip of the Hioki connection
BAUD = 9600              # irrelevant for USB, must match the instrument for RS-232C

Z_SPEED = "SLOW"          # "FAST" / "MED" / "SLOW"  (impedance measurement)
V_SPEED = "FAST"          # "FAST" / "MED" / "SLOW"  (voltage measurement)

RANGE_OHM = 100e-3         # 3 mOhm range 1.5 A, 10 mOhm 500 mA, 100 mOhm 50 mA rms
F_MIN, F_MAX = 0.1, 1050.0  # full frequency range of BT4560-50 (BT4560: from 0.1 Hz)? Stimmt das? oder haben wir -50
POINTS_PER_DECADE = 10   # log-spaced frequencies per factor of 10, both ends included

EXCLUDED_HZ = [0.1, 1050.0]  # mains frequency and 2nd harmonic, skipped
EXTRA_HZ = []               # measure additional frequencies
EXCLUDE_TOL_HZ = 3.0         # absolute exclusion band: f within ±3 Hz is skipped

FREQ_SETTLE_S = 0.5      # pause after sending :FREQ so the instrument has processed the command; no current flows yet, settling of the sample is done by SAMPLE_DELAY_WAVES
SAMPLE_DELAY_WAVES = 2.0 # sample delay in periods, 0.0 to 9.0 (factory 1.0): waits (1/f)*M + 5 ms after current is applied
SLOPE_CORRECTION = "ON"  # potential slope correction
ZERO_CROSS_STOP = "OFF"   # measurement signal zero cross stop
AVERAGING = 1            # 1 to 99, how often if the measurement for one frequency taken and averaged
N_RUNS = 1              # number of complete sweeps, each saved to its own file
TEST_MODE = False        # True: only 1000 Hz and 100 Hz to check the setup, False: full sweep
PATH_SAVE = r""
# ------------------------------------------------

ERROR_THRESHOLD = 1e7    # error codes are returned as +1E+08 ... +9E+08 (manual p. 40)


def snap(f):
    """Round to the frequency resolution of the BT4560 (manual p. 103)."""
    step = 0.01 if f < 1 else 0.1 if f < 10 else 1 if f < 100 else 10
    return round(round(f / step) * step, 2)


def frequencies():
    n = int(np.ceil(np.log10(F_MAX / F_MIN) * POINTS_PER_DECADE)) + 1
    f = np.logspace(np.log10(F_MIN), np.log10(F_MAX), n)
    f = {snap(x) for x in f} | {snap(x) for x in EXTRA_HZ}   # dense grid + extra points
    f = sorted(f, reverse=True)                               # unique, high to low, slow points last
    return [x for x in f if all(abs(x - b) > EXCLUDE_TOL_HZ for b in EXCLUDED_HZ)]


def find_port(manufacturer=PORT_MANUFACTURER):
    """Return the COM port whose USB manufacturer matches, e.g. "COM5"."""
    from serial.tools import list_ports   # submodule, not loaded by "import serial"
    ports = list_ports.comports()          # all serial ports
    for p in ports:
        if p.manufacturer and manufacturer.lower() in p.manufacturer.lower():
            return p.device
    found = ", ".join(f"{p.device} ({p.manufacturer})" for p in ports) or "none"
    raise SystemExit(f"Error: no {manufacturer} port found. Available ports: {found}")


class BT4560:
    def __init__(self, port, baud):
        self.ser = serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1,
                                 timeout=5, write_timeout=5)
        time.sleep(0.5)                    # give the USB-serial adapter time after opening

    def write(self, cmd):
        self.ser.write((cmd + "\r\n").encode("ascii"))

    def query(self, cmd, timeout=5):
        self.ser.timeout = timeout
        self.ser.reset_input_buffer()
        self.write(cmd)
        line = self.ser.read_until(b"\r\n").decode("ascii").strip()
        if not line:
            raise TimeoutError(f"No reply to {cmd!r}")
        return line

    def wait_ready(self, max_wait=900):
        """Wait until the instrument answers *IDN?.

        If a previous run was aborted during :READ?, the instrument finishes that
        measurement first (minutes at low frequencies) and then sends the old result.
        """
        self.ser.timeout = 5
        self.ser.reset_input_buffer()
        self.write("*IDN?")
        t0 = time.time()
        while time.time() - t0 < max_wait:
            line = self.ser.read_until(b"\r\n").decode("ascii", "replace").strip()
            if line.startswith("HIOKI"):
                return line
            if line:
                print("Discarded old reply:", line)
            else:
                print(f"Instrument busy, waiting ... {time.time() - t0:.0f} s")
        raise TimeoutError(f"No reply to '*IDN?' within {max_wait} s")

    def close(self):
        try:
            self.write(":SYST:LOC")        # back to local operation
        finally:
            self.ser.close()


def read_timeout(f):
    """Serial timeout for :READ? at frequency f (only so the script does not give up too early).

    The instrument chooses the number of measured periods itself; 8 periods (SLOW) is used as upper bound.
    """
    t = (8 + SAMPLE_DELAY_WAVES) / f * AVERAGING
    return 1.5 * t + 10


def measure(dev, f):
    response = dev.query(":READ?", timeout=read_timeout(f))
    return [float(s) for s in response.split(",")]


def setup(dev):
    """Configure the instrument once and read the settings back for the measurement log."""
    dev.write("*RST")                     # stop measurement, default settings
    time.sleep(1)
    dev.write(":INIT:CONT OFF")           # no free-run, measure only on :READ?
    dev.write(":FUNC RV")                 # R, X, V
    dev.write(f":RANG {RANGE_OHM:.1E}")
    dev.write(f":SAMP:RATE Z,{Z_SPEED}")
    dev.write(f":SAMP:RATE V,{V_SPEED}")
    dev.write(f":CALC:AVER {AVERAGING}")
    dev.write(":SAMP:DEL:MODE WAVE")      # settling: sample delay in signal periods (manual p. 25)
    dev.write(f":SAMP:DEL:WAVE {SAMPLE_DELAY_WAVES:.1f}")
    dev.write(f":ADJ:SLOP {SLOPE_CORRECTION}")   # potential slope correction (p. 26)
    dev.write(f":ZERO:CROS {ZERO_CROSS_STOP}")   # measurement signal zero cross stop (p. 26)
    dev.write(":ADJ:CLE")                 # clear zero adjustment, i.e. measure uncalibrated
    dev.write(":TRIG:SOUR IMM")
    dev.write(":MEAS:VAL 1")              # return measured values only
    time.sleep(0.5)

    # read back the settings and write them to the measurement log
    queries = {"IDN": "*IDN?", "range": ":RANG?", "Z speed": ":SAMP:RATE? Z",
               "V speed": ":SAMP:RATE? V", "averaging": ":CALC:AVER?",
               "sample delay mode": ":SAMP:DEL:MODE?", "sample delay waves": ":SAMP:DEL:WAVE?",
               "slope correction": ":ADJ:SLOP?", "zero cross stop": ":ZERO:CROS?",
               "zero adjustment active": ":ADJ:STAT?"}
    # long timeout: replies only come after a measurement that may still be running
    settings = {k: dev.query(q, timeout=120) for k, q in queries.items()}
    for k, v in settings.items():
        print(f"{k:24s} {v}")
    return settings


def sweep(dev, run, settings):
    """One complete sweep, saved to its own CSV (also if it is interrupted)."""
    outfile = os.path.join(PATH_SAVE, f"bt4560_sweep_{datetime.now():%Y%m%d_%H%M%S}_run{run}.csv")
    os.makedirs(PATH_SAVE, exist_ok=True)
    with open(outfile.replace(".csv", "_settings.txt"), "w") as fh:
        fh.write(f"start: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        fh.writelines(f"{k}: {v}\n" for k, v in settings.items())

    rows = []
    try:
        freqs = [1000.0, 100.0] if TEST_MODE else frequencies()   # high to low
        dev.write(f":FREQ {freqs[0]:.2E}")
        time.sleep(FREQ_SETTLE_S)
        measure(dev, freqs[0])                # dummy measurement, discarded
        for f in freqs:
            dev.write(f":FREQ {f:.2E}")
            time.sleep(FREQ_SETTLE_S)
            f_set = float(dev.query(":FREQ?", timeout=30))   # frequency actually set
            r, x, v = measure(dev, f_set)
            ok = max(abs(r), abs(x), abs(v)) < ERROR_THRESHOLD
            z = np.hypot(r, x) if ok else np.nan
            phase = np.degrees(np.arctan2(x, r)) if ok else np.nan
            rows.append(dict(f_Hz=f_set, R_Ohm=r, X_Ohm=x, Z_Ohm=z, phase_deg=phase,
                             voltage_V=v, valid=ok))
            print(f"{f_set:9.3f} Hz  R={r: .5e}  X={x: .5e}  V={v: .5e}  {'' if ok else 'ERROR'}")
    finally:
        if rows:                           # save whatever was measured, also after an error
            pd.DataFrame(rows).sort_values("f_Hz").to_csv(outfile, index=False)
            print("Saved:", outfile)


def main():
    port = PORT or find_port()
    print("Port:", port)
    dev = BT4560(port, BAUD)
    try:
        print("Device:", dev.wait_ready())
        settings = setup(dev)              # once: the instrument stays in remote mode between runs
        for run in range(1, N_RUNS + 1):
            print(f"--- run {run}/{N_RUNS} ---")
            sweep(dev, run, settings)
    except KeyboardInterrupt:
        print("Stopped by user.")
    except Exception as e:
        print("ERROR:", repr(e))
    finally:
        dev.close()                        # back to local only after the last run


if __name__ == "__main__":
    main()
