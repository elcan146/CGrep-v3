import os, sys
sys.path.insert(0, r'c:\projects\CGrep-v3')
os.environ.setdefault('POSTGRES_PASSWORD', 'changeme')
from sqlalchemy import text
from models import SessionLocal

db = SessionLocal()
try:
    rows = db.execute(text("""
        SELECT sdr_id, count(*) as cnt, max(timestamp) as latest
        FROM rf_sweep_data
        WHERE timestamp >= now() - interval '1 hour'
        GROUP BY sdr_id
        ORDER BY sdr_id
    """)).fetchall()
    print('Active SDRs last hour:', rows)

    rows = db.execute(text("""
        SELECT id, timestamp, frequency_mhz, power_dbfs, snr_db, sdr_id
        FROM rf_sweep_data
        WHERE sdr_id='site_1'
        ORDER BY id DESC
        LIMIT 40
    """)).fetchall()
    print('Latest site_1 rows:')
    for r in rows:
        print(r)
finally:
    db.close()
