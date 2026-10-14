# Roche Green SDK 2.0 - Digital Sustainability

Welcome to the **Roche Green SDK 2.0**. This tool allows you to measure the carbon footprint of your software and calculate the Software Carbon Intensity (SCI) of your code, whether it is traditional processing (CPU/RAM) or Artificial Intelligence inference (SCI for AI).

The SDK is built on a **Dual-Path Architecture** that calculates environmental impact directly in your local environment, ensuring zero latency and eliminating reliance on fragile external APIs.

The calculation is based on the official standard from the Green Software Foundation:
$SCI = \frac{(E \cdot I) + M}{R}$

## 1. Installation

You do not need to set up complex infrastructure. The SDK runs locally and stores data in a local SQLite database.

1. Place the `green-dashboard-main` folder into the root directory of your project.
2. Open your terminal in that directory and install the package locally by running:
```bash
pip install -e ./green-dashboard-main

```



*(This will automatically install the required dependencies: `codecarbon` and `requests`).*

## 2. How to use the SDK in your code (Dual-Path)

Import `GreenLogger` in your main script and wrap the function or process you want to measure using a `with` block. Depending on the nature of your project, use one of the following templates:

### Option A: Standard Code Projects (Data Science, ETLs, APIs)

This activates **Path A**, using CodeCarbon to measure the actual physical CPU and RAM energy consumption on your local machine or container.

```python
from green_logger import GreenLogger

# Define how many units you are processing (e.g., CSV rows, transactions)
records_processed = 5000

with GreenLogger(
    project_id="Your_Project_Name",    # E.g., "EcoFocus"
    step_name="Data_Processing",       # E.g., "Pandas_Cleaning"
    functional_units=records_processed,
    functional_unit_name="CSV rows",
    provider="AWS",                    # E.g., "AWS", "GCP", "Azure", "On-Premise"
    instance_type="m5.large"           # NEW v2.0: Triggers Boavizta for exact Hardware LCA
):
    # Place your actual code here
    process_massive_data()

```

### Option B: AI and LLM Projects (SCI for AI via CLEER)

AI inference energy consumption happens in the cloud. Because cloud providers do not expose physical GPU sensors, setting `is_ai=True` activates **Path B (The CLEER Engine)**. The SDK deterministically calculates the environmental impact locally based on token telemetry, completely removing external API bottlenecks.

```python
from green_logger import GreenLogger

# 1. Execute your AI call (Amazon Bedrock, OpenAI, Anthropic, etc.)
response = ai_client.generate_text(prompt="Summarize this document")

# 2. Wrap the measurement by setting the is_ai=True flag
with GreenLogger(
    project_id="Your_AI_Project_Name",
    step_name="LLM_Inference",
    is_ai=True,                           # MANDATORY FOR AI (Activates CLEER)
    model_name="claude-3-5-sonnet",       # The foundational model you are using
    prompt_tokens=response.usage.input_tokens, 
    cached_tokens=response.usage.cache_read_input_tokens, # NEW v2.0: Captures GSF Prompt Caching
    completion_tokens=response.usage.output_tokens,       
    functional_units=1,                   # 1 transaction/call
    functional_unit_name="API request"
    # Note: 'provider' is omitted here because the SDK automatically infers 
    # the cloud provider (Azure, AWS, GCP) based on the AI model name.
):
    pass # The mathematical telemetry registration is automatic

```

## 2.1 ⏱️ Time Normalization (`measurement_period`)

In GreenOps, it is crucial to differentiate between **Script Execution Time** (how long the Python code takes to run) and the **Measurement Period** (the actual timeframe the telemetry represents).

The `GreenLogger` handles both scenarios to ensure the SCI Dashboard can mathematically normalize metrics (Carbon, Energy, Cost, and Water) accurately:

* **Real-Time Tracking (Default):** If you are measuring a live process (e.g., an API call, a model inference, or a live data transformation), ignore this parameter. The SDK will automatically time your code execution.
* **Batch Processing & Historical Data:** If your script runs in 5 seconds, but it is parsing logs or processing a batch that represents a whole month of activity, you **must** specify the timeframe.
Accepted formats: `"X Minutes"`, `"X Hours"`, `"X Days"`, `"X Months"`, `"X Years"`.

**Example for Historical Data:**

```python
from green_logger import GreenLogger

# Initialize logger for historical/batch data
logger = GreenLogger(
    project_id="myCO2", 
    step_name="DWH_Compute", 
    functional_unit_name="queries", 
    functional_units=5000, 
    measurement_period="1 Month"  # <--- CRITICAL FOR ACCURATE DASHBOARD SCALING
)
    # Your log parsing / batch processing code here
    parse_historical_logs()
    # Log metrics to telemetry file
    logger.log_metrics()

```

## 3. 📏 How to define your Functional Unit (`functional_units`)

To calculate the Software Carbon Intensity (SCI) accurately, the system divides your total carbon emissions by a Functional Unit (R). **It is critical that you choose the correct Functional Unit for your project and keep it consistent across all your measurements.**

Please use the following standard depending on the nature of your software:

**1. CI/CD, Builds, and Static Pipelines**

* **Standard:** `functional_units = 1` | `functional_unit_name = "pipeline execution"` or `"full build"`
* **Why:** The goal is to measure the carbon cost of running the *entire* pipeline or deployment process once. Do not use the number of individual tests or compiled files as your unit.

**2. AI Models, Chatbots, and Transactional APIs**

* **Standard:** `functional_units = [Number of requests]` | `functional_unit_name = "API request"` or `"user prompt"`
* **Why:** The impact of these systems scales with user traffic. We need to measure the carbon cost per individual interaction (e.g., if your test script sends 500 prompts, set this to 500).

**3. Massive Data Processing (ETLs, Big Data scripts)**

* **Standard:** `functional_units = [Volume]` | `functional_unit_name = "CSV rows"`, `"DB records"`, or `"MBs processed"`
* **Why:** Processing 10 rows is not the same as 10 million. Use the number of database records, rows, or Megabytes analyzed during the execution to measure the efficiency of your algorithm.

## 4. Parameter Dictionary

To ensure your data is accurately reflected in the global dashboard, please fill in these parameters correctly:

* **`project_id` (String):** The official name of your project. Always use the exact same name across all your scripts so the metrics are grouped together.
* **`step_name` (String):** A brief description of the task being measured.
* **`functional_units` (Integer):** The $R$ value in the SCI equation. It represents the unit of work (e.g., 1 user, 1000 records, 1 API request).
* **`functional_unit_name` (String):** The name/description of your $R$ value (e.g., "API request", "build", "CSV rows"). This defines what the SCI score represents.
* **`is_ai` (Boolean):** Set to `True` ONLY if you are measuring the impact of a remote AI model (Triggers Path B / CLEER).
* **`measurement_period` (String, Optional):** The explicit timeframe your data represents (e.g., "1 Month", "24 Hours"). Use this ONLY if you are processing historical data or batches. If left blank, the SDK will automatically record the real-time script execution duration.
* **`provider` (String, Optional):** The infrastructure where your code executes (e.g., `"AWS"`, `"GCP"`, `"Azure"`, `"On-Premise"`). **Critical for tracking the Level 1 Water Footprint**. If left blank, AI models will auto-infer their provider based on the model name, but standard code will default to `"Unknown"` and receive a conservative, high-penalty water metric (1.80 L/kWh).
* **`instance_type` (String, Optional):** (v2.0) If known (e.g., `"ml.p4d.24xlarge"`), passing this triggers the Boavizta API to fetch the exact manufacturing LCA of the server for the $M$ variable. If omitted, the SDK safely falls back to CLEER industry hardware averages.
* **`country_iso_code` (String, Optional)**: The 3-letter ISO country code where the code is running (e.g., `"ESP"`,`"IND"`). If left blank, the SDK attempts to geolocate via IP. Security Note: If your execution environment has strict outbound firewalls blocking geolocation APIs, you must pass this parameter to force the SDK into fully offline mode and prevent timeouts.
* **`embodied_carbon_rate` (Float, Optional):** The allocated embodied carbon rate of your hardware. Defaults to `0.0`. Only modify this manually if you have precise hardware LCA data and are not using `instance_type`.

**Exclusive parameters if `is_ai=True`:**

* **`model_name` (String):** The name of the AI model being used (e.g., "gpt-4o-mini").
* **`prompt_tokens` (Integer):** The net input tokens processed by the model.
* **`cached_tokens` (Integer):** (v2.0) Input tokens served directly from the provider's memory. *Critical for demonstrating FinOps and Carbon savings through Semantic/Prompt Caching.*
* **`completion_tokens` (Integer):** The number of tokens generated by the model.

## 🛡️ Data Quality & Resilience (v2.0.0+)

The SDK now automatically tracks `SDK_VERSION` and `MEASUREMENT_METHOD` to ensure transparency. It distinguishes between physical hardware sensors (`rapl`), container estimates (`tdp_estimate`), and deterministic GenAI models (`CLEER_v2`). Furthermore, the SDK is crash-resistant: it handles SIGKILL process interruptions gracefully, recording partial `[INCOMPLETE]` rows to ensure zero telemetry loss.

## 5. Submitting Your Results

After your code finishes executing, the SDK will automatically generate a file named `green_telemetry.db` (or your custom `db_path`) in your project folder. This file stores all your telemetry data locally.

**Next Step:** Move this `.db` file to the `incoming_dbs/` directory and run `merge_dbs.py`. Your data will be instantly consolidated and visualized on the central SCI Dashboard (`app.py`).

## 6. ⚖️ Measuring Optimizations (Pre vs. Post & The Own Mirror)

The ultimate goal of Green Software is not to compete against other projects, but to establish a baseline and prove internal efficiency gains. If you apply Green Software Foundation (GSF) Patterns, you must provide a **Baseline (Pre)** and an **Optimized (Post)** database.

*Note: For GenAI projects leveraging Prompt Caching, providing the `cached_tokens` parameter automatically generates an inline Baseline vs CLEER comparison in the dashboard.*

**🚨 THE GOLDEN RULE: Apples to Apples**
To allow the GreenOps Dashboard to calculate the exact carbon and financial FinOps savings, the telemetry structure must be completely symmetrical:

1. **Exact same number of steps:** If your baseline measurement used 3 `GreenLogger` wrappers (e.g., resulting in 3 rows in the DB), your optimized measurement MUST also use 3 wrappers wrapping the exact same logic blocks. Do not consolidate multiple steps into a single measurement in the post-optimization run.
2. **Exact same names:** The `step_name` parameter in your Python code must remain **100% identical** between the baseline and the optimized code (e.g., if it was `"Client_Vite_Build"`, keep it exactly like that). Do not manually add "-post" or "-optimized" in your code.
3. **File naming convention:**
* Send your baseline file named normally (e.g., `green_telemetry_pre.db`).
* Send your optimized file with the word **"post"** or **"opt"** in the filename (e.g., `green_telemetry_post.db`). Our central parser will automatically detect this and tag your steps as `(POST-OPTIMIZATION)` on the dashboard.



*Failure to maintain the exact same `step_name` and row count will result in mismatched data, and the dashboard will reject the granular comparison.*