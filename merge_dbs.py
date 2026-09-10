import sqlite3
import glob
import os
import shutil

def merge_databases(output_db="green_telemetry.db", incoming_dir="incoming_dbs", archive_dir="archived_dbs"):
    output_path = os.path.abspath(output_db)
    
    os.makedirs(incoming_dir, exist_ok=True)
    os.makedirs(archive_dir, exist_ok=True)

    master_conn = sqlite3.connect(output_path)
    master_cursor = master_conn.cursor()
    
    # 1. Added MEASUREMENT_PERIOD, PROVIDER, MEASUREMENT_METHOD, and SDK_VERSION
    master_cursor.execute("""
        CREATE TABLE IF NOT EXISTS CO_SOFTWARE_CARBON_INTENSITY (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            EFFECTIVE_DATE TEXT, EMBODIED_EMISSIONS_GCO2E REAL, ENERGY_CONSUMED_KWH REAL,
            EXECUTION_DATE TEXT, FUNCTIONAL_UNIT_TX INTEGER, FUNCTIONAL_UNIT_NAME TEXT, PROCESS_DESC TEXT,
            PROJECT_NAME TEXT, REGION TEXT, SCI_SCORE_GCO2E_TX REAL, SCI_TRACKER_ID TEXT,
            TOTAL_CARBON_FOOTPRINT_GCO2E REAL, IS_AI INTEGER DEFAULT 0,
            AI_MODEL_NAME TEXT, PROMPT_TOKENS INTEGER DEFAULT 0, COMPLETION_TOKENS INTEGER DEFAULT 0,
            IS_SYNCED INTEGER DEFAULT 0, MEASUREMENT_PERIOD TEXT, PROVIDER TEXT DEFAULT 'Unknown',
            MEASUREMENT_METHOD TEXT DEFAULT 'unknown', SDK_VERSION TEXT DEFAULT 'v1.0.0'
        )
    """)
    
    # 2. Fallbacks to update older master databases automatically
    alter_queries = [
        "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN FUNCTIONAL_UNIT_NAME TEXT",
        "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN MEASUREMENT_PERIOD TEXT",
        "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN PROVIDER TEXT DEFAULT 'Unknown'",
        "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN MEASUREMENT_METHOD TEXT DEFAULT 'unknown'",
        "ALTER TABLE CO_SOFTWARE_CARBON_INTENSITY ADD COLUMN SDK_VERSION TEXT DEFAULT 'v1.0.0'"
    ]
    for query in alter_queries:
        try:
            master_cursor.execute(query)
        except sqlite3.OperationalError:
            pass
    
    pattern = os.path.join(incoming_dir, "*.db")
    db_files = glob.glob(pattern)
    
    if not db_files:
        print(f"ℹ️ No new files in '{incoming_dir}/'. Everything is up to date.")
        master_conn.close()
        return

    records_added = 0
    files_processed = 0

    for db_file in db_files:
        print(f"📦 Processing: {db_file}")
        
        filename_lower = os.path.basename(db_file).lower()
        is_post_optimization = "post" in filename_lower or "opt" in filename_lower
        
        try:
            conn = sqlite3.connect(db_file)
            cursor = conn.cursor()
            
            cursor.execute("PRAGMA table_info(CO_SOFTWARE_CARBON_INTENSITY)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if "FUNCTIONAL_UNIT_NAME" in columns:
                fu_select = "FUNCTIONAL_UNIT_NAME"
            else:
                fu_select = "'transaction' AS FUNCTIONAL_UNIT_NAME"
                
            # 3. Dynamic checks for new columns. Legacy DBs automatically become v1.0.0
            if "MEASUREMENT_PERIOD" in columns:
                period_select = "MEASUREMENT_PERIOD"
            else:
                period_select = "NULL AS MEASUREMENT_PERIOD"
                
            if "PROVIDER" in columns:
                provider_select = "PROVIDER"
            else:
                provider_select = "'Unknown' AS PROVIDER"
                
            if "MEASUREMENT_METHOD" in columns:
                method_select = "MEASUREMENT_METHOD"
            else:
                method_select = "'legacy_v1_data' AS MEASUREMENT_METHOD"
                
            if "SDK_VERSION" in columns:
                version_select = "SDK_VERSION"
            else:
                version_select = "'v1.0.0' AS SDK_VERSION"
                
            cursor.execute(f"""
                SELECT EFFECTIVE_DATE, EMBODIED_EMISSIONS_GCO2E, ENERGY_CONSUMED_KWH,
                       EXECUTION_DATE, FUNCTIONAL_UNIT_TX, {fu_select}, PROCESS_DESC, PROJECT_NAME,
                       REGION, SCI_SCORE_GCO2E_TX, SCI_TRACKER_ID, TOTAL_CARBON_FOOTPRINT_GCO2E,
                       IS_AI, AI_MODEL_NAME, PROMPT_TOKENS, COMPLETION_TOKENS, IS_SYNCED, {period_select}, {provider_select}, {method_select}, {version_select}
                FROM CO_SOFTWARE_CARBON_INTENSITY
            """)
            rows = cursor.fetchall()
            
            for row in rows:
                row_list = list(row)
                
                if is_post_optimization and "(POST-OPTIMIZATION)" not in row_list[6]:
                    row_list[6] = f"{row_list[6]} (POST-OPTIMIZATION)"
                
                # 4. Insert 21 elements including SDK_VERSION
                master_cursor.execute("""
                    INSERT INTO CO_SOFTWARE_CARBON_INTENSITY (
                        EFFECTIVE_DATE, EMBODIED_EMISSIONS_GCO2E, ENERGY_CONSUMED_KWH,
                        EXECUTION_DATE, FUNCTIONAL_UNIT_TX, FUNCTIONAL_UNIT_NAME, PROCESS_DESC, PROJECT_NAME,
                        REGION, SCI_SCORE_GCO2E_TX, SCI_TRACKER_ID, TOTAL_CARBON_FOOTPRINT_GCO2E,
                        IS_AI, AI_MODEL_NAME, PROMPT_TOKENS, COMPLETION_TOKENS, IS_SYNCED, MEASUREMENT_PERIOD, PROVIDER, MEASUREMENT_METHOD, SDK_VERSION
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, row_list)
                records_added += 1
            
            conn.close()
            
            filename = os.path.basename(db_file)
            archive_path = os.path.join(archive_dir, filename)
            
            if os.path.exists(archive_path):
                os.remove(archive_path)
                
            shutil.move(db_file, archive_path)
            files_processed += 1
            print(f"   ✅ Moved to archive: {archive_path}")
            
        except Exception as e:
            print(f"   ⚠️ Error processing {db_file}: {e}")
            
    master_conn.commit()
    master_conn.close()
    print(f"\n🚀 Consolidation complete! Added {records_added} records from {files_processed} files.")

if __name__ == "__main__":
    merge_databases()