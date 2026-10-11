'''
Analyze all rf_sweep_data rows per SDR region (and per RTYS) to determine
calibration thresholds using kernel density estimation + valley detection.
Outputs sdr_thresholds.json.
'''
import json
import os

import numpy as np
from sqlalchemy import create_engine, text

DB_URL = 'postgresql://postgres:root@localhost:5432/sdr_db'

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sdr_config.json')
try:
    with open(_CONFIG_PATH, 'r', encoding='utf-8') as _f:
        _SDR_CFG = json.load(_f)
    VALID_SDRS = {s['id'] for s in _SDR_CFG.get('sdrs', [])}
    _STATION_MAP = {}
    for sdr in _SDR_CFG.get('sdrs', []):
        sid = sdr['id']
        for st in sdr.get('stations', []):
            freq = round(float(st.get('freq', 0.0)), 3)
            rtys = st.get('rtys', '')
            if rtys:
                _STATION_MAP[(sid, freq)] = rtys
except Exception:
    VALID_SDRS = {'site_1', 'site_2', 'site_3', 'site_4', 'site_5', 'site_6', 'site_7', 'site_8'}
    _STATION_MAP = {}

DB_MIN = -90.0
DB_MAX = -30.0
SNR_MIN = 0.0
SNR_MAX = 60.0
MIN_SDR_SAMPLES = 1000
MIN_RTYS_SAMPLES = 100
KDE_BINS = 256
KDE_SIGMA = 4.0
PEAK_PROMINENCE = 0.1


def _to_mhz(freq_val):
    f = float(freq_val)
    if f > 1000:
        return f / 1000.0
    return f


def _rtys_for(sdr_id, freq_mhz, db_rtys):
    if db_rtys:
        return db_rtys
    return _STATION_MAP.get((sdr_id, round(freq_mhz, 3)), None)


def _smooth_histogram(counts, sigma=KDE_SIGMA):
    size = int(3 * sigma)
    x = np.arange(-size, size + 1)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel = kernel / kernel.sum()
    return np.convolve(counts, kernel, mode='same')


def _find_extrema(density):
    peaks = []
    valleys = []
    for i in range(1, len(density) - 1):
        if density[i] > density[i - 1] and density[i] >= density[i + 1]:
            peaks.append(i)
        elif density[i] < density[i - 1] and density[i] <= density[i + 1]:
            valleys.append(i)
    return peaks, valleys


def _kde_thresholds(power, data_min=None, data_max=None):
    power = np.asarray(power, dtype=np.float64)
    power = power[np.isfinite(power)]
    if len(power) < 4:
        return None

    lo = data_min if data_min is not None else DB_MIN
    hi = data_max if data_max is not None else DB_MAX
    window = power[(power >= lo) & (power <= hi)]
    if len(window) < 4:
        return None

    counts, edges = np.histogram(window, bins=KDE_BINS, range=(lo, hi))
    bin_centers = (edges[:-1] + edges[1:]) / 2.0
    density = _smooth_histogram(counts)

    if density.max() == 0:
        return None
    threshold = density.max() * PEAK_PROMINENCE
    peaks, _ = _find_extrema(density)
    prominent = [i for i in peaks if density[i] >= threshold]
    if not prominent:
        return None

    mid = len(density) // 2
    left = [i for i in prominent if i <= mid]
    right = [i for i in prominent if i > mid]

    if not left or not right:
        # unimodal: fall back to percentiles
        p10, p50, p75 = np.percentile(power, [10, 50, 75])
        return {'noise_floor': p10, 'signal_threshold': p50, 'strong_signal': p75}

    noise_i = left[np.argmax(density[left])]
    signal_i = right[np.argmax(density[right])]
    if noise_i >= signal_i:
        return None

    # valley between peaks
    valley_i = noise_i + int(np.argmin(density[noise_i:signal_i]))
    signal_threshold = bin_centers[valley_i]

    # half-height left of noise peak
    noise_h = density[noise_i] * 0.5
    noise_floor_i = noise_i
    while noise_floor_i > 0 and density[noise_floor_i] > noise_h:
        noise_floor_i -= 1
    noise_floor = bin_centers[noise_floor_i]

    # half-height right of signal peak
    signal_h = density[signal_i] * 0.5
    strong_i = signal_i
    while strong_i < len(density) - 1 and density[strong_i] > signal_h:
        strong_i += 1
    strong_signal = bin_centers[strong_i]

    return {
        'noise_floor': noise_floor,
        'signal_threshold': signal_threshold,
        'strong_signal': strong_signal,
    }


def _calc_thresholds(power, snr):
    power = np.asarray(power, dtype=np.float64)
    power = power[np.isfinite(power)]
    if len(power) < 4:
        return None

    snr = np.asarray(snr, dtype=np.float64)
    snr = snr[np.isfinite(snr)]

    kt = _kde_thresholds(power)
    if kt is None:
        return None

    p05, p95 = np.percentile(power, [5, 95])
    signal_power = power[power >= kt['signal_threshold']]
    strong = np.percentile(signal_power, 95) if len(signal_power) else kt['strong_signal']

    kt_snr = _kde_thresholds(snr, SNR_MIN, SNR_MAX)
    if kt_snr is None:
        snr_threshold = round(max(SNR_MIN, min(10.0, float(np.percentile(snr, 25) if len(snr) else 0.0))), 1)
    else:
        snr_threshold = round(max(SNR_MIN, min(10.0, max(kt_snr['signal_threshold'], kt_snr['noise_floor'] + 5.0))), 1)

    return {
        'db_min': DB_MIN,
        'db_max': DB_MAX,
        'noise_floor': round(max(DB_MIN, min(DB_MAX, kt['noise_floor'])), 1),
        'signal_threshold': round(max(DB_MIN, min(DB_MAX, min(kt['signal_threshold'] - 3.5, kt['noise_floor'] + 5.0))), 1),
        'strong_signal': round(max(DB_MIN, min(DB_MAX, strong + 10.0)), 1),
        'snr_threshold': round(max(SNR_MIN, min(SNR_MAX, snr_threshold)), 1),
        'stats': {
            'samples': int(len(power)),
            'avg_power': round(float(np.mean(power)), 1),
            'median_power': round(float(np.median(power)), 1),
            'avg_snr': round(float(np.mean(snr)), 1) if len(snr) else None,
            'median_snr': round(float(np.median(snr)), 1) if len(snr) else None,
            'p05': round(float(p05), 1),
            'p95': round(float(p95), 1),
        }
    }


def _process_sdr(sid):
    engine = create_engine(DB_URL)
    sdr_power = []
    sdr_snr = []
    rtys_buckets = {}

    with engine.connect().execution_options(stream_results=True) as conn:
        result = conn.execute(text('''
            SELECT frequency_mhz, power_dbfs, snr_db, rtys_name
            FROM rf_sweep_data
            WHERE sdr_id = :sid
              AND power_dbfs IS NOT NULL
              AND snr_db IS NOT NULL
        '''), {'sid': sid})
        for row in result:
            freq_mhz = _to_mhz(row[0])
            power = row[1]
            snr = row[2]
            db_rtys = row[3]
            rtys = _rtys_for(sid, freq_mhz, db_rtys)
            sdr_power.append(power)
            sdr_snr.append(snr)
            if rtys:
                rtys_buckets.setdefault(rtys, {'power': [], 'snr': []})
                rtys_buckets[rtys]['power'].append(power)
                rtys_buckets[rtys]['snr'].append(snr)

    if len(sdr_power) < MIN_SDR_SAMPLES:
        print('%15s | %5d | SKIPPED (too few samples)' % (sid, len(sdr_power)))
        return None

    sdr_th = _calc_thresholds(sdr_power, sdr_snr)
    if sdr_th is None:
        return None

    rtys_thresholds = {}
    for r, data in rtys_buckets.items():
        if len(data['power']) < MIN_RTYS_SAMPLES:
            continue
        rt = _calc_thresholds(data['power'], data['snr'])
        if rt is not None:
            rtys_thresholds[r] = rt

    if rtys_thresholds:
        sdr_th['rtys'] = rtys_thresholds
    return sdr_th


thresholds = {}
for sid in sorted(VALID_SDRS):
    print('Processing %s ...' % sid)
    th = _process_sdr(sid)
    if th is not None:
        thresholds[sid] = th

print('=' * 110)
print('Generated thresholds for %d SDR regions.' % len(thresholds))

output_path = 'sdr_thresholds.json'
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(thresholds, f, indent=2, ensure_ascii=False)

print('Written to: %s' % output_path)
print('\nThresholds summary:')
for sid, t in thresholds.items():
    print('  %15s: display [%.0f - %.0f] dB | noise_floor=%.1f | signal=%.1f | strong=%.1f | snr=%.1f' % (
        sid, t['db_min'], t['db_max'], t['noise_floor'], t['signal_threshold'], t['strong_signal'], t['snr_threshold']))
    for r, rt in t.get('rtys', {}).items():
        print('      %s: display [%.0f - %.0f] dB | noise_floor=%.1f | signal=%.1f | strong=%.1f | snr=%.1f' % (
            r, rt['db_min'], rt['db_max'], rt['noise_floor'], rt['signal_threshold'], rt['strong_signal'], rt['snr_threshold']))
