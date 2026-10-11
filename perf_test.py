import time, sys
sys.path.insert(0, r'C:\projects\CGrep-v3')
from server import _fetch_latest_spectrum
for i in range(3):
    t0 = time.time()
    r = _fetch_latest_spectrum()
    print('fetch_latest_spectrum took', round(time.time()-t0,3), 's, keys', list(r.keys()) if r else None)
