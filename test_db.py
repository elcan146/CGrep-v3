"""
test_db.py — Quick database diagnostic
"""

import os
from sqlalchemy import create_engine, text

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "changeme")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "sdr_db")

db_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

print(f"Connecting to: {DB_HOST}:{DB_PORT}/{DB_NAME}")
print(f"User: {DB_USER}")

try:
    engine = create_engine(db_url)
    
    # Check connection
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        print(f"[OK] Database connection works")
        
        # Check if table exists
        table_check = conn.execute(text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'rf_sweep_data'"
        )).fetchone()
        
        if table_check:
            print("[OK] Table rf_sweep_data exists")
            
            # Count records
            count = conn.execute(text("SELECT COUNT(*) FROM rf_sweep_data")).fetchone()[0]
            print(f"[INFO] Records in rf_sweep_data: {count}")
            
            if count > 0:
                # Show last 5 records
                print("\nLast 5 records:")
                result = conn.execute(text(
                    "SELECT id, timestamp, frequency_mhz, power_dbfs, floor_dbfs, snr_db "
                    "FROM rf_sweep_data ORDER BY id DESC LIMIT 5"
                ))
                for row in result:
                    print(f"  {row}")
            else:
                print("[INFO] Table is empty - no data logged yet")
        else:
            print("[ERROR] Table rf_sweep_data does NOT exist")
            
except Exception as e:
    print(f"[ERROR] {e}")
