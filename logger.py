import os
import time
import sqlite3
import hashlib
import requests
from datetime import datetime
from codecarbon import OfflineEmissionsTracker

DB_FILE = "green_telemetry.db"

def init_local_db(db_path: str = DB_FILE):
    """Initializes local SQLite database schema for Software Carbon Intensity (SCI) tracking."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS CO_SOFTWARE_CARBON_INTENSITY (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            EFFECTIVE_DATE TEXT,
            EMBODIED_EMISSIONS_GCO2E REAL,
            ENERGY_CONSUMED_KWH REAL,
            EXECUTION_DATE TEXT,
            FUNCTIONAL_UNIT_TX INTEGER,
            FUNCTIONAL_UNIT_NAME TEXT, 
            PROCESS_DESC TEXT,
            PROJECT_NAME TEXT,
            REGION TEXT,
            SCI_SCORE_GCO2E_TX REAL,
            SCI_TRACKER_ID TEXT,
            TOTAL_CARBON_FOOTPRINT_GCO2E REAL,
            IS_AI INTEGER DEFAULT 0,
            AI_MODEL_NAME TEXT,
            PROMPT_TOKENS INTEGER DEFAULT 0,
            COMPLETION_TOKENS INTEGER DEFAULT 0,
            IS_SYNCED INTEGER DEFAULT 0,
            MEASUREMENT_PERIOD TEXT
        )
    """)
    
    # Safe fallback to add columns if the user runs this on an older database version
    try:
        cursor.execute("ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN FUNCTIONAL_UNIT_NAME TEXT")
    except sqlite3.OperationalError:
        pass 
        
    try:
        cursor.execute("ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN MEASUREMENT_PERIOD TEXT")
    except sqlite3.OperationalError:
        pass 
        
    conn.commit()
    conn.close()

# Ensure local database schema exists upon module loading
init_local_db()

class GreenLogger:
    """
    GreenLogger context manager for tracking Software Carbon Intensity (SCI)
    according to Green Software Foundation (GSF) specifications.
    Writes telemetry directly to local SQLite database ('green_telemetry.db').
    """
    def __init__(
        self, 
        project_id: str, 
        step_name: str, 
        functional_unit_name: str, # MANDATORY: No default value allowed
        functional_units: int,     # MANDATORY: No default value allowed
        is_ai: bool = False,
        model_name: str = "gpt-4o-mini",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        measurement_period: str = None,  # Optional explicit timeframe (e.g., "1 Month")
        db_path: str = DB_FILE
    ):
        # Strict validation for functional_unit_name to prevent lazy generic labels
        if not functional_unit_name or functional_unit_name.strip().lower() in ["", "transaction", "test"]:
            raise ValueError(
                "🚨 GREEN OPS ERROR: You must specify a real 'functional_unit_name' for your project "
                "(e.g., 'pipeline execution', 'API request', 'CSV rows'). Check the SCI documentation."
            )
            
        # Strict validation for functional_units to prevent ZeroDivisionError and enforce conscious measurement
        if not isinstance(functional_units, int) or functional_units <= 0:
            raise ValueError(
                "🚨 GREEN OPS ERROR: 'functional_units' must be an integer greater than 0. "
                "Define how many units (rows, requests, executions) you are measuring."
            )

        self.project_id = project_id
        self.step_name = step_name
        self.functional_units = functional_units
        self.functional_unit_name = functional_unit_name
        self.is_ai = is_ai
        self.model_name = model_name
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.measurement_period = measurement_period # Can be None if realtime tracking is desired
        self.db_path = db_path
        
        # Initialize CodeCarbon tracker only for local non-AI execution
        if not self.is_ai:
            self.tracker = OfflineEmissionsTracker(country_iso_code="ESP", log_level="error")

    def __enter__(self):
        """Starts tracking timestamp and CodeCarbon tracker if running standard code."""
        self.start_time = time.time()
        if not self.is_ai:
            self.tracker.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stops tracking, computes SCI metrics, and writes telemetry directly to SQLite."""
        execution_time_seconds = time.time() - self.start_time
        
        if not self.is_ai:
            # 1. Standard Hardware Execution (CPU + RAM + Embodied M)
            emissions_kg = self.tracker.stop()
            carbon_g = emissions_kg * 1000.0 if emissions_kg else 0.0
            
            energy_kwh = (
                self.tracker.final_emissions_data.cpu_energy + 
                self.tracker.final_emissions_data.ram_energy
                if hasattr(self.tracker, 'final_emissions_data') and self.tracker.final_emissions_data 
                else 0.0
            )
            M_allocated = 0.05 * execution_time_seconds  # Estimated embodied carbon allocation based on execution time
        else:
            # 2. AI / LLM Inference Execution (SCI for AI via EcoLogits proxy)
            energy_kwh, carbon_g = self._fetch_ecologits_impact()
            M_allocated = 0.0

        # SCI Equation: (Operational Carbon + Embodied Carbon) / Functional Units (R)
        sci_score = (carbon_g + M_allocated) / self.functional_units
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        project_hash = hashlib.md5(self.project_id.encode('utf-8')).hexdigest()[:8]
        tracker_id = f"{self.project_id}-{project_hash}"

        # TIME LOGIC: Use explicit measurement window if provided, otherwise fallback to script execution time
        final_period = self.measurement_period
        if final_period is None:
            if execution_time_seconds >= 60:
                final_period = f"{execution_time_seconds/60:.2f} Minutes (Script Execution)"
            else:
                final_period = f"{execution_time_seconds:.2f} Seconds (Script Execution)"

        payload = {
            "EFFECTIVE_DATE": current_time,
            "EMBODIED_EMISSIONS_GCO2E": float(M_allocated),
            "ENERGY_CONSUMED_KWH": float(energy_kwh),
            "EXECUTION_DATE": current_time,
            "FUNCTIONAL_UNIT_TX": int(self.functional_units),
            "FUNCTIONAL_UNIT_NAME": self.functional_unit_name, 
            "PROCESS_DESC": f"{self.step_name} {'[AI]' if self.is_ai else '[Standard]'}",
            "PROJECT_NAME": self.project_id,
            "REGION": "ESP",
            "SCI_SCORE_GCO2E_TX": float(sci_score),
            "SCI_TRACKER_ID": tracker_id,
            "TOTAL_CARBON_FOOTPRINT_GCO2E": float(carbon_g),
            "IS_AI": 1 if self.is_ai else 0,
            "AI_MODEL_NAME": self.model_name if self.is_ai else None,
            "PROMPT_TOKENS": int(self.prompt_tokens) if self.is_ai else 0,
            "COMPLETION_TOKENS": int(self.completion_tokens) if self.is_ai else 0,
            "MEASUREMENT_PERIOD": final_period
        }

        # Directly save payload into local SQLite database
        self._save_to_local_db(payload)

    def _save_to_local_db(self, payload: dict):
        """Persists SCI metrics directly to SQLite database file without network requests."""
        try:
            init_local_db(self.db_path)
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO CO_SOFTWARE_CARBON_INTENSITY (
                    EFFECTIVE_DATE, EMBODIED_EMISSIONS_GCO2E, ENERGY_CONSUMED_KWH, 
                    EXECUTION_DATE, FUNCTIONAL_UNIT_TX, FUNCTIONAL_UNIT_NAME, PROCESS_DESC, PROJECT_NAME, 
                    REGION, SCI_SCORE_GCO2E_TX, SCI_TRACKER_ID, TOTAL_CARBON_FOOTPRINT_GCO2E,
                    IS_AI, AI_MODEL_NAME, PROMPT_TOKENS, COMPLETION_TOKENS, IS_SYNCED, MEASUREMENT_PERIOD
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """, (
                payload["EFFECTIVE_DATE"], payload["EMBODIED_EMISSIONS_GCO2E"], payload["ENERGY_CONSUMED_KWH"],
                payload["EXECUTION_DATE"], payload["FUNCTIONAL_UNIT_TX"], payload["FUNCTIONAL_UNIT_NAME"], payload["PROCESS_DESC"], payload["PROJECT_NAME"],
                payload["REGION"], payload["SCI_SCORE_GCO2E_TX"], payload["SCI_TRACKER_ID"], payload["TOTAL_CARBON_FOOTPRINT_GCO2E"],
                payload["IS_AI"], payload["AI_MODEL_NAME"], payload["PROMPT_TOKENS"], payload["COMPLETION_TOKENS"], payload["MEASUREMENT_PERIOD"]
            ))
            conn.commit()
            conn.close()
            print(f"✅ [GREEN LOG] Recorded SCI footprint for '{payload['PROJECT_NAME']}' in '{self.db_path}'.")
        except Exception as e:
            print(f"❌ [GREEN LOG] Failed writing to local SQLite: {e}")

    def _fetch_ecologits_impact(self):
        """Calculates energy (kWh) and carbon (gCO2e) using EcoLogits API proxy."""
        url = "https://api.ecologits.ai/v1beta/estimations"
        data_payload = {
            "provider": "openai",
            "model_name": self.model_name,
            "input_token_count": self.prompt_tokens,
            "output_token_count": self.completion_tokens
        }
        try:
            res = requests.post(url, json=data_payload, timeout=5)
            if res.status_code == 200:
                impact = res.json().get('impacts', {})
                e_data = impact.get('energy', {}).get('value', {'min': 0, 'max': 0})
                energy_avg = (e_data['min'] + e_data['max']) / 2.0
                gwp_data = impact.get('gwp', {}).get('value', {'min': 0, 'max': 0})
                carbon_avg_g = ((gwp_data['min'] + gwp_data['max']) / 2.0) * 1000.0
                return energy_avg, carbon_avg_g
        except Exception as e:
            print(f"⚠️ [ECOLOGITS] Connection warning: {e}")
        return 0.0, 0.0