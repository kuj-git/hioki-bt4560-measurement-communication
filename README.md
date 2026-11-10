# hioki-bt4560-measurement-communication
Python script for automated impedance sweeps with the HIOKI BT4560 battery impedance meter via USB, saving each sweep as CSV.

# BT4560 Impedance Sweep

Python script for automated impedance sweeps with the Hioki BT4560(-50) via USB (virtual COM port). Commands follow the BT4560 Communication Manual (A987-01).

## Setup

1. Install the Hioki USB driver; the instrument appears as a COM port (Prolific).
2. `pip install pyserial pandas numpy`
3. Adjust the settings block at the top of `bt4560_sweep.py`, then run `python bt4560_sweep.py`.

## Procedure

- Port is found automatically (`PORT = None`).
- Instrument is configured once (`*RST`, range, speed, sample delay, zero adjustment cleared) and all settings are read back.
- `N_RUNS` sweeps from `F_MAX` down to `F_MIN` (log-spaced, plus `EXTRA_HZ`, minus `EXCLUDED_HZ` ± `EXCLUDE_TOL_HZ`), each with one discarded dummy measurement.
- The instrument stays in remote mode between runs and returns to local only at the end.

## Output (`PATH_SAVE`)

Per run: `bt4560_sweep_<timestamp>_run<n>.csv` with `f_Hz, R_Ohm, X_Ohm, Z_Ohm, phase_deg, voltage_V, valid` and `..._settings.txt` with the read-back settings. `valid = False` marks instrument error codes (≥ 1E+08).

## Notes

- `TEST_MODE = True` measures only 1000 Hz and 100 Hz.
- `:ADJ:CLE` permanently clears the zero adjustment stored in the instrument.
- A running measurement cannot be aborted via `:READ?`. After Ctrl+C the instrument finishes the current point first (0.01 Hz with SLOW ≈ 17 min); the next start waits automatically. Faster: LOCAL key on the instrument.
- Measurement time is dominated by low frequencies: about (8 + `SAMPLE_DELAY_WAVES`) / f per point with SLOW.
