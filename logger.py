import os
import time
import sqlite3
import hashlib
import requests
from datetime import datetime
from codecarbon import OfflineEmissionsTracker, EmissionsTracker

DB_FILE = "green_telemetry.db"
SDK_VERSION = "v2.0.0"

def init_local_db(db_path: str = DB_FILE):
    """Initializes SQLite with v1.0.0 + v2.0.0 (CLEER + Boavizta) schema."""
    with sqlite3.connect(db_path, timeout=15.0) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS CO_SOFTWARE_CARBON_INTENSITY (
                ID INTEGER PRIMARY KEY AUTOINCREMENT,
                EFFECTIVE_DATE TEXT, EMBODIED_EMISSIONS_GCO2E REAL, ENERGY_CONSUMED_KWH REAL,
                EXECUTION_DATE TEXT, FUNCTIONAL_UNIT_TX INTEGER, FUNCTIONAL_UNIT_NAME TEXT, 
                PROCESS_DESC TEXT, PROJECT_NAME TEXT, REGION TEXT, SCI_SCORE_GCO2E_TX REAL,
                SCI_TRACKER_ID TEXT, TOTAL_CARBON_FOOTPRINT_GCO2E REAL, IS_AI INTEGER DEFAULT 0,
                AI_MODEL_NAME TEXT, PROMPT_TOKENS INTEGER DEFAULT 0, COMPLETION_TOKENS INTEGER DEFAULT 0,
                IS_SYNCED INTEGER DEFAULT 0, MEASUREMENT_PERIOD TEXT, PROVIDER TEXT DEFAULT 'Unknown',
                MEASUREMENT_METHOD TEXT DEFAULT 'unknown', SDK_VERSION TEXT DEFAULT 'v1.0.0'
            )
        """)
        
        alter_queries = [
            "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN CACHED_TOKENS INTEGER DEFAULT 0",
            "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN BASELINE_SCI_GCO2E REAL DEFAULT 0.0",
            "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN CLEER_SCI_GCO2E REAL DEFAULT 0.0",
            "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN OPTIMIZATION_YIELD_PCT REAL DEFAULT 0.0",
            "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN INSTANCE_TYPE TEXT DEFAULT 'Unknown'"
        ]
        for query in alter_queries:
            try:
                cursor.execute(query)
            except sqlite3.OperationalError:
                pass 
        conn.commit()

class GreenLogger:
    def __init__(
        self, project_id: str, step_name: str, functional_unit_name: str, functional_units: int,     
        is_ai: bool = False, model_name: str = None, prompt_tokens: int = 0, cached_tokens: int = 0,
        completion_tokens: int = 0, measurement_period: str = None, provider: str = "Unknown",
        country_iso_code: str = None, embodied_carbon_rate: float = 0.0, instance_type: str = None,
        db_path: str = DB_FILE
    ):
        if not functional_unit_name or functional_unit_name.strip().lower() in ["", "transaction", "test"]:
            raise ValueError("🚨 GREEN OPS ERROR: You must specify a real 'functional_unit_name'.")
        if not isinstance(functional_units, int) or functional_units <= 0:
            raise ValueError("🚨 GREEN OPS ERROR: 'functional_units' must be > 0.")
        if is_ai and not model_name:
            raise ValueError("🚨 GREEN OPS ERROR: When 'is_ai=True', provide a 'model_name'.")

        self.project_id = project_id
        self.step_name = step_name
        self.functional_units = functional_units
        self.functional_unit_name = functional_unit_name
        self.is_ai = is_ai
        self.model_name = model_name
        self.prompt_tokens = prompt_tokens
        self.cached_tokens = cached_tokens
        self.completion_tokens = completion_tokens
        self.measurement_period = measurement_period 
        self.provider = provider
        self.country_iso_code = country_iso_code
        self.embodied_carbon_rate = embodied_carbon_rate
        self.instance_type = instance_type
        self.db_path = db_path
        self.row_id = None
        
        if self.country_iso_code:
            self.tracker = OfflineEmissionsTracker(country_iso_code=self.country_iso_code, log_level="error")
        else:
            self.tracker = EmissionsTracker(log_level="error")

    def _fetch_boavizta_m(self, duration_s):
        """Fetches exact hardware embodied carbon (M) from Boavizta API if instance_type is known."""
        if not self.instance_type:
            return None
            
        prov_map = {"AWS": "aws", "GCP": "gcp", "AZURE": "azure"}
        prov = prov_map.get(str(self.provider).upper(), "aws")
        
        try:
            url = "https://api.boavizta.org/v1/cloud/instance"
            payload = {"provider": prov, "instance_type": self.instance_type}
            res = requests.post(url, json=payload, timeout=3)
            
            if res.status_code == 200:
                data = res.json()
                # Extract manufacture phase (Embodied Carbon) in kgCO2eq
                m_kg = data.get('impacts', {}).get('gwp', {}).get('manufacture', {}).get('value', 0.0)
                if m_kg > 0:
                    m_g = m_kg * 1000.0
                    lifespan_s = 4 * 365 * 24 * 3600  # Amortized over standard 4 years
                    return (m_g / lifespan_s) * duration_s
        except Exception as e:
            print(f"⚠️ [BOAVIZTA] Fallo al obtener LCA para '{self.instance_type}': {e}. Usando CLEER fallback.")
        return None

    def _calculate_cleer_impact(self, exact_m_value=None):
        """Path B: Generative AI CLEER Mathematical Model (With Boavizta Injection Capability)"""
        e_in_kwh, e_out_kwh, discount_r = 0.00032, 0.00096, 0.15
        gross_in_tokens = self.prompt_tokens + self.cached_tokens
        
        e_acc_baseline = ((gross_in_tokens / 1000.0) * e_in_kwh) + ((self.completion_tokens / 1000.0) * e_out_kwh)
        e_acc_cleer = ((self.prompt_tokens / 1000.0) * e_in_kwh) + \
                      ((self.cached_tokens / 1000.0) * (e_in_kwh * discount_r)) + \
                      ((self.completion_tokens / 1000.0) * e_out_kwh)
                      
        e_dev_base, e_dev_cleer = e_acc_baseline * 1.5, e_acc_cleer * 1.5
        
        e_fac_base = (e_acc_baseline * 1.5 * 1.1 * 1.45) + (e_dev_base * 1.5 * 1.45)
        e_fac_cleer = (e_acc_cleer * 1.5 * 1.1 * 1.45) + (e_dev_cleer * 1.5 * 1.45)
        
        # HYBRID LOGIC: Real Data > Fallback
        if exact_m_value is not None:
            m_base = exact_m_value
            m_cleer = exact_m_value
        else:
            m_base = (e_acc_baseline + e_dev_base) * 90.0 + (e_fac_base * 7.0)
            m_cleer = (e_acc_cleer + e_dev_cleer) * 90.0 + (e_fac_cleer * 7.0)
        
        grid_intensity = 350.0 
        return {
            "energy_kwh": e_fac_cleer,
            "carbon_g": (e_fac_cleer * grid_intensity) + m_cleer,
            "baseline_carbon_g": (e_fac_base * grid_intensity) + m_base,
            "m_cleer_used": m_cleer
        }

    def __enter__(self):
        self.start_time = time.time()
        self.tracker.start()
        self.row_id = self._insert_initial_row()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        execution_time_seconds = time.time() - self.start_time
        
        emissions_kg = self.tracker.stop()
        local_carbon_g = emissions_kg * 1000.0 if emissions_kg else 0.0
        local_energy_kwh = (self.tracker.final_emissions_data.cpu_energy + self.tracker.final_emissions_data.ram_energy) if hasattr(self.tracker, 'final_emissions_data') and self.tracker.final_emissions_data else 0.0
        
        local_method = "rapl" if os.path.exists('/sys/class/powercap/intel-rapl') else "tdp_estimate"
        method_str = local_method

        # Buscamos el LCA del hardware real primero
        m_exact = self._fetch_boavizta_m(execution_time_seconds)
        
        remote_energy_kwh, remote_carbon_g = 0.0, 0.0
        baseline_sci, cleer_sci, optimization_yield = 0.0, 0.0, 0.0
        
        if not self.is_ai:
            # Path A: Usar Boavizta M si existe, sino fallback a rate manual
            M_allocated = m_exact if m_exact is not None else (self.embodied_carbon_rate * execution_time_seconds)
            if m_exact is not None: method_str += " + boavizta_m"
        else:
            # Path B: Usar CLEER, inyectándole Boavizta M si existe
            cleer_metrics = self._calculate_cleer_impact(exact_m_value=m_exact)
            method_str = f"{local_method} + CLEER_v2"
            if m_exact is not None: method_str += " (Boavizta Hardware)"
            
            remote_energy_kwh = cleer_metrics["energy_kwh"]
            remote_carbon_g = cleer_metrics["carbon_g"]
            baseline_sci = cleer_metrics["baseline_carbon_g"] / self.functional_units
            cleer_sci = remote_carbon_g / self.functional_units
            optimization_yield = ((baseline_sci - cleer_sci) / baseline_sci) * 100 if baseline_sci > 0 else 0.0
            M_allocated = cleer_metrics["m_cleer_used"]

        total_energy = local_energy_kwh + remote_energy_kwh
        total_carbon = local_carbon_g + remote_carbon_g
        sci_score = (total_carbon + M_allocated) / self.functional_units

        detected_region = self.country_iso_code if self.country_iso_code else getattr(self.tracker._measure_power_secs, 'country_iso_code', 'Unknown')
        final_period = self.measurement_period or (f"{execution_time_seconds/60:.2f} Minutes" if execution_time_seconds >= 60 else f"{execution_time_seconds:.2f} Seconds")
        
        payload = {
            "EFFECTIVE_DATE": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "EMBODIED_EMISSIONS_GCO2E": float(M_allocated),
            "ENERGY_CONSUMED_KWH": float(total_energy),
            "EXECUTION_DATE": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "PROCESS_DESC": f"{self.step_name} {'[AI]' if self.is_ai else '[Standard]'}",
            "REGION": detected_region, "SCI_SCORE_GCO2E_TX": float(sci_score),
            "TOTAL_CARBON_FOOTPRINT_GCO2E": float(total_carbon),
            "MEASUREMENT_PERIOD": final_period, "MEASUREMENT_METHOD": method_str,
            "SDK_VERSION": SDK_VERSION, "PROMPT_TOKENS": self.prompt_tokens,
            "COMPLETION_TOKENS": self.completion_tokens, "CACHED_TOKENS": self.cached_tokens,
            "PROVIDER": self.provider, "AI_MODEL_NAME": self.model_name,
            "BASELINE_SCI_GCO2E": float(baseline_sci), "CLEER_SCI_GCO2E": float(cleer_sci),
            "OPTIMIZATION_YIELD_PCT": float(optimization_yield),
            "INSTANCE_TYPE": self.instance_type or 'Unknown'
        }

        if self.row_id:
            self._update_final_row(payload)

    def _insert_initial_row(self):
        try:
            init_local_db(self.db_path) 
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                cursor = conn.cursor()
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                tracker_id = f"{self.project_id}-{hashlib.md5(self.project_id.encode('utf-8')).hexdigest()[:8]}"
                
                cursor.execute("""
                    INSERT INTO CO_SOFTWARE_CARBON_INTENSITY (
                        EFFECTIVE_DATE, EXECUTION_DATE, FUNCTIONAL_UNIT_TX, FUNCTIONAL_UNIT_NAME, PROCESS_DESC, PROJECT_NAME, 
                        SCI_TRACKER_ID, IS_AI, AI_MODEL_NAME, PROMPT_TOKENS, COMPLETION_TOKENS, CACHED_TOKENS, PROVIDER, MEASUREMENT_METHOD, SDK_VERSION, INSTANCE_TYPE
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'incomplete_run', ?, ?)
                """, (
                    current_time, current_time, int(self.functional_units), self.functional_unit_name, 
                    f"[INCOMPLETE] {self.step_name}", self.project_id, tracker_id, 1 if self.is_ai else 0, 
                    self.model_name if self.is_ai else None, int(self.prompt_tokens), int(self.completion_tokens), 
                    int(self.cached_tokens), self.provider, SDK_VERSION, self.instance_type or 'Unknown'
                ))
                return cursor.lastrowid
        except Exception as e:
            print(f"❌ [GREEN LOG] Failed inserting initial row: {e}")
            return None

    def _update_final_row(self, p: dict):
        try:
            with sqlite3.connect(self.db_path, timeout=15.0) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE CO_SOFTWARE_CARBON_INTENSITY 
                    SET EFFECTIVE_DATE=?, EMBODIED_EMISSIONS_GCO2E=?, ENERGY_CONSUMED_KWH=?, EXECUTION_DATE=?, PROCESS_DESC=?, 
                        REGION=?, SCI_SCORE_GCO2E_TX=?, TOTAL_CARBON_FOOTPRINT_GCO2E=?, MEASUREMENT_PERIOD=?, MEASUREMENT_METHOD=?, 
                        SDK_VERSION=?, PROMPT_TOKENS=?, COMPLETION_TOKENS=?, CACHED_TOKENS=?, PROVIDER=?, AI_MODEL_NAME=?,
                        BASELINE_SCI_GCO2E=?, CLEER_SCI_GCO2E=?, OPTIMIZATION_YIELD_PCT=?, INSTANCE_TYPE=?
                    WHERE ID=?
                """, (
                    p["EFFECTIVE_DATE"], p["EMBODIED_EMISSIONS_GCO2E"], p["ENERGY_CONSUMED_KWH"], p["EXECUTION_DATE"], 
                    p["PROCESS_DESC"], p["REGION"], p["SCI_SCORE_GCO2E_TX"], p["TOTAL_CARBON_FOOTPRINT_GCO2E"], 
                    p["MEASUREMENT_PERIOD"], p["MEASUREMENT_METHOD"], p["SDK_VERSION"], p["PROMPT_TOKENS"], 
                    p["COMPLETION_TOKENS"], p["CACHED_TOKENS"], p["PROVIDER"], p["AI_MODEL_NAME"],
                    p["BASELINE_SCI_GCO2E"], p["CLEER_SCI_GCO2E"], p["OPTIMIZATION_YIELD_PCT"], p["INSTANCE_TYPE"], self.row_id
                ))
        except Exception as e:
            print(f"❌ [GREEN LOG] Failed updating final SQLite row: {e}")