import os
import time
import sqlite3
import hashlib
import requests
from datetime import datetime
from codecarbon import OfflineEmissionsTracker, EmissionsTracker

DB_FILE = "green_telemetry.db"
# FIX: Hardcoded internal SDK version. Updates automatically write to the DB.
SDK_VERSION = "v2.0.0"

def init_local_db(db_path: str = DB_FILE):
    """Initializes local SQLite database schema for Software Carbon Intensity (SCI) tracking."""
    conn = sqlite3.connect(db_path, timeout=15.0)
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
            MEASUREMENT_PERIOD TEXT,
            PROVIDER TEXT DEFAULT 'Unknown',
            MEASUREMENT_METHOD TEXT DEFAULT 'unknown',
            SDK_VERSION TEXT DEFAULT 'v1.0.0'
        )
    """)
    
    # Safe fallback to add columns for older schema versions
    alter_queries = [
        "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN FUNCTIONAL_UNIT_NAME TEXT",
        "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN MEASUREMENT_PERIOD TEXT",
        "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN PROVIDER TEXT DEFAULT 'Unknown'",
        "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN MEASUREMENT_METHOD TEXT DEFAULT 'unknown'",
        "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN SDK_VERSION TEXT DEFAULT 'v1.0.0'"
    ]
    for query in alter_queries:
        try:
            cursor.execute(query)
        except sqlite3.OperationalError:
            pass 
            
    conn.commit()
    conn.close()

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
        functional_unit_name: str, 
        functional_units: int,     
        is_ai: bool = False,
        model_name: str = None,               
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        measurement_period: str = None,  
        provider: str = "Unknown",
        country_iso_code: str = None,         
        embodied_carbon_rate: float = 0.0,    
        db_path: str = DB_FILE
    ):
        # Strict validation for functional_unit_name 
        if not functional_unit_name or functional_unit_name.strip().lower() in ["", "transaction", "test"]:
            raise ValueError(
                "🚨 GREEN OPS ERROR: You must specify a real 'functional_unit_name' for your project "
                "(e.g., 'pipeline execution', 'API request', 'CSV rows')."
            )
            
        # Strict validation for functional_units 
        if not isinstance(functional_units, int) or functional_units <= 0:
            raise ValueError(
                "🚨 GREEN OPS ERROR: 'functional_units' must be an integer greater than 0. "
            )
            
        # Strict validation for AI model name to prevent dirty data
        if is_ai and not model_name:
            raise ValueError(
                "🚨 GREEN OPS ERROR: When 'is_ai=True', you must explicitly provide a 'model_name' (e.g., 'claude-3-5-sonnet')."
            )

        self.project_id = project_id
        self.step_name = step_name
        self.functional_units = functional_units
        self.functional_unit_name = functional_unit_name
        self.is_ai = is_ai
        self.model_name = model_name
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.measurement_period = measurement_period 
        self.provider = provider
        self.country_iso_code = country_iso_code
        self.embodied_carbon_rate = embodied_carbon_rate
        self.db_path = db_path
        self.row_id = None
        
        # Initialize CodeCarbon tracker globally to capture local work (e.g., OCR) even in AI workflows.
        # If 'country_iso_code' is provided (e.g., "IND"), use OfflineEmissionsTracker to prevent outbound firewall blocks.
        # Otherwise, use EmissionsTracker to automatically detect the region via IP.
        if self.country_iso_code:
            self.tracker = OfflineEmissionsTracker(
                country_iso_code=self.country_iso_code, 
                log_level="error"
            )
        else:
            self.tracker = EmissionsTracker(log_level="error")

    def __enter__(self):
        """Starts tracking timestamp, starts CodeCarbon, and inserts INCOMPLETE row."""
        self.start_time = time.time()
        self.tracker.start()
        
        # Write partial row immediately to prevent data loss on SIGKILL.
        self.row_id = self._insert_initial_row()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stops tracking, aggregates local + remote metrics, and updates final SQLite row."""
        execution_time_seconds = time.time() - self.start_time
        
        # LOCAL CPU/RAM Measurement
        emissions_kg = self.tracker.stop()
        local_carbon_g = emissions_kg * 1000.0 if emissions_kg else 0.0
        local_energy_kwh = (
            self.tracker.final_emissions_data.cpu_energy + 
            self.tracker.final_emissions_data.ram_energy
            if hasattr(self.tracker, 'final_emissions_data') and self.tracker.final_emissions_data 
            else 0.0
        )
        
        # Detect if RAPL is actually accessible or if it fell back to TDP
        has_rapl = os.path.exists('/sys/class/powercap/intel-rapl')
        local_method = "rapl" if has_rapl else "tdp_estimate"
        method_str = local_method

        remote_energy_kwh, remote_carbon_g = 0.0, 0.0
        
        # REMOTE AI Measurement (Appended to Local)
        if self.is_ai:
            r_energy, r_carbon = self._fetch_ecologits_impact()
            if r_energy is None:
                # Explicitly flag missing API data rather than silently using 0.0
                method_str = f"{local_method} + ecologits_unavailable"
            else:
                method_str = f"{local_method} + ecologits"
                remote_energy_kwh, remote_carbon_g = r_energy, r_carbon

        # Aggregation
        total_energy = local_energy_kwh + remote_energy_kwh
        total_carbon = local_carbon_g + remote_carbon_g
        M_allocated = self.embodied_carbon_rate * execution_time_seconds

        sci_score = (total_carbon + M_allocated) / self.functional_units
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        final_period = self.measurement_period
        if final_period is None:
            if execution_time_seconds >= 60:
                final_period = f"{execution_time_seconds/60:.2f} Minutes (Script Execution)"
            else:
                final_period = f"{execution_time_seconds:.2f} Seconds (Script Execution)"
                
        # Resolve Region (Extract from tracker if auto-detected)
        detected_region = self.country_iso_code if self.country_iso_code else getattr(self.tracker._measure_power_secs, 'country_iso_code', 'Unknown')

        # SE AÑADEN LAS VARIABLES LATE-BINDING AL PAYLOAD
        payload = {
            "EFFECTIVE_DATE": current_time,
            "EMBODIED_EMISSIONS_GCO2E": float(M_allocated),
            "ENERGY_CONSUMED_KWH": float(total_energy),
            "EXECUTION_DATE": current_time,
            "PROCESS_DESC": f"{self.step_name} {'[AI]' if self.is_ai else '[Standard]'}",
            "REGION": detected_region,
            "SCI_SCORE_GCO2E_TX": float(sci_score),
            "TOTAL_CARBON_FOOTPRINT_GCO2E": float(total_carbon),
            "MEASUREMENT_PERIOD": final_period,
            "MEASUREMENT_METHOD": method_str,
            "SDK_VERSION": SDK_VERSION,
            "PROMPT_TOKENS": self.prompt_tokens if hasattr(self, 'prompt_tokens') else 0,
            "COMPLETION_TOKENS": self.completion_tokens if hasattr(self, 'completion_tokens') else 0,
            "PROVIDER": self.provider if hasattr(self, 'provider') else "Unknown",
            "AI_MODEL_NAME": self.ai_model_name if hasattr(self, 'ai_model_name') else None
        }

        # Update the row instead of creating a new one
        if self.row_id:
            self._update_final_row(payload)

    def _insert_initial_row(self):
        """Inserts an incomplete placeholder row at start to prevent data loss on SIGKILL."""
        try:
            init_local_db(self.db_path) 
            conn = sqlite3.connect(self.db_path, timeout=15.0)
            cursor = conn.cursor()
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            project_hash = hashlib.md5(self.project_id.encode('utf-8')).hexdigest()[:8]
            tracker_id = f"{self.project_id}-{project_hash}"
            
            cursor.execute("""
                INSERT INTO CO_SOFTWARE_CARBON_INTENSITY (
                    EFFECTIVE_DATE, EXECUTION_DATE, FUNCTIONAL_UNIT_TX, FUNCTIONAL_UNIT_NAME, PROCESS_DESC, PROJECT_NAME, 
                    SCI_TRACKER_ID, IS_AI, AI_MODEL_NAME, PROMPT_TOKENS, COMPLETION_TOKENS, PROVIDER, MEASUREMENT_METHOD, SDK_VERSION
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'incomplete_run', ?)
            """, (
                current_time, current_time, int(self.functional_units), self.functional_unit_name, 
                f"[INCOMPLETE] {self.step_name}", self.project_id, tracker_id, 
                1 if self.is_ai else 0, self.model_name if self.is_ai else None, 
                int(self.prompt_tokens) if self.is_ai else 0, int(self.completion_tokens) if self.is_ai else 0, 
                self.provider, SDK_VERSION
            ))
            row_id = cursor.lastrowid
            conn.commit()
            conn.close()
            return row_id
        except Exception as e:
            print(f"❌ [GREEN LOG] Failed inserting initial row: {e}")
            return None

    def _update_final_row(self, payload: dict):
        """Updates the placeholder row with actual measured metrics."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=15.0)
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE CO_SOFTWARE_CARBON_INTENSITY 
                SET EFFECTIVE_DATE = ?, EMBODIED_EMISSIONS_GCO2E = ?, ENERGY_CONSUMED_KWH = ?, 
                    EXECUTION_DATE = ?, PROCESS_DESC = ?, REGION = ?, SCI_SCORE_GCO2E_TX = ?, 
                    TOTAL_CARBON_FOOTPRINT_GCO2E = ?, MEASUREMENT_PERIOD = ?, MEASUREMENT_METHOD = ?, SDK_VERSION = ?
                WHERE ID = ?
            """, (
                payload["EFFECTIVE_DATE"], payload["EMBODIED_EMISSIONS_GCO2E"], payload["ENERGY_CONSUMED_KWH"],
                payload["EXECUTION_DATE"], payload["PROCESS_DESC"], payload["REGION"], payload["SCI_SCORE_GCO2E_TX"],
                payload["TOTAL_CARBON_FOOTPRINT_GCO2E"], payload["MEASUREMENT_PERIOD"], payload["MEASUREMENT_METHOD"], payload["SDK_VERSION"],
                self.row_id
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ [GREEN LOG] Failed updating final SQLite row: {e}")

    def _fetch_ecologits_impact(self):
        """Calculates energy (kWh) and carbon (gCO2e) using EcoLogits API proxy."""
        url = "https://api.ecologits.ai/v1beta/estimations"
        
        # Auto-infer provider for EcoLogits if user didn't specify one
        api_provider = self.provider.lower()
        if api_provider == "unknown" or not api_provider:
            m_lower = str(self.model_name).lower()
            if "claude" in m_lower: api_provider = "anthropic"
            elif "gpt" in m_lower: api_provider = "openai"
            elif "gemini" in m_lower: api_provider = "google"
            elif "llama" in m_lower: api_provider = "meta"
            elif "mistral" in m_lower: api_provider = "mistral"
            else: api_provider = "openai"
            
        data_payload = {
            "provider": api_provider,
            "model_name": self.model_name,
            "input_token_count": self.prompt_tokens,
            "output_token_count": self.completion_tokens
        }
        try:
            res = requests.post(url, json=data_payload, timeout=5)
            if res.status_code == 200:
                data = res.json() or {}
                impact = data.get('impacts')
                
                # Robustness against explicit nulls from the API
                if not impact:
                    print(f"⚠️ [ECOLOGITS] API returned no impact data for model '{self.model_name}'.")
                    return None, None
                    
                e_value = impact.get('energy', {})
                e_value = e_value.get('value') if e_value else {'min': 0, 'max': 0}
                energy_avg = (e_value.get('min', 0) + e_value.get('max', 0)) / 2.0
                
                gwp_value = impact.get('gwp', {})
                gwp_value = gwp_value.get('value') if gwp_value else {'min': 0, 'max': 0}
                carbon_avg_g = ((gwp_value.get('min', 0) + gwp_value.get('max', 0)) / 2.0) * 1000.0
                
                return energy_avg, carbon_avg_g
            else:
                print(f"⚠️ [ECOLOGITS] API rejected request (Status {res.status_code}): {res.text}")
                return None, None
        except Exception as e:
            print(f"⚠️ [ECOLOGITS] Connection error: {e}")
            return None, None