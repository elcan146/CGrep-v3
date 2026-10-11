"""
Safe AI tools that prevent SQL injection and prompt injection attacks.
Replaces dynamic SQL generation with parameterized, constrained functions.
"""

import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from sqlalchemy import text, func, desc
from models import SessionLocal, SweepData, SignalEvent

class SafeRFAnalyzer:
    """Safe RF analysis tools for AI integration without SQL injection risk."""
    
    @staticmethod
    def get_frequency_telemetry(frequency_mhz: float, duration_minutes: int = 60) -> str:
        """
        Get telemetry data for a specific frequency.
        
        Args:
            frequency_mhz: Target frequency in MHz (e.g., 106.3)
            duration_minutes: Time window in minutes (max 1440 = 24h)
        """
        if not (88.0 <= frequency_mhz <= 108.0):
            return json.dumps({"error": "Frequency must be between 88.0 and 108.0 MHz"})
        
        duration_minutes = min(max(duration_minutes, 1), 1440)  # Clamp between 1 min and 24h
        
        db = SessionLocal()
        try:
            since = datetime.utcnow() - timedelta(minutes=duration_minutes)
            
            # Convert MHz to kHz for database query
            freq_khz = int(frequency_mhz * 1000)
            freq_range = 50  # ±50 kHz tolerance
            
            query = db.query(SweepData).filter(
                SweepData.timestamp >= since,
                SweepData.frequency_mhz.between(freq_khz - freq_range, freq_khz + freq_range)
            ).order_by(desc(SweepData.timestamp)).limit(100)
            
            rows = query.all()
            
            if not rows:
                return json.dumps({
                    "frequency_mhz": frequency_mhz,
                    "duration_minutes": duration_minutes,
                    "data_points": 0,
                    "message": "No data found for this frequency"
                })
            
            # Process results
            data = []
            for row in rows:
                data.append({
                    "timestamp": row.timestamp.isoformat() + "Z",
                    "frequency_mhz": round(row.frequency_mhz / 1000.0, 3),
                    "power_dbfs": round(row.power_dbfs, 2),
                    "snr_db": round(row.snr_db, 2)
                })
            
            # Calculate statistics
            powers = [r.power_dbfs for r in rows]
            snrs = [r.snr_db for r in rows]
            
            stats = {
                "frequency_mhz": frequency_mhz,
                "duration_minutes": duration_minutes,
                "data_points": len(rows),
                "power_stats": {
                    "min": round(min(powers), 2),
                    "max": round(max(powers), 2),
                    "avg": round(sum(powers) / len(powers), 2),
                    "current": round(powers[0], 2)  # Latest reading
                },
                "snr_stats": {
                    "min": round(min(snrs), 2),
                    "max": round(max(snrs), 2),
                    "avg": round(sum(snrs) / len(snrs), 2),
                    "current": round(snrs[0], 2)  # Latest reading
                },
                "data": data
            }
            
            return json.dumps(stats, default=str)
            
        except Exception as e:
            return json.dumps({"error": f"Database query failed: {str(e)[:200]}"})
        finally:
            db.close()
    
    @staticmethod
    def get_station_health(station_name: str = None, hours: int = 24) -> str:
        """
        Get health status for radio stations.
        
        Args:
            station_name: Optional station name filter
            hours: Time window in hours (max 168 = 1 week)
        """
        hours = min(max(hours, 1), 168)  # Clamp between 1h and 1 week
        
        # Known stations in Region
        stations = {
            "DAB+ 12A": 88.7,
            "FM 90.0": 90.0,
            "Lider FM": 90.7,
            "Naxçıvan": 92.5,
            "ANS FM": 93.0,
            "CBC FM": 93.3,
            "Retro FM": 97.7,
            "Radio AZ": 100.0,
            "Melodiya": 100.5,
            "Avro FM": 101.1,
            "Sport FM": 103.3,
            "Bakı FM": 104.0,
            "Şərq FM": 105.0,
            "FM 105.5": 105.5,
            "Az. Radio": 106.0,
            "Region FM": 106.3,
            "Ulduz FM": 107.0,
            "FM 107.8": 107.8
        }
        
        db = SessionLocal()
        try:
            since = datetime.utcnow() - timedelta(hours=hours)
            results = []
            
            for name, freq in stations.items():
                if station_name and name != station_name:
                    continue
                
                # Query for this station
                freq_khz = int(freq * 1000)
                freq_range = 100  # ±100 kHz tolerance
                
                rows = db.query(SweepData).filter(
                    SweepData.timestamp >= since,
                    SweepData.frequency_mhz.between(freq_khz - freq_range, freq_khz + freq_range)
                ).order_by(desc(SweepData.timestamp)).limit(50).all()
                
                if rows:
                    powers = [r.power_dbfs for r in rows]
                    snrs = [r.snr_db for r in rows]
                    
                    # Calculate outage percentage (below -65 dBFS)
                    outage_count = sum(1 for p in powers if p < -65)
                    outage_percent = round((outage_count / len(powers)) * 100, 1)
                    
                    results.append({
                        "station_name": name,
                        "frequency_mhz": freq,
                        "data_points": len(rows),
                        "power_current": round(powers[0], 2),
                        "power_avg": round(sum(powers) / len(powers), 2),
                        "power_min": round(min(powers), 2),
                        "snr_current": round(snrs[0], 2),
                        "snr_avg": round(sum(snrs) / len(snrs), 2),
                        "outage_percent": outage_percent,
                        "status": "healthy" if outage_percent < 5 else "degraded" if outage_percent < 20 else "critical"
                    })
                else:
                    results.append({
                        "station_name": name,
                        "frequency_mhz": freq,
                        "status": "no_data",
                        "message": "No data available"
                    })
            
            return json.dumps({
                "query_hours": hours,
                "station_filter": station_name,
                "stations": results
            })
            
        except Exception as e:
            return json.dumps({"error": f"Station health query failed: {str(e)[:200]}"})
        finally:
            db.close()
    
    @staticmethod
    def detect_interference(start_time: str = None, end_time: str = None) -> str:
        """
        Detect interference patterns in the spectrum.
        
        Args:
            start_time: ISO start time (optional, defaults to 1 hour ago)
            end_time: ISO end time (optional, defaults to now)
        """
        db = SessionLocal()
        try:
            # Default to last hour if not specified
            if not end_time:
                end_time = datetime.utcnow()
            else:
                end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
            
            if not start_time:
                start_time = end_time - timedelta(hours=1)
            else:
                start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            
            # Query for potential interference (high power signals)
            rows = db.query(SweepData).filter(
                SweepData.timestamp.between(start_time, end_time),
                SweepData.power_dbfs > -60  # Signals stronger than -60 dBFS
            ).order_by(desc(SweepData.power_dbfs)).limit(100).all()
            
            if not rows:
                return json.dumps({
                    "interference_detected": False,
                    "message": "No interference detected in the specified time range"
                })
            
            # Group by frequency to find persistent interference
            freq_groups = {}
            for row in rows:
                freq_mhz = round(row.frequency_mhz / 1000.0, 1)
                if freq_mhz not in freq_groups:
                    freq_groups[freq_mhz] = []
                freq_groups[freq_mhz].append(row.power_dbfs)
            
            # Analyze each frequency
            interference_events = []
            for freq, powers in freq_groups.items():
                if len(powers) >= 3:  # Persistent signal
                    avg_power = sum(powers) / len(powers)
                    max_power = max(powers)
                    
                    interference_events.append({
                        "frequency_mhz": freq,
                        "detections": len(powers),
                        "avg_power_dbfs": round(avg_power, 2),
                        "max_power_dbfs": round(max_power, 2),
                        "severity": "high" if max_power > -50 else "medium" if max_power > -55 else "low"
                    })
            
            return json.dumps({
                "interference_detected": len(interference_events) > 0,
                "time_range": {
                    "start": start_time.isoformat() + "Z",
                    "end": end_time.isoformat() + "Z"
                },
                "events": interference_events
            })
            
        except Exception as e:
            return json.dumps({"error": f"Interference detection failed: {str(e)[:200]}"})
        finally:
            db.close()

# Create safe tool functions for Gemini
def get_safe_ai_tools():
    """Return safe AI tool definitions for Gemini."""
    return [
        {
            "name": "get_frequency_telemetry",
            "description": "Get detailed telemetry for a specific FM frequency including power and SNR statistics",
            "parameters": {
                "type": "object",
                "properties": {
                    "frequency_mhz": {
                        "type": "number",
                        "description": "FM frequency in MHz between 88.0 and 108.0"
                    },
                    "duration_minutes": {
                        "type": "integer",
                        "description": "Time window in minutes (1-1440)"
                    }
                },
                "required": ["frequency_mhz"]
            }
        },
        {
            "name": "get_station_health",
            "description": "Get health status for Region radio stations including outage percentages",
            "parameters": {
                "type": "object",
                "properties": {
                    "station_name": {
                        "type": "string",
                        "description": "Optional station name filter (e.g., 'Region FM')"
                    },
                    "hours": {
                        "type": "integer",
                        "description": "Time window in hours (1-168)"
                    }
                }
            },
            "required": []
        },
        {
            "name": "detect_interference",
            "description": "Detect interference patterns in the spectrum",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {
                        "type": "string",
                        "description": "ISO start time (optional)"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "ISO end time (optional)"
                    }
                }
            },
            "required": []
        }
    ]

def execute_safe_tool(tool_name: str, args: dict) -> str:
    """Execute a safe AI tool and return the result."""
    analyzer = SafeRFAnalyzer()
    
    if tool_name == "get_frequency_telemetry":
        return analyzer.get_frequency_telemetry(
            frequency_mhz=args.get("frequency_mhz"),
            duration_minutes=args.get("duration_minutes", 60)
        )
    elif tool_name == "get_station_health":
        return analyzer.get_station_health(
            station_name=args.get("station_name"),
            hours=args.get("hours", 24)
        )
    elif tool_name == "detect_interference":
        return analyzer.detect_interference(
            start_time=args.get("start_time"),
            end_time=args.get("end_time")
        )
    else:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
