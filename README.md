
# Roche Green SDK - Digital Sustainability 

Welcome to the **Roche Green SDK**. This tool allows you to measure the carbon footprint of your software and calculate the Software Carbon Intensity (SCI) of your code, whether it is traditional processing (CPU/RAM) or Artificial Intelligence inference (SCI for AI).

The calculation is based on the official standard from the Green Software Foundation: 
$SCI = \frac{(E \cdot I) + M}{R}$

## 1. Installation

You do not need to set up complex infrastructure or connect to an external API. The SDK runs locally and stores data in a local SQLite database.

1. Place the `roche_green_sdk` folder into the root directory of your project.
2. Open your terminal in that directory and install the package locally by running:
   ```bash
   pip install -e ./roche_green_sdk

```

*(This will automatically install the required dependencies: `codecarbon` and `requests`).*

## 2. How to use the SDK in your code

Import `GreenLogger` in your main script and wrap the function or process you want to measure using a `with` block. Depending on the nature of your project, use one of the following templates:

### Option A: Standard Code Projects (Data Science, ETLs, APIs)

This measures the actual CPU and RAM energy consumption on your local machine.

```python
from roche_green.logger import GreenLogger

# Define how many units you are processing (e.g., CSV rows, transactions)
records_processed = 5000

with GreenLogger(
    project_id="Your_Project_Name",    # E.g., "EcoFocus"
    step_name="Data_Processing",       # E.g., "Pandas_Cleaning"
    functional_units=records_processed,
    functional_unit_name="CSV rows"
):
    # Place your actual code here
    process_massive_data()

```

### Option B: AI and LLM Projects (SCI for AI)

AI inference energy consumption happens in the cloud. The SDK uses EcoLogits to estimate the environmental impact on the servers based on the chosen model and the tokens used.

```python
from roche_green.logger import GreenLogger

# 1. Execute your AI call (Groq, OpenAI, etc.)
response = ai_client.generate_text(prompt="Summarize this document")
tokens_in = response.usage.prompt_tokens
tokens_out = response.usage.completion_tokens

# 2. Wrap the measurement by setting the is_ai=True flag
with GreenLogger(
    project_id="Your_AI_Project_Name",
    step_name="LLM_Inference",
    is_ai=True,                           # MANDATORY FOR AI
    model_name="gpt-4o-mini",             # The proxy model you are using
    prompt_tokens=tokens_in,              # Input tokens
    completion_tokens=tokens_out,         # Output tokens
    functional_units=1,                   # 1 transaction/call
    functional_unit_name="API request"
):
    pass # The telemetry registration is automatic

```

## 2.1 ⏱️ Time Normalization (`measurement_period`)

In GreenOps, it is crucial to differentiate between **Script Execution Time** (how long the Python code takes to run) and the **Measurement Period** (the actual timeframe the telemetry represents).

The `GreenLogger` handles both scenarios to ensure the SCI Dashboard can mathematically normalize metrics (Carbon, Energy, Cost) accurately:

* **Real-Time Tracking (Default):** If you are measuring a live process (e.g., an API call, a model inference, or a live data transformation), ignore this parameter. The SDK will automatically time your code execution.
* **Batch Processing & Historical Data:** If your script runs in 5 seconds, but it is parsing logs or processing a batch that represents a whole month of activity, you **must** specify the timeframe.
Accepted formats: `"X Minutes"`, `"X Hours"`, `"X Days"`, `"X Months"`, `"X Years"`.

**Example for Historical Data:**

```python
with GreenLogger(
    project_id="myCO2", 
    step_name="DWH_Compute", 
    functional_unit_name="queries", 
    functional_units=5000, 
    measurement_period="1 Month"  # <--- CRITICAL FOR ACCURATE DASHBOARD SCALING
):
    # Your log parsing / batch processing code here

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
* **`is_ai` (Boolean):** Set to `True` ONLY if you are measuring the impact of a remote AI model.
* **`measurement_period` (String, Optional):** The explicit timeframe your data represents (e.g., "1 Month", "24 Hours"). Use this ONLY if you are processing historical data or batches. If left blank, the SDK will automatically record the real-time script execution duration.

**Exclusive parameters if `is_ai=True`:**

* **`model_name` (String):** The name of the AI model being used (e.g., "gpt-4o-mini").
* **`prompt_tokens` (Integer):** The number of tokens sent in your request.
* **`completion_tokens` (Integer):** The number of tokens generated by the model.

## 5. Submitting Your Results

After your code finishes executing, the SDK will automatically generate a file named `local_telemetry.db` in your project folder. This file stores all your telemetry data locally.

**Next Step:** Send this `.db` file to the Digital Sustainability (Green Coding Team) so your data can be merged and visualized on the company's central SCI Dashboard.

## 6. ⚖️ Measuring Optimizations (Pre vs. Post)

If you are applying Green Coding practices and want to measure the environmental ROI (Return on Investment) of your optimizations, you must provide a **Baseline (Pre)** and an **Optimized (Post)** database.

**🚨 THE GOLDEN RULE: Apples to Apples**
To allow the GreenOps Dashboard to calculate the exact carbon and financial savings, the telemetry structure must be completely symmetrical:

1. **Exact same number of steps:** If your baseline measurement used 3 `GreenLogger` wrappers (e.g., resulting in 3 rows in the DB), your optimized measurement MUST also use 3 wrappers wrapping the exact same logic blocks. Do not consolidate multiple steps into a single measurement in the post-optimization run.
2. **Exact same names:** The `step_name` parameter in your Python code must remain **100% identical** between the baseline and the optimized code (e.g., if it was `"Client_Vite_Build"`, keep it exactly like that). Do not manually add "-post" or "-optimized" in your code.
3. **File naming convention:** * Send your baseline file named normally (e.g., `green_telemetry_pre.db`).
* Send your optimized file with the word **"post"** or **"opt"** in the filename (e.g., `green_telemetry_post.db`). Our central parser will automatically detect this and tag your steps as `(POST-OPTIMIZATION)` on the dashboard.



*Failure to maintain the exact same `step_name` and row count will result in mismatched data, and the dashboard will reject the granular comparison.*

```

```