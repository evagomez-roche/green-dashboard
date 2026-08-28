import os
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import numpy as np
import re

# =====================================================================
# PAGE CONFIGURATION
# =====================================================================
st.set_page_config(
    page_title="SCI Carbon Dashboard",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS styles for metrics
st.markdown('''
    <style>
    .metric-card {
        background-color: #f8f9fa;
        border-left: 5px solid #27ae60;
        padding: 15px;
        border-radius: 5px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .metric-card-danger {
        background-color: #f8f9fa;
        border-left: 5px solid #e74c3c;
        padding: 15px;
        border-radius: 5px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .metric-card-ai {
        background-color: #f8f9fa;
        border-left: 5px solid #8e44ad;
        padding: 15px;
        border-radius: 5px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .metric-card h2, .metric-card-danger h2, .metric-card-ai h2 {
        font-size: 1.6rem !important; 
        margin: 10px 0 0 0;
    }
    .metric-card h4, .metric-card-danger h4, .metric-card-ai h4 {
        font-size: 0.9rem !important; 
        margin: 0;
        color: #555;
    }
    </style>
''', unsafe_allow_html=True)

st.title("🌱 Software Carbon Intensity (SCI) Dashboard")
st.caption("Green Software Foundation Telemetry & Real-Time Analytics")

# =====================================================================
# FORMATTING HELPERS
# =====================================================================
def format_european(val):
    """Format number to European style (dot for thousands, comma for decimals)."""
    if pd.isna(val):
        return val
    if isinstance(val, (int, float)):
        # If integer or effectively integer
        if isinstance(val, int) or val.is_integer():
            return f"{int(val):,}".replace(',', '.')
        
        abs_val = abs(val)
        
        # If it's a very small non-zero number, use 4 decimal places
        if 0 < abs_val < 0.01:
            formatted = f"{val:,.4f}"
        else:
            formatted = f"{val:,.2f}"
            
        # Replace point with comma
        return formatted.translate(str.maketrans(',.', '.,'))
    return val

def format_dataframe_display(df):
    """Apply European formatting to all numeric columns in a DataFrame for display."""
    df_display = df.copy()
    for col in df_display.columns:
        if df_display[col].dtype in ['float64', 'int64']:
            # Do not format integer columns like Tokens with decimals
            if 'Token' in col or 'IS_AI' in col:
                df_display[col] = df_display[col].apply(lambda x: str(int(x)) if pd.notna(x) else x)
            else:
                df_display[col] = df_display[col].apply(format_european)
        # Fix $ formatting to ensure it has commas
        if 'Cost_$' in col and df_display[col].dtype == 'object':
             df_display[col] = df_display[col].str.replace('.', ',')
    return df_display

# =====================================================================
# DATA NORMALIZATION HELPERS
# =====================================================================
def normalize_model_name(name):
    if pd.isna(name) or name is None:
        return ""
    
    name = str(name).lower().strip()
    
    for prefix in ['eu.anthropic.', 'us.anthropic.', 'anthropic.', 'openai.', 'meta.']:
        name = name.replace(prefix, '')
        
    name = name.split('-v')[0]
    name = name.split('@')[0]
    name = re.sub(r'-\d{4}-\d{2}-\d{2}$', '', name)
    
    return name.strip()

# =====================================================================
# DATA LOADING & COST CALCULATION
# =====================================================================
@st.cache_data(ttl=60)
def load_cost_data():
    file_name = "Galileo_model_data.csv" 
    if os.path.exists(file_name):
        try:
            df_costs = pd.read_csv(file_name)
            cost_col = [c for c in df_costs.columns if 'cost' in c.lower()][0]
            token_col = [c for c in df_costs.columns if 'token' in c.lower() and '%' not in c][0]
            model_col = [c for c in df_costs.columns if 'model' in c.lower()][0]

            df_costs[cost_col] = df_costs[cost_col].replace({r'\$': '', ',': ''}, regex=True).astype(float)
            df_costs[token_col] = df_costs[token_col].astype(float)
            
            df_costs['Cost_Per_Token'] = df_costs[cost_col] / df_costs[token_col]
            df_costs['Join_Key'] = df_costs[model_col].apply(normalize_model_name)
            
            df_costs = df_costs.drop_duplicates(subset=['Join_Key'])
            return df_costs[['Join_Key', 'Cost_Per_Token']]
        except Exception as e:
            st.sidebar.error(f"Failed parsing CSV: {e}")
            return pd.DataFrame()
    return pd.DataFrame()

@st.cache_data(ttl=5)
def load_data():
    db_file = "green_telemetry.db"
    if os.path.exists(db_file):
        try:
            conn = sqlite3.connect(db_file)
            df = pd.read_sql("SELECT * FROM CO_SOFTWARE_CARBON_INTENSITY", conn)
            conn.close()
            if not df.empty:
                return process_dataframe(df), "LOCAL_SQLITE"
        except Exception as e:
            return None, str(e)
    return pd.DataFrame(), "EMPTY"

def process_dataframe(df):
    for col in ['IS_AI', 'AI_MODEL_NAME', 'PROMPT_TOKENS', 'COMPLETION_TOKENS']:
        if col not in df.columns:
            df[col] = 0 if 'TOKENS' in col or col == 'IS_AI' else None
            
    if 'FUNCTIONAL_UNIT_NAME' not in df.columns:
        df['FUNCTIONAL_UNIT_NAME'] = 'transaction'
    else:
        df['FUNCTIONAL_UNIT_NAME'] = df['FUNCTIONAL_UNIT_NAME'].fillna('transaction')
        
    df['Functional Unit Details'] = df['FUNCTIONAL_UNIT_TX'].astype(str) + " (" + df['FUNCTIONAL_UNIT_NAME'] + ")"
    df['Total_Tokens'] = df['PROMPT_TOKENS'] + df['COMPLETION_TOKENS']

    df['Join_Key'] = df['AI_MODEL_NAME'].apply(normalize_model_name)

    df_costs = load_cost_data()
    if not df_costs.empty:
        df = df.merge(df_costs, on='Join_Key', how='left')
        df['Estimated_Cost_$'] = df['Total_Tokens'] * df['Cost_Per_Token']
        df['Estimated_Cost_$'] = df['Estimated_Cost_$'].fillna(0.0)
    else:
        df['Estimated_Cost_$'] = 0.0
        
    df = df.rename(columns={
        'SCI_TRACKER_ID': 'SCI Tracker ID',
        'EXECUTION_DATE': 'Date',
        'SCI_SCORE_GCO2E_TX': 'SCI Score (g CO2e/tx)',
        'TOTAL_CARBON_FOOTPRINT_GCO2E': 'Total Carbon Footprint (g CO2e)',
        'ENERGY_CONSUMED_KWH': 'Energy Consumed - E (kWh)',
        'EMBODIED_EMISSIONS_GCO2E': 'Embodied Emissions - M (g CO2e)',
        'FUNCTIONAL_UNIT_TX': 'Functional Unit - R (Transactions)',
        'PROJECT_NAME': 'Project'
    })
    
    df['Operational_Emissions_gCO2e'] = df['Total Carbon Footprint (g CO2e)']
    return df

# =====================================================================
# ANTI-DOUBLE COUNTING FILTER HELPER
# =====================================================================
def apply_phase_filter(df, view_phases):
    if "Baseline (Pre)" in view_phases and "Optimized (Post)" in view_phases:
        return df.copy()
    elif "Baseline (Pre)" in view_phases:
        return df[~df['Is_Post']].copy()
    elif "Optimized (Post)" in view_phases:
        proyectos_optimizados = df[df['Is_Post']]['Project'].unique()
        condicion_post = df['Is_Post'] | (~df['Project'].isin(proyectos_optimizados))
        return df[condicion_post].copy()
    else:
        return pd.DataFrame(columns=df.columns)

df, source = load_data()

# =====================================================================
# SIDEBAR CONTROLS & FILTERS
# =====================================================================
st.sidebar.header("🔄 Real-Time Controls")

if source == "LOCAL_SQLITE":
    st.sidebar.info("🟡 Source: Local SQLite Database")

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

if source == "EMPTY" or (df is not None and df.empty):
    st.info("ℹ️ Telemetry database is currently empty. Run your test script to populate 'green_telemetry.db'.")
    st.stop()
elif df is None:
    st.error(f"❌ Database connection error: {source}")
    st.stop()

st.sidebar.divider()
st.sidebar.header("Filter Telemetry")
selected_projects = st.sidebar.multiselect(
    "Select Project / Software Unit", 
    options=df['Project'].unique(), 
    default=df['Project'].unique()
)

filtered_df = df[df['Project'].isin(selected_projects)].copy()

if filtered_df.empty:
    st.warning("No data available for the selected project filters.")
    st.stop()

# Tag which rows belong to a Post-Optimization execution globally
filtered_df['Is_Post'] = filtered_df['PROCESS_DESC'].str.contains('(POST-OPTIMIZATION)', regex=False)

# =====================================================================
# TABS SETUP
# =====================================================================
tab_global, tab_standard, tab_ai, tab_drilldown = st.tabs([
    "🌍 Global Overview", 
    "💻 SCI (Standard Code)", 
    "🧠 SCI for AI (Models & Costs)", 
    "🔍 Project Drill-down"
])

# =====================================================================
# TAB 1: GLOBAL OVERVIEW
# =====================================================================
with tab_global:
    st.subheader("📋 Telemetry Registry (`CO_SOFTWARE_CARBON_INTENSITY`)")
    
    # Updated Display Order: Date, Project, SCI Score, then the rest
    base_cols = ['Date', 'Project', 'SCI Score (g CO2e/tx)', 'Component / Step' if 'Component / Step' in filtered_df.columns else 'PROCESS_DESC']
    display_cols = [c for c in base_cols if c in filtered_df.columns]
    
    other_cols = ['Total Carbon Footprint (g CO2e)', 'Functional Unit Details', 'IS_AI']
    display_cols.extend([c for c in other_cols if c in filtered_df.columns])
    
    # Add any remaining columns except Join_Key
    display_cols.extend([col for col in filtered_df.columns if col not in display_cols and col != 'Join_Key'])
    
    df_display_tab1 = format_dataframe_display(filtered_df[display_cols].sort_values(by="Date", ascending=False))
    st.dataframe(df_display_tab1, use_container_width=True)
    st.divider()

    st.subheader("📈 Key Impact Metrics (KPIs)")
    m1, m2, m3, m4 = st.columns(4)
    total_carbon = filtered_df['Total Carbon Footprint (g CO2e)'].sum()
    avg_sci = filtered_df['SCI Score (g CO2e/tx)'].mean()
    total_energy = filtered_df['Energy Consumed - E (kWh)'].sum()
    max_su_idx = filtered_df['SCI Score (g CO2e/tx)'].idxmax()
    max_su = filtered_df.loc[max_su_idx]['SCI Tracker ID'] if pd.notna(max_su_idx) else "N/A"
    
    with m1:
        st.markdown(f'<div class="metric-card"><h4>Total Carbon Footprint</h4><h2>{format_european(total_carbon)} gCO₂e</h2></div>', unsafe_allow_html=True)
    with m2:
        st.markdown(f'<div class="metric-card"><h4>Average SCI Score</h4><h2>{format_european(avg_sci)} g/tx</h2></div>', unsafe_allow_html=True)
    with m3:
        st.markdown(f'<div class="metric-card"><h4>Energy Consumed</h4><h2>{format_european(total_energy)} kWh</h2></div>', unsafe_allow_html=True)
    with m4:
        st.markdown(f'<div class="metric-card-danger"><h4>Highest Hotspot</h4><h2>{max_su}</h2></div>', unsafe_allow_html=True)

    st.divider()
    
    # --- PHASE SELECTOR FOR GLOBAL CHARTS ---
    # Default changed to Baseline (Pre)
    tab1_phases = st.multiselect(
        "⚖️ Select Phase (Filters footprint distribution charts below):", 
        options=["Baseline (Pre)", "Optimized (Post)"],
        default=["Baseline (Pre)"],
        key="tab1_phase"
    )
    df_tab1 = apply_phase_filter(filtered_df, tab1_phases)

    st.subheader("📊 Footprint & Workload Distribution")
    c1, c2 = st.columns(2)
    with c1:
        proj_agg = df_tab1.groupby('Project')['Total Carbon Footprint (g CO2e)'].sum().reset_index()
        fig_pie = px.pie(proj_agg, values='Total Carbon Footprint (g CO2e)', names='Project', title="Footprint by Project", hole=0.4)
        st.plotly_chart(fig_pie, use_container_width=True)
    with c2:
        df_tab1['Workload_Type'] = df_tab1['IS_AI'].apply(lambda x: 'AI / LLM Inference' if x == 1 else 'Standard Code')
        ai_agg = df_tab1.groupby('Workload_Type')['Total Carbon Footprint (g CO2e)'].sum().reset_index()
        fig_ai = px.bar(ai_agg, x='Workload_Type', y='Total Carbon Footprint (g CO2e)', title="Footprint by Workload Type", color='Workload_Type', color_discrete_map={'Standard Code': '#2ecc71', 'AI / LLM Inference': '#9b59b6'})
        st.plotly_chart(fig_ai, use_container_width=True)

    st.divider()
    st.subheader("⚡ SCI Breakdown: Operational (E × I) vs Embodied Carbon (M)")
    lifespan_years = st.slider("Hardware Lifespan Simulator (Years)", min_value=1, max_value=10, value=4, step=1, key="global_lifespan")
    df_grouped = df_tab1.groupby('Project').agg({
        'Embodied Emissions - M (g CO2e)': 'sum', 'Operational_Emissions_gCO2e': 'sum', 'Functional Unit - R (Transactions)': 'sum'
    }).reset_index()
    df_grouped['M_Adjusted'] = df_grouped['Embodied Emissions - M (g CO2e)'] / lifespan_years
    df_grouped['Embodied_gCO2e_tx'] = np.where(df_grouped['Functional Unit - R (Transactions)'] > 0, df_grouped['M_Adjusted'] / df_grouped['Functional Unit - R (Transactions)'], 0.0)
    df_grouped['Operational_gCO2e_tx'] = np.where(df_grouped['Functional Unit - R (Transactions)'] > 0, df_grouped['Operational_Emissions_gCO2e'] / df_grouped['Functional Unit - R (Transactions)'], 0.0)

    fig_stack = go.Figure(data=[
        go.Bar(name='Operational Emissions (E × I / R)', x=df_grouped['Project'], y=df_grouped['Operational_gCO2e_tx'], marker_color='#2ecc71'),
        go.Bar(name='Embodied Emissions (M / R)', x=df_grouped['Project'], y=df_grouped['Embodied_gCO2e_tx'], marker_color='#e74c3c')
    ])
    fig_stack.update_layout(barmode='stack', yaxis_title='gCO₂e / transaction')
    st.plotly_chart(fig_stack, use_container_width=True)

# =====================================================================
# TAB 2: SCI (STANDARD CODE)
# =====================================================================
with tab_standard:
    df_std_base = filtered_df[filtered_df['IS_AI'] == 0].copy()
    if df_std_base.empty:
        st.info("No standard code telemetry found in the selected projects.")
    else:
        st.subheader("📋 Standard Projects Registry")
        display_cols_std = [c for c in display_cols if c in df_std_base.columns and c != 'Join_Key']
        
        df_display_tab2 = format_dataframe_display(df_std_base[display_cols_std].sort_values(by="Date", ascending=False))
        st.dataframe(df_display_tab2, use_container_width=True)
        st.divider()

        # --- PHASE SELECTOR FOR STANDARD METRICS ---
        tab2_phases = st.multiselect(
            "⚖️ Select Phase (Filters KPIs and charts below):", 
            options=["Baseline (Pre)", "Optimized (Post)"],
            default=["Baseline (Pre)"],
            key="tab2_phase"
        )
        df_tab2 = apply_phase_filter(df_std_base, tab2_phases)

        st.subheader("📈 Standard Code KPIs")
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            st.markdown(f'<div class="metric-card"><h4>Total Carbon</h4><h2>{format_european(df_tab2["Total Carbon Footprint (g CO2e)"].sum())} gCO₂e</h2></div>', unsafe_allow_html=True)
        with s2:
            st.markdown(f'<div class="metric-card"><h4>Average SCI</h4><h2>{format_european(df_tab2["SCI Score (g CO2e/tx)"].mean())} g/tx</h2></div>', unsafe_allow_html=True)
        with s3:
            st.markdown(f'<div class="metric-card"><h4>Energy Consumed</h4><h2>{format_european(df_tab2["Energy Consumed - E (kWh)"].sum())} kWh</h2></div>', unsafe_allow_html=True)
        with s4:
            max_su_idx_std = df_tab2['SCI Score (g CO2e/tx)'].idxmax() if not df_tab2.empty else None
            max_su_std = df_tab2.loc[max_su_idx_std]['SCI Tracker ID'] if pd.notna(max_su_idx_std) else "N/A"
            st.markdown(f'<div class="metric-card-danger"><h4>Highest Hotspot</h4><h2>{max_su_std}</h2></div>', unsafe_allow_html=True)
        
        st.divider()
        st.subheader("📊 Standard Projects Overview")
        c3, c4 = st.columns(2)
        with c3:
            std_agg = df_tab2.groupby('Project')['Total Carbon Footprint (g CO2e)'].sum().reset_index()
            fig_pie_std = px.pie(std_agg, values='Total Carbon Footprint (g CO2e)', names='Project', title="Carbon Footprint by Standard Project", hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
            st.plotly_chart(fig_pie_std, use_container_width=True)
        with c4:
            st.markdown("**(Hardware Lifespan Simulator)**")
            lifespan_std = st.slider("Lifespan (Years)", min_value=1, max_value=10, value=4, step=1, key="std_lifespan")
            df_std_grp = df_tab2.groupby('Project').agg({'Embodied Emissions - M (g CO2e)': 'sum', 'Operational_Emissions_gCO2e': 'sum', 'Functional Unit - R (Transactions)': 'sum'}).reset_index()
            df_std_grp['M_Adjusted'] = df_std_grp['Embodied Emissions - M (g CO2e)'] / lifespan_std
            df_std_grp['Embodied_gCO2e_tx'] = np.where(df_std_grp['Functional Unit - R (Transactions)'] > 0, df_std_grp['M_Adjusted'] / df_std_grp['Functional Unit - R (Transactions)'], 0.0)
            df_std_grp['Operational_gCO2e_tx'] = np.where(df_std_grp['Functional Unit - R (Transactions)'] > 0, df_std_grp['Operational_Emissions_gCO2e'] / df_std_grp['Functional Unit - R (Transactions)'], 0.0)
            fig_stack_std = go.Figure(data=[
                go.Bar(name='Operational', x=df_std_grp['Project'], y=df_std_grp['Operational_gCO2e_tx'], marker_color='#2ecc71'),
                go.Bar(name='Embodied', x=df_std_grp['Project'], y=df_std_grp['Embodied_gCO2e_tx'], marker_color='#e74c3c')
            ])
            fig_stack_std.update_layout(barmode='stack')
            st.plotly_chart(fig_stack_std, use_container_width=True)

# =====================================================================
# TAB 3: SCI FOR AI (MODELS & COSTS)
# =====================================================================
with tab_ai:
    df_ai_base = filtered_df[filtered_df['IS_AI'] == 1].copy()
    if df_ai_base.empty:
        st.info("No AI telemetry found in the selected projects.")
    else:
        st.subheader("📋 AI Projects Registry")
        
        # Updated Display Order for AI
        base_cols_ai = ['Date', 'Project', 'SCI Score (g CO2e/tx)', 'AI_MODEL_NAME']
        display_cols_ai = [c for c in base_cols_ai if c in df_ai_base.columns]
        other_cols_ai = ['Total_Tokens', 'Total Carbon Footprint (g CO2e)', 'Estimated_Cost_$']
        display_cols_ai.extend([c for c in other_cols_ai if c in df_ai_base.columns])
        
        # Add remaining cols
        display_cols_ai.extend([c for c in df_ai_base.columns if c not in display_cols_ai and c != 'Join_Key'])
        
        formatted_df_ai = df_ai_base.copy()
        
        df_display_tab3 = format_dataframe_display(formatted_df_ai[display_cols_ai].sort_values(by="Date", ascending=False))
        # Re-apply cost formatting specifically to maintain the $ sign but with comma
        if 'Estimated_Cost_$' in df_display_tab3.columns:
            df_display_tab3['Estimated_Cost_$'] = df_ai_base['Estimated_Cost_$'].apply(lambda x: f"${x:.4f}".replace('.', ','))

        st.dataframe(df_display_tab3, use_container_width=True)
        st.divider()

        # --- PHASE SELECTOR FOR AI METRICS ---
        tab3_phases = st.multiselect(
            "⚖️ Select Phase (Filters KPIs and charts below):", 
            options=["Baseline (Pre)", "Optimized (Post)"],
            default=["Baseline (Pre)"],
            key="tab3_phase"
        )
        df_tab3 = apply_phase_filter(df_ai_base, tab3_phases)

        st.subheader("📈 AI Code KPIs")
        a1, a2, a3, a4 = st.columns(4)
        with a1:
            st.markdown(f'<div class="metric-card"><h4>Total AI Carbon</h4><h2>{format_european(df_tab3["Total Carbon Footprint (g CO2e)"].sum())} gCO₂e</h2></div>', unsafe_allow_html=True)
        with a2:
            st.markdown(f'<div class="metric-card"><h4>Average AI SCI</h4><h2>{format_european(df_tab3["SCI Score (g CO2e/tx)"].mean())} g/tx</h2></div>', unsafe_allow_html=True)
        with a3:
            st.markdown(f'<div class="metric-card"><h4>Total Tokens</h4><h2>{df_tab3["Total_Tokens"].sum():,.0f}</h2></div>', unsafe_allow_html=True)
        with a4:
            max_ai_idx = df_tab3['SCI Score (g CO2e/tx)'].idxmax() if not df_tab3.empty else None
            max_su_ai = df_tab3.loc[max_ai_idx]['SCI Tracker ID'] if pd.notna(max_ai_idx) else "N/A"
            st.markdown(f'<div class="metric-card-danger"><h4>Highest Hotspot</h4><h2>{max_su_ai}</h2></div>', unsafe_allow_html=True)

        st.divider()
        st.subheader("📊 AI Analytics & Financial FinOps")
        r1, r2 = st.columns(2)
        with r1:
            ai_agg_proj = df_tab3.groupby('Project')['Total Carbon Footprint (g CO2e)'].sum().reset_index()
            fig_pie_ai = px.pie(ai_agg_proj, values='Total Carbon Footprint (g CO2e)', names='Project', title="Carbon Footprint by AI Project", hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
            st.plotly_chart(fig_pie_ai, use_container_width=True)
        with r2:
            total_cost_str = f"${df_tab3['Estimated_Cost_$'].sum():.4f}".replace('.', ',')
            st.markdown(f'<div class="metric-card-ai" style="text-align: center; margin-bottom: 20px;"><h4>Total FinOps Cost Across All AI Projects</h4><h2 style="font-size: 2.5rem !important;">{total_cost_str}</h2></div>', unsafe_allow_html=True)
            cost_agg_proj = df_tab3.groupby('Project')['Estimated_Cost_$'].sum().reset_index()
            fig_cost_proj = px.bar(cost_agg_proj, x='Project', y='Estimated_Cost_$', title="Financial Cost by Project ($)", color='Project', color_discrete_sequence=px.colors.qualitative.Pastel)
            st.plotly_chart(fig_cost_proj, use_container_width=True)

# =====================================================================
# TAB 4: PROJECT DRILL-DOWN (Uses full 'filtered_df' for Pre vs Post math)
# =====================================================================
with tab_drilldown:
    st.subheader("🔍 Detailed Project Analysis")
    st.markdown("Isolate a specific project to identify granular carbon bottlenecks, steps, or individual AI agents.")
    
    selected_proj_drill = st.selectbox("Select a project to analyze:", options=filtered_df['Project'].unique())
    
    if selected_proj_drill:
        df_proj = filtered_df[filtered_df['Project'] == selected_proj_drill].copy()
        is_ai_proj = df_proj['IS_AI'].iloc[0] == 1 if not df_proj.empty else False
        
        df_proj['Clean_Step'] = df_proj['PROCESS_DESC'].str.replace(' (POST-OPTIMIZATION)', '', regex=False)
        df_proj['Status'] = np.where(df_proj['Is_Post'], 'Post-Optimization', 'Baseline')
        
        group_col_table = 'AI_MODEL_NAME' if is_ai_proj else 'PROCESS_DESC'
        breakdown_table = df_proj.groupby(group_col_table).agg({
            'SCI Score (g CO2e/tx)': 'mean',
            'Energy Consumed - E (kWh)': 'sum', 
            'Total Carbon Footprint (g CO2e)': 'sum', 
            'Estimated_Cost_$': 'sum'
        }).reset_index()
        
        breakdown_table = breakdown_table.rename(columns={
            group_col_table: 'Component / Step', 
            'SCI Score (g CO2e/tx)': 'Average SCI Score (g CO2e/tx)'
        })
        
        formatted_breakdown = format_dataframe_display(breakdown_table)
        if 'Estimated_Cost_$' in formatted_breakdown.columns:
            formatted_breakdown['Estimated_Cost_$'] = breakdown_table['Estimated_Cost_$'].apply(lambda x: f"${x:.4f}".replace('.', ','))
            
        st.dataframe(formatted_breakdown, use_container_width=True)
        
        if df_proj['Is_Post'].any():
            chart_view_phase = st.selectbox("Select Phase to visualize in Breakdown:", options=["Baseline", "Post-Optimization"])
            chart_df = df_proj[df_proj['Status'] == chart_view_phase].copy()
        else:
            chart_df = df_proj.copy()
            
        group_col_chart = 'AI_MODEL_NAME' if is_ai_proj else 'PROCESS_DESC'
        breakdown_chart = chart_df.groupby(group_col_chart).agg({
            'Total Carbon Footprint (g CO2e)': 'sum',
            'Estimated_Cost_$': 'sum'
        }).reset_index().rename(columns={group_col_chart: 'Component / Step'})

        if is_ai_proj:
            d1, d2 = st.columns(2)
            with d1:
                fig_drill_carbon = px.bar(
                    breakdown_chart, x='Component / Step', y='Total Carbon Footprint (g CO2e)', 
                    title="Carbon Impact Breakdown", 
                    color='Total Carbon Footprint (g CO2e)', color_continuous_scale='Purples'
                )
                st.plotly_chart(fig_drill_carbon, use_container_width=True)
            
            with d2:
                fig_drill_cost = px.bar(
                    breakdown_chart, x='Component / Step', y='Estimated_Cost_$', 
                    title="Cost Impact Breakdown ($)", 
                    color='Estimated_Cost_$', color_continuous_scale='Blues'
                )
                st.plotly_chart(fig_drill_cost, use_container_width=True)
        else:
            fig_drill = px.bar(
                breakdown_chart, x='Component / Step', y='Total Carbon Footprint (g CO2e)', 
                title=f"Carbon Impact Breakdown ({chart_df['Status'].iloc[0] if not chart_df.empty else 'Baseline'})", 
                color='Total Carbon Footprint (g CO2e)', color_continuous_scale='Greens'
            ) 
            st.plotly_chart(fig_drill, use_container_width=True)

        # =====================================================================
        # PRE VS POST OPTIMIZATION SAVINGS MODULE
        # =====================================================================
        if df_proj['Is_Post'].any():
            st.divider()
            st.subheader("📉 Optimization Trajectory & Savings (Pre vs Post)")
            
            df_pre = df_proj[~df_proj['Is_Post']]
            df_post = df_proj[df_proj['Is_Post']]
            
            pre_carbon_total = df_pre['Total Carbon Footprint (g CO2e)'].sum()
            post_carbon_total = df_post['Total Carbon Footprint (g CO2e)'].sum()
            
            compare_data = pd.DataFrame({
                'Phase': ['Baseline', 'Post-Optimization'],
                'Carbon Footprint (gCO2e)': [pre_carbon_total, post_carbon_total]
            })
            if is_ai_proj:
                pre_cost_total = df_pre['Estimated_Cost_$'].sum()
                post_cost_total = df_post['Estimated_Cost_$'].sum()
                compare_data['Financial Cost ($)'] = [pre_cost_total, post_cost_total]
                
                g1, g2 = st.columns(2)
                with g1:
                    fig_comp_carbon = px.bar(
                        compare_data, x='Phase', y='Carbon Footprint (gCO2e)', color='Phase',
                        title='Carbon Reduction Overview', text_auto='.2f',
                        color_discrete_map={'Baseline': '#e74c3c', 'Post-Optimization': '#2ecc71'}
                    )
                    st.plotly_chart(fig_comp_carbon, use_container_width=True)
                with g2:
                    fig_comp_cost = px.bar(
                        compare_data, x='Phase', y='Financial Cost ($)', color='Phase',
                        title='FinOps Savings Overview', text_auto='.4f',
                        color_discrete_map={'Baseline': '#7f8c8d', 'Post-Optimization': '#3498db'}
                    )
                    st.plotly_chart(fig_comp_cost, use_container_width=True)
            else:
                fig_comp_carbon = px.bar(
                    compare_data, x='Phase', y='Carbon Footprint (gCO2e)', color='Phase',
                    title='Carbon Reduction Overview', text_auto='.2f',
                    color_discrete_map={'Baseline': '#e74c3c', 'Post-Optimization': '#2ecc71'}
                )
                st.plotly_chart(fig_comp_carbon, use_container_width=True)

            st.markdown("### 📊 Overall Project Savings")
            
            carbon_saved = pre_carbon_total - post_carbon_total
            carbon_pct = (carbon_saved / pre_carbon_total * 100) if pre_carbon_total > 0 else 0
            
            # Format saved strings with comma
            carbon_saved_str = format_european(carbon_saved)
            carbon_pct_str = format_european(-carbon_pct)
            
            if is_ai_proj:
                cost_saved = pre_cost_total - post_cost_total
                cost_pct = (cost_saved / pre_cost_total * 100) if pre_cost_total > 0 else 0
                cost_saved_str = format_european(cost_saved)
                cost_pct_str = format_european(-cost_pct)
                
                m1, m2 = st.columns(2)
                m1.metric("Total Carbon Reduction", f"{carbon_saved_str} gCO₂e", f"{carbon_pct_str}%", delta_color="inverse")
                m2.metric("Total FinOps Savings", f"${cost_saved_str}", f"{cost_pct_str}%", delta_color="inverse")
            else:
                st.metric("Total Carbon Reduction", f"{carbon_saved_str} gCO₂e", f"{carbon_pct_str}%", delta_color="inverse")
            
            # --- REAL WORLD EQUIVALENTS ---
            if carbon_saved >= 8.0:
                st.markdown("#### 🌍 Real-World Impact Equivalent")
                smartphones = carbon_saved / 8.0 # Approx 8g CO2 per smartphone charge
                
                if carbon_saved >= 192.0:
                    km_driven = carbon_saved / 192.0 # Approx 192g CO2 per km for average petrol car
                    st.info(f"💡 The **{format_european(carbon_saved)} gCO₂e** saved by optimizing this project is equivalent to the carbon footprint of driving a gasoline car for **{format_european(km_driven)} km** or charging **{int(smartphones)} smartphones**!")
                else:
                    st.info(f"💡 The **{format_european(carbon_saved)} gCO₂e** saved by optimizing this project is equivalent to the carbon footprint of charging **{int(smartphones)} smartphones**!")   
            
            pre_steps = set(df_pre['Clean_Step'].unique())
            post_steps = set(df_post['Clean_Step'].unique())
            matching_steps = pre_steps.intersection(post_steps)
            
            if matching_steps:
                st.divider()
                st.markdown("### 🔍 Granular Step-by-Step Savings")
                st.caption("Showing exact component matches between baseline and optimized runs.")
                
                step_results = {}
                for step in matching_steps:
                    pre_c = df_pre[df_pre['Clean_Step'] == step]['Total Carbon Footprint (g CO2e)'].sum()
                    post_c = df_post[df_post['Clean_Step'] == step]['Total Carbon Footprint (g CO2e)'].sum()
                    saved = pre_c - post_c
                    pct = (saved / pre_c * 100) if pre_c > 0 else 0
                    step_results[step] = {'saved': saved, 'pct': pct, 'pre': pre_c, 'post': post_c}
                
                best_step = max(step_results, key=lambda k: step_results[k]['pct']) if step_results else None
                
                cols = st.columns(3) 
                col_idx = 0
                
                for step, metrics in step_results.items():
                    with cols[col_idx % 3]:
                        with st.container(border=True):
                            badge = "🏆" if step == best_step and metrics['pct'] > 0 else "🧩"
                            st.markdown(f"**{badge} `{step}`**")
                            
                            if metrics['pct'] < 0:
                                st.error("Regression Detected")
                            elif metrics['pct'] > 0:
                                st.success("Improved Efficiency")
                            else:
                                st.info("No Change")

                            if is_ai_proj:
                                step_pre_cost = df_pre[df_pre['Clean_Step'] == step]['Estimated_Cost_$'].sum()
                                step_post_cost = df_post[df_post['Clean_Step'] == step]['Estimated_Cost_$'].sum()
                                step_cost_saved = step_pre_cost - step_post_cost
                                step_cost_pct = (step_cost_saved / step_pre_cost * 100) if step_pre_cost > 0 else 0
                                
                                st.metric("Carbon Reduction", f"{format_european(metrics['saved'])} gCO₂e", f"{format_european(-metrics['pct'])}%", delta_color="inverse")
                                st.metric("FinOps Savings", f"${format_european(step_cost_saved)}", f"{format_european(-step_cost_pct)}%", delta_color="inverse")
                            else:
                                st.metric("Carbon Reduction", f"{format_european(metrics['saved'])} gCO₂e", f"{format_european(-metrics['pct'])}%", delta_color="inverse")
                    col_idx += 1