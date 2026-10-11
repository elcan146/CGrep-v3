import { useState, useEffect } from 'react';

let _cache = null;
let _promise = null;

export default function useSdrThresholds() {
  const [map, setMap] = useState(_cache || {});

  useEffect(() => {
    if (_cache) {
      setMap(_cache);
      return;
    }
    if (!_promise) {
      _promise = fetch('/api/sdr/list', { credentials: 'include' })
        .then(r => (r.ok ? r.json() : []))
        .then(list => {
          _cache = Object.fromEntries((list || []).map(s => [s.id, s.thresholds || {}]));
          return _cache;
        })
        .catch(() => ({}));
    }
    _promise.then(m => setMap(m));
  }, []);

  return map;
}
