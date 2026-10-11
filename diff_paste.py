import numpy as np


def load(p):
    d = {}
    for line in open(p, encoding="utf-8"):
        x = line.split()
        if len(x) >= 4:
            try:
                d[int(x[0])] = (float(x[1]), float(x[2]), float(x[3]))
            except ValueError:
                pass
    return d


ref = load("sdruno_ref.txt")
me = load("mine.txt")
common = sorted(set(ref) & set(me))

print(f"{'MHz':>7} | {'Pk me':>7} {'Pk uno':>7} {'dPk':>6} | "
      f"{'F me':>7} {'F uno':>7} {'dF':>6} | "
      f"{'SNR me':>7} {'SNR uno':>7} {'dSNR':>6}")
print("-" * 85)
dpk, df, ds = [], [], []
for k in common:
    m, r = me[k], ref[k]
    a, b, c = m[0] - r[0], m[1] - r[1], m[2] - r[2]
    dpk.append(a); df.append(b); ds.append(c)
    flag = " <<<" if abs(a) > 5 else ""
    print(f"{k/1000:7.1f} | {m[0]:7.2f} {r[0]:7.2f} {a:+6.2f} | "
          f"{m[1]:7.2f} {r[1]:7.2f} {b:+6.2f} | "
          f"{m[2]:7.2f} {r[2]:7.2f} {c:+6.2f}{flag}")

dpk, df, ds = np.array(dpk), np.array(df), np.array(ds)
print("-" * 85)
print(f"freqs: {len(common)}")
print(f"dPk : mean {dpk.mean():+.2f}  mean|d| {np.abs(dpk).mean():.2f}  max|d| {np.abs(dpk).max():.2f}")
print(f"dF  : mean {df.mean():+.2f}  mean|d| {np.abs(df).mean():.2f}  max|d| {np.abs(df).max():.2f}")
print(f"dSNR: mean {ds.mean():+.2f}  mean|d| {np.abs(ds).mean():.2f}  max|d| {np.abs(ds).max():.2f}")
