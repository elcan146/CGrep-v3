-- Performance indexes for rf_sweep_data
-- Run manually with psql, or use the FastAPI startup hook in server.py which applies them automatically.

-- As requested: supports queries that filter/order by frequency_mhz directly.
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rf_sweep_freq_id
    ON rf_sweep_data (frequency_mhz, id DESC);

-- Supports the SDR list query (now cached in server.py, but still needs an index for cache refresh):
--   SELECT DISTINCT sdr_id FROM rf_sweep_data WHERE sdr_id IS NOT NULL ORDER BY sdr_id;
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rf_sweep_sdr_id
    ON rf_sweep_data (sdr_id);

-- REQUIRED by the recursive loose-index-scan CTE in server.py / memory_buffer.py.
-- This is the index that actually makes the latest-per-station query fast.
--   Index Only Scan using ix_rf_sweep_bucket_id_cover
--   Execution Time: ~75 ms (was ~11,400 ms with DISTINCT ON, ~121,000 ms with a non-covering index).
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rf_sweep_bucket_id_cover
    ON rf_sweep_data (CAST(round(frequency_mhz / 100.0) * 100 AS INTEGER), id DESC)
    INCLUDE (timestamp, frequency_mhz, power_dbfs, snr_db);

-- Optional: if the active-SDR query (WHERE timestamp >= :s AND sdr_id IS NOT NULL) is still slow,
-- replace the single-column sdr_id index with this composite index:
-- CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_rf_sweep_ts_sdr_id
--     ON rf_sweep_data (timestamp DESC, sdr_id);
