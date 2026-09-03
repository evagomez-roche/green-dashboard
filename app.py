import os
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import numpy as np
import re

# =====================================================================
# PAGE CONFIGURATION & KNOWN METADATA
# =====================================================================
st.set_page_config(
    page_title="SCI Carbon Dashboard",
    page_icon="🌱",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Manual fallback for measurement periods until integrated into the SQLite schema
KNOWN_PERIODS = {
    "Galileo": "Unknown (Jan 1st, 2025 - Aug 5th, 2026)",
    "myCO2": "1 Hour (Frontend/API) - Pending DWH merge",
    "Archimedes Lever": "Unknown",
    "ris-ase-fp-analyzer": "5 Minutes (Automated Assessment)"
}

# Custom Color Palettes for High Contrast Bar Charts
DARK_PURPLES = ['#4A235A', '#5B2C6F', '#6C3483', '#7D3C98', '#8E44AD', '#9B59B6', '#AF7AC5', '#C39BD3', '#D2B4DE', '#E8DAEF']
DARK_BLUES = ['#154360', '#1A5276', '#1F618D', '#2471A3', '#2980B9', '#5499C7', '#7FB3D5', '#A9CCE3', '#D4E6F1', '#EAF2F8']

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
# FORMATTING & NORMALIZATION HELPERS
# =====================================================================
def format_european(val):
    if pd.isna(val):
        return val
    if isinstance(val, (int, float)):
        if isinstance(val, int) or val.is_integer():
            return f"{int(val):,}".replace(',', '.')
        
        abs_val = abs(val)
        if 0 < abs_val < 0.01:
            formatted = f"{val:,.4f}"
        else:
            formatted = f"{val:,.2f}"
            
        return formatted.translate(str.maketrans(',.', '.,'))
    return val

def format_dataframe_display(df):
    df_display = df.copy()
    for col in df_display.columns:
        if df_display[col].dtype in ['float64', 'int64']:
            if 'Token' in col or 'IS_AI' in col:
                df_display[col] = df_display[col].replace([np.inf, -np.inf], np.nan)
                df_display[col] = df_display[col].apply(lambda x: str(int(x)) if pd.notna(x) else x)
            else:
                df_display[col] = df_display[col].apply(format_european)
        if 'Cost_$' in col and df_display[col].dtype == 'object':
             df_display[col] = df_display[col].str.replace('.', ',')
    return df_display

def add_percent_to_labels(df, value_col, label_col):
    total = df[value_col].sum()
    if total > 0:
        def format_pct(val):
            pct = (val / total) * 100
            if pct == 0:
                return "0%"
            elif pct < 0.001:
                return "<0.001%"
            elif pct < 1.0:
                return f"{pct:.3f}%"
            else:
                return f"{pct:.1f}%"
        df[label_col] = df.apply(lambda row: f"{row[label_col]} ({format_pct(row[value_col])})", axis=1)
    return df

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

def parse_period_to_hours(period_str):
    if pd.isna(period_str) or not isinstance(period_str, str):
        return None
        
    s = period_str.lower()
    qty_match = re.search(r'([\d\.]+)', s)
    qty = float(qty_match.group(1)) if qty_match else 1.0
    
    if 'minute' in s or 'min' in s: return qty * (1/60)
    if 'hour' in s or 'hr' in s: return qty * 1
    if 'day' in s: return qty * 24
    if 'week' in s: return qty * 168
    if 'month' in s: return qty * 730
    if 'year' in s: return qty * 8760
    
    return None

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
    
    base_cols = ['Date', 'Project', 'SCI Score (g CO2e/tx)', 'Component / Step' if 'Component / Step' in filtered_df.columns else 'PROCESS_DESC']
    display_cols = [c for c in base_cols if c in filtered_df.columns]
    
    other_cols = ['Total Carbon Footprint (g CO2e)', 'Functional Unit Details', 'IS_AI']
    display_cols.extend([c for c in other_cols if c in filtered_df.columns])
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
        proj_agg = add_percent_to_labels(proj_agg, 'Total Carbon Footprint (g CO2e)', 'Project')
        fig_pie = px.pie(proj_agg, values='Total Carbon Footprint (g CO2e)', names='Project', title="Footprint by Project", hole=0.4)
        fig_pie.update_traces(textinfo='none')
        st.plotly_chart(fig_pie, use_container_width=True)
    with c2:
        df_tab1['Workload_Type'] = df_tab1['IS_AI'].apply(lambda x: 'AI / LLM Inference' if x == 1 else 'Standard Code')
        ai_agg = df_tab1.groupby('Workload_Type')['Total Carbon Footprint (g CO2e)'].sum().reset_index()
        fig_ai = px.bar(ai_agg, x='Workload_Type', y='Total Carbon Footprint (g CO2e)', title="Footprint by Workload Type", color='Workload_Type', color_discrete_map={'Standard Code': '#2ecc71', 'AI / LLM Inference': '#9b59b6'})
        st.plotly_chart(fig_ai, use_container_width=True)

    st.divider()
    st.subheader("⚡ Average SCI Breakdown: Operational (E × I) vs Embodied Carbon (M)")
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
    
    global_avg_sci = df_tab1['SCI Score (g CO2e/tx)'].mean()
    if pd.notna(global_avg_sci):
        fig_stack.add_hline(y=global_avg_sci, line_dash="dash", line_color="gray", 
                            annotation_text=f"Global Avg SCI: {global_avg_sci:.2f}", 
                            annotation_position="top left")
    
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
            std_agg = add_percent_to_labels(std_agg, 'Total Carbon Footprint (g CO2e)', 'Project')
            fig_pie_std = px.pie(std_agg, values='Total Carbon Footprint (g CO2e)', names='Project', title="Carbon Footprint by Standard Project", hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
            fig_pie_std.update_traces(textinfo='none')
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
            fig_stack_std.update_layout(barmode='stack', yaxis_title='gCO₂e / transaction')
            
            std_avg_sci = df_tab2['SCI Score (g CO2e/tx)'].mean()
            if pd.notna(std_avg_sci):
                fig_stack_std.add_hline(y=std_avg_sci, line_dash="dash", line_color="gray", 
                                        annotation_text=f"Std Avg SCI: {std_avg_sci:.2f}", 
                                        annotation_position="top left")
                                        
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
        
        base_cols_ai = ['Date', 'Project', 'SCI Score (g CO2e/tx)', 'AI_MODEL_NAME']
        display_cols_ai = [c for c in base_cols_ai if c in df_ai_base.columns]
        other_cols_ai = ['Total_Tokens', 'Total Carbon Footprint (g CO2e)', 'Estimated_Cost_$']
        display_cols_ai.extend([c for c in other_cols_ai if c in df_ai_base.columns])
        
        display_cols_ai.extend([c for c in df_ai_base.columns if c not in display_cols_ai and c != 'Join_Key'])
        
        formatted_df_ai = df_ai_base.copy()
        
        df_display_tab3 = format_dataframe_display(formatted_df_ai[display_cols_ai].sort_values(by="Date", ascending=False))
        if 'Estimated_Cost_$' in df_display_tab3.columns:
            df_display_tab3['Estimated_Cost_$'] = df_ai_base['Estimated_Cost_$'].apply(lambda x: f"${x:.4f}".replace('.', ','))

        st.dataframe(df_display_tab3, use_container_width=True)
        st.divider()

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
        
        ai_agg_proj = df_tab3.groupby('Project')['Total Carbon Footprint (g CO2e)'].sum().reset_index()
        ai_agg_proj = add_percent_to_labels(ai_agg_proj, 'Total Carbon Footprint (g CO2e)', 'Project')
        fig_pie_ai = px.pie(ai_agg_proj, values='Total Carbon Footprint (g CO2e)', names='Project', title="Carbon Footprint by AI Project", hole=0.4, color_discrete_sequence=px.colors.qualitative.Pastel)
        fig_pie_ai.update_traces(textinfo='none')
        st.plotly_chart(fig_pie_ai, use_container_width=True)
        
        st.write("") 
        
        total_cost_str = f"${df_tab3['Estimated_Cost_$'].sum():.4f}".replace('.', ',')
        st.markdown(f'<div class="metric-card-ai" style="text-align: center; margin-bottom: 20px;"><h4>Total FinOps Cost Across All AI Projects</h4><h2 style="font-size: 2.5rem !important;">{total_cost_str}</h2></div>', unsafe_allow_html=True)
        
        cost_agg_proj = df_tab3.groupby('Project')['Estimated_Cost_$'].sum().reset_index()
        fig_cost_proj = px.bar(cost_agg_proj, x='Project', y='Estimated_Cost_$', title="Financial Cost by Project ($)", color_discrete_sequence=['#3498db'])
        st.plotly_chart(fig_cost_proj, use_container_width=True)

   # =====================================================================
# FORMATTING HELPERS (Add these at the top of your app.py)
# =====================================================================
def format_time_duration(seconds):
    """Converts seconds into a human-readable compound format (e.g., '1 week and 4 days')"""
    if seconds <= 0: return "0 seconds"
    if seconds < 60: return f"{int(seconds)} seconds"
    
    minutes = seconds // 60
    sec_rem = seconds % 60
    if minutes < 60: 
        return f"{int(minutes)} minutes" + (f" and {int(sec_rem)} seconds" if sec_rem > 0 else "")
    
    hours = seconds // 3600
    min_rem = (seconds % 3600) // 60
    if hours < 24: 
        return f"{int(hours)} hours" + (f" and {int(min_rem)} minutes" if min_rem > 0 else "")
    
    days = seconds // 86400
    hours_rem = (seconds % 86400) // 3600
    if days < 7: 
        return f"{int(days)} days" + (f" and {int(hours_rem)} hours" if hours_rem > 0 else "")
    
    weeks = seconds // 604800
    days_rem = (seconds % 604800) // 86400
    if weeks < 4: 
        return f"{int(weeks)} weeks" + (f" and {int(days_rem)} days" if days_rem > 0 else "")
    
    months = seconds // 2592000 # Approx 30 days
    weeks_rem = (seconds % 2592000) // 604800
    if months < 12: 
        return f"{int(months)} months" + (f" and {int(weeks_rem)} weeks" if weeks_rem > 0 else "")
    
    years = seconds // 31536000
    months_rem = (seconds % 31536000) // 2592000
    return f"{int(years)} years" + (f" and {int(months_rem)} months" if months_rem > 0 else "")

def format_uk_pct(pct):
    """Formats percentage with dynamic decimals depending on scale."""
    if pct == 0: return "0%"
    elif pct >= 1: return f"{pct:.2f}%"
    elif pct >= 0.01: return f"{pct:.2f}%" 
    elif pct >= 0.0001: return f"{pct:.4f}%" 
    else: return "~0% (negligible fraction)"

# =====================================================================
# TAB 4: PROJECT DRILL-DOWN 
# =====================================================================
with tab_drilldown:
    st.subheader("🔍 Detailed Project Analysis")
    st.markdown("Isolate a specific project to identify granular carbon bottlenecks, steps, or individual AI agents.")
    
    selected_proj_drill = st.selectbox("Select a project to analyze:", options=filtered_df['Project'].unique())
    
    if selected_proj_drill:
        df_proj = filtered_df[filtered_df['Project'] == selected_proj_drill].copy()
        is_ai_proj = df_proj['IS_AI'].iloc[0] == 1 if not df_proj.empty else False
        
        if 'MEASUREMENT_PERIOD' not in df_proj.columns:
            df_proj['MEASUREMENT_PERIOD'] = np.nan
            
        df_proj['MEASUREMENT_PERIOD'] = df_proj['MEASUREMENT_PERIOD'].fillna(KNOWN_PERIODS.get(selected_proj_drill, "Unknown"))
        period_text = df_proj['MEASUREMENT_PERIOD'].iloc[0]

        # Check if period is mathematically parseable (not unknown)
        can_normalize = False
        for idx, row in df_proj.iterrows():
            if parse_period_to_hours(row['MEASUREMENT_PERIOD']) is not None:
                can_normalize = True
                break
        
        # --- TIME NORMALIZATION ENGINE ---
        if can_normalize and "unknown" not in period_text.lower():
            df_proj_raw = df_proj.copy() # Keep raw copy for projection chart
            
            st.write("")
            st.markdown("### ⏱️ Time Normalization Engine")
            st.info("Different components in this project were measured over different timeframes. Select a target timeframe to mathematically project and align all absolute metrics (Total Carbon, Energy, Cost). **SCI Scores remain unaffected.**")
            
            target_scale = st.radio(
                "Project metrics to:", 
                options=["Raw Data (No Scaling)", "Hourly (1h)", "Daily (24h)", "Monthly (730h)", "Yearly (8760h)"],
                horizontal=True
            )
            
            scale_map = {"Hourly (1h)": 1, "Daily (24h)": 24, "Monthly (730h)": 730, "Yearly (8760h)": 8760}
            
            # 1. Apply user selection mathematically to the dataframe FIRST
            if target_scale != "Raw Data (No Scaling)":
                target_hours = scale_map[target_scale]
                for idx, row in df_proj.iterrows():
                    base_hours = parse_period_to_hours(row['MEASUREMENT_PERIOD'])
                    if base_hours and base_hours > 0:
                        multiplier = target_hours / base_hours
                        df_proj.at[idx, 'Total Carbon Footprint (g CO2e)'] *= multiplier
                        df_proj.at[idx, 'Energy Consumed - E (kWh)'] *= multiplier
                        if 'Estimated_Cost_$' in df_proj.columns:
                            df_proj.at[idx, 'Estimated_Cost_$'] *= multiplier
                            
                        suffix = f" [Projected to {target_scale.split(' ')[0]}]"
                        df_proj.at[idx, 'PROCESS_DESC'] = f"{row['PROCESS_DESC']}{suffix}"
                        if pd.notna(row['AI_MODEL_NAME']):
                            df_proj.at[idx, 'AI_MODEL_NAME'] = f"{row['AI_MODEL_NAME']}{suffix}"

            # 2. Sub-feature: Dynamic Component Comparison Chart
            with st.expander("📊 View Component Comparison Chart", expanded=False):
                proj_metric = st.selectbox(
                    "Select metric to compare across components:", 
                    ["Total Carbon Footprint (g CO2e)", "Energy Consumed - E (kWh)", "Estimated_Cost_$"]
                )
                
                group_col_compare = 'AI_MODEL_NAME' if is_ai_proj else 'PROCESS_DESC'
                
                # Group by component using the currently scaled data
                compare_df = df_proj.groupby(group_col_compare)[proj_metric].sum().reset_index()
                compare_df = compare_df.sort_values(by=proj_metric, ascending=False)
                
                # Dynamic orientation based on item count
                num_compare_items = len(compare_df)
                is_horizontal_comp = num_compare_items >= 8
                comp_orientation = 'h' if is_horizontal_comp else 'v'
                comp_height = max(400, num_compare_items * 25) if is_horizontal_comp else 400
                
                x_ax = proj_metric if is_horizontal_comp else group_col_compare
                y_ax = group_col_compare if is_horizontal_comp else proj_metric
                
                # Use sequential colors depending on the metric
                color_seq = DARK_BLUES if 'Cost' in proj_metric else DARK_PURPLES
                
                fig_comp = px.bar(
                    compare_df, x=x_ax, y=y_ax, color=group_col_compare,
                    title=f"{proj_metric.split(' (')[0]} by Component ({target_scale})", 
                    orientation=comp_orientation, height=comp_height,
                    color_discrete_sequence=color_seq
                )
                st.plotly_chart(fig_comp, use_container_width=True)
                
        # --- PROJECT SPECIFIC KPIS ---
        st.divider()
        st.subheader(f"📈 Key Impact Metrics (KPIs): {selected_proj_drill}")
        pm1, pm2, pm3, pm4 = st.columns(4)
        
        proj_total_carbon = df_proj['Total Carbon Footprint (g CO2e)'].sum()
        proj_avg_sci = df_proj['SCI Score (g CO2e/tx)'].mean()
        proj_total_energy = df_proj['Energy Consumed - E (kWh)'].sum()
        
        if not df_proj.empty:
            hotspot_idx = df_proj['Total Carbon Footprint (g CO2e)'].idxmax()
            proj_hotspot = df_proj.loc[hotspot_idx]['PROCESS_DESC'] if not is_ai_proj else df_proj.loc[hotspot_idx]['AI_MODEL_NAME']
        else:
            proj_hotspot = "N/A"
            
        with pm1:
            st.markdown(f'<div class="metric-card"><h4>Total Carbon Footprint</h4><h2>{format_european(proj_total_carbon)} gCO₂e</h2></div>', unsafe_allow_html=True)
        with pm2:
            st.markdown(f'<div class="metric-card"><h4>Average SCI Score</h4><h2>{format_european(proj_avg_sci)} g/tx</h2></div>', unsafe_allow_html=True)
        with pm3:
            st.markdown(f'<div class="metric-card"><h4>Energy Consumed</h4><h2>{format_european(proj_total_energy)} kWh</h2></div>', unsafe_allow_html=True)
        with pm4:
            st.markdown(f'<div class="metric-card-danger"><h4>Highest Hotspot</h4><h2 style="font-size: 1.2rem !important; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{proj_hotspot}</h2></div>', unsafe_allow_html=True)
        
        st.write("")

        # --- BENCHMARK & CONTEXT (ANDY'S REQUEST) ---
        st.divider()
        st.markdown("### 📊 Benchmark & Real-World Context")
        
        # 1. Calculate Global SCI Statistics for context (GROUPED BY PROJECT)
        project_avg_scis = filtered_df.groupby('Project')['SCI Score (g CO2e/tx)'].mean()
        global_sci_stats = project_avg_scis.describe()
        global_median_sci = project_avg_scis.median()
        
        # DISPLAY THE 4 NUMBERS
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Global SCI (Min)", f"{format_european(global_sci_stats['min'])} g/tx")
        c2.metric("Global SCI (Median)", f"{format_european(global_median_sci)} g/tx")
        c3.metric("Global SCI (Average)", f"{format_european(global_sci_stats['mean'])} g/tx")
        c4.metric("Global SCI (Max)", f"{format_european(global_sci_stats['max'])} g/tx")
        
        st.write("")
        
        # Explain Absolute vs Intensity comparison dynamically
        col_bench1, col_bench2 = st.columns(2)
        project_totals = filtered_df.groupby('Project')['Total Carbon Footprint (g CO2e)'].sum()
        global_median_carbon = project_totals.median()
        
        with col_bench1:
            st.markdown("**1. Absolute Environmental Impact (Total Carbon)**")
            if pd.notna(proj_total_carbon) and pd.notna(global_median_carbon):
                if proj_total_carbon > global_median_carbon:
                    st.warning(f"⚠️ **High Volume:** This project's total cumulative carbon ({format_european(proj_total_carbon)} gCO₂e) is **higher** than the global project median ({format_european(global_median_carbon)} gCO₂e).")
                else:
                    st.success(f"🌱 **Low Volume:** This project's total cumulative carbon ({format_european(proj_total_carbon)} gCO₂e) is **lower** than the global project median ({format_european(global_median_carbon)} gCO₂e).")
        
        with col_bench2:
            st.markdown("**2. Software Code Efficiency (Average SCI)**")
            if pd.notna(proj_avg_sci) and pd.notna(global_median_sci):
                if proj_avg_sci > global_median_sci:
                    st.warning(f"⚠️ **Opportunity for Improvement:** This project's intensity per transaction ({format_european(proj_avg_sci)} g/tx) is **higher** than the global median ({format_european(global_median_sci)} g/tx).")
                else:
                    st.success(f"🌱 **High Efficiency:** This project's intensity per transaction ({format_european(proj_avg_sci)} g/tx) is **lower** (better) than the global median ({format_european(global_median_sci)} g/tx).")

        st.caption("*Note: A project like a high-traffic AI model might have a large Total Carbon footprint due to high user demand, while maintaining a highly efficient SCI score per request.*")
        
        # --- EXECUTIVE INSIGHT (DYNAMIC CONTEXT - DIPLOMATIC TONE) ---
        st.write("")
        st.markdown("#### 🧠 Executive Insight")
        
        if pd.notna(proj_total_carbon) and pd.notna(global_median_carbon) and pd.notna(proj_avg_sci) and pd.notna(global_median_sci):
            is_high_carbon = proj_total_carbon > global_median_carbon
            is_high_sci = proj_avg_sci > global_median_sci
            
            if is_high_carbon and not is_high_sci:
                st.success(
                    "**Scale with Efficiency:** While the total footprint reflects a massive processing volume, the SCI score remains below the global average. "
                    "This indicates the software architecture is successfully handling high demand with strong per-transaction efficiency. "
                    "The footprint is driven by business growth, not architectural waste."
                )
            elif not is_high_carbon and is_high_sci:
                st.info(
                    "**Early Optimization Opportunity:** Current absolute emissions are kept low by moderate usage volumes. "
                    "However, the higher-than-average SCI suggests there is room to refine the code. "
                    "Addressing this proactively will prevent disproportionate cloud costs and emissions as the project scales."
                )
            elif is_high_carbon and is_high_sci:
                st.warning(
                    "**High-Impact Optimization Area:** This project operates at a significant scale and currently shows a higher-than-average SCI. "
                    "This presents an excellent opportunity: even minor architectural or code-level optimizations here will yield massive, "
                    "highly visible reductions in both global CO₂ emissions and financial operating costs."
                )
            else:
                st.success(
                    "**Sustainable Baseline:** This project currently operates with both a low absolute footprint and a highly efficient SCI score. "
                    "It serves as a strong internal benchmark for Green Software best practices."
                )
        
        st.write("")
        
        # 2. Real World Equivalents & Intensity Context
        st.markdown("#### 🌍 Real-World Equivalents & Intensity Context")
        
        # Intensity Context (SCI)
        if is_ai_proj:
            st.info(f"💡 **Intensity Context (AI):** This project's average SCI is **{format_european(proj_avg_sci)} gCO₂e/tx**. For comparison, an average ChatGPT or Gemini text prompt emits between **2.0 and 3.0 gCO₂e**.")
        else:
            st.info(f"💡 **Intensity Context (Standard):** This project's average SCI is **{format_european(proj_avg_sci)} gCO₂e/tx**. For comparison, a standard Google web search emits roughly **0.2 gCO₂e**.")

        # Total Impact Context (Energy & Carbon)
        if proj_total_energy > 0 or proj_total_carbon > 0:
            if is_ai_proj:
                # AI Context 
                total_wh = proj_total_energy * 1000
                gemini_queries_eq = total_wh / 0.24 if total_wh > 0 else 0
                
                microwave_seconds = gemini_queries_eq * 1
                fridge_seconds = gemini_queries_eq * 6
                
                microwave_str = format_time_duration(microwave_seconds)
                fridge_str = format_time_duration(fridge_seconds)
                
                st.info(
                    f"💡 **Energy Context (AI):** The {format_european(proj_total_energy)} kWh used by this project equals **{format_european(total_wh)} Wh**. "
                    f"According to recent Google estimates, a standard Gemini text query uses 0.24 Wh. "
                    f"This project's total energy is equivalent to running a microwave for **{microwave_str}** or a standard fridge for **{fridge_str}**."
                )
            else:
                # Standard Context (Non-AI) with small impact protection
                if proj_total_carbon < 8.0:
                    st.info(f"💡 **Energy Context (Standard Code):** The {format_european(proj_total_carbon)} gCO₂e emitted is so minimal that it's not even enough to fully charge a single smartphone (which takes ~8g CO₂e).")
                else:
                    smartphones = int(proj_total_carbon / 8.0)
                    km_driven = proj_total_carbon / 192.0
                    
                    if km_driven < 1:
                        st.info(f"💡 **Energy Context (Standard Code):** The {format_european(proj_total_carbon)} gCO₂e emitted by this standard software project is equivalent to charging **{smartphones} smartphones** (less than 1 km of driving a gas car).")
                    else:
                        st.info(f"💡 **Energy Context (Standard Code):** The {format_european(proj_total_carbon)} gCO₂e emitted by this standard software project is equivalent to the carbon footprint of driving a gasoline car for **{format_european(km_driven)} km** or charging **{smartphones} smartphones**.")
                
            # Shared Carbon Context applied to BOTH AI and Non-AI
            uk_annual_carbon_g = 7000000.0 # 7 tonnes
            pct_of_uk_citizen = (proj_total_carbon / uk_annual_carbon_g) * 100
            
            st.info(
                f"💡 **Carbon Context:** The average UK citizen emits 7 tonnes of CO₂ annually from energy and industry. "
                f"This project's total footprint ({format_european(proj_total_carbon)} gCO₂e) represents **{format_uk_pct(pct_of_uk_citizen)}** of that annual footprint."
            )
        else:
            st.info("💡 **Context:** The recorded energy and carbon totals for this timeframe are currently **0**. Process more data to see real-world equivalents.")


        # Disclaimers
        if selected_proj_drill == "Galileo":
            st.info(
                "**Galileo Telemetry Data Notes:**\n\n"
                "• **Token Aggregation:** The raw dataset provides a single combined 'Tokens' count. Since the exact split between prompt and completion tokens is unavailable, all consumed tokens have been aggregated and logged under `PROMPT_TOKENS`.\n\n"
                "• **Data Filtering & Integrity:** The initial raw dataset contained over 2,500 entries. To maintain accurate SCI and carbon calculations, rows with an 'Unknown' provider were excluded. A final filter to match supported and recognized AI models finalized the dataset at exactly **186 valid rows**."
            )

        if selected_proj_drill == "Archimedes Lever":
            st.warning(
                "**Proxy Model Estimation (Archimedes Lever):**\n\n"
                "The baseline telemetry for this project was originally recorded using Anthropic's 'Opus 5'. "
                "Since this specific model version is not yet supported by standard carbon tracking APIs, "
                "the footprint and costs were calculated using **Claude 3 Opus** as an accurate proxy model."
            )
        
        df_proj['Clean_Step'] = df_proj['PROCESS_DESC'].str.replace(' (POST-OPTIMIZATION)', '', regex=False)
        df_proj['Status'] = np.where(df_proj['Is_Post'], 'Post-Optimization', 'Baseline')
        
        group_col_table = 'AI_MODEL_NAME' if is_ai_proj else 'PROCESS_DESC'
        breakdown_table = df_proj.groupby(group_col_table).agg({
            'SCI Score (g CO2e/tx)': 'mean',
            'Energy Consumed - E (kWh)': 'sum', 
            'Total Carbon Footprint (g CO2e)': 'sum', 
            'Estimated_Cost_$': 'sum',
            'MEASUREMENT_PERIOD': 'first'
        }).reset_index()
        
        breakdown_table = breakdown_table.rename(columns={
            group_col_table: 'Component / Step', 
            'SCI Score (g CO2e/tx)': 'Average SCI Score (g CO2e/tx)',
            'MEASUREMENT_PERIOD': 'Original Period'
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

        num_items = len(breakdown_chart)
        is_horizontal = num_items >= 8
        orientation_flag = 'h' if is_horizontal else 'v'
        
        breakdown_chart = breakdown_chart.sort_values(by='Total Carbon Footprint (g CO2e)', ascending=is_horizontal)
        dynamic_chart_height = max(450, num_items * 25) if is_horizontal else 450
        
        x_carbon = 'Total Carbon Footprint (g CO2e)' if is_horizontal else 'Component / Step'
        y_carbon = 'Component / Step' if is_horizontal else 'Total Carbon Footprint (g CO2e)'
        
        x_cost = 'Estimated_Cost_$' if is_horizontal else 'Component / Step'
        y_cost = 'Component / Step' if is_horizontal else 'Estimated_Cost_$'

        st.write("") 

        fig_drill_carbon = px.bar(
            breakdown_chart, x=x_carbon, y=y_carbon, color='Component / Step',
            title="Carbon Impact Breakdown", 
            orientation=orientation_flag, height=dynamic_chart_height,
            color_discrete_sequence=DARK_PURPLES
        )
        st.plotly_chart(fig_drill_carbon, use_container_width=True)
        
        if is_ai_proj:
            fig_drill_cost = px.bar(
                breakdown_chart, x=x_cost, y=y_cost, color='Component / Step',
                title="Cost Impact Breakdown ($)", 
                orientation=orientation_flag, height=dynamic_chart_height,
                color_discrete_sequence=DARK_BLUES
            )
            st.plotly_chart(fig_drill_cost, use_container_width=True)

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
            
            if carbon_saved >= 8.0:
                st.markdown("#### 🌍 Real-World Impact Equivalent")
                smartphones = int(carbon_saved / 8.0) 
                
                if carbon_saved >= 192.0:
                    km_driven = carbon_saved / 192.0 
                    st.info(f"💡 The **{format_european(carbon_saved)} gCO₂e** saved by optimizing this project is equivalent to the carbon footprint of driving a gasoline car for **{format_european(km_driven)} km** or charging **{smartphones} smartphones**!")
                else:
                    st.info(f"💡 The **{format_european(carbon_saved)} gCO₂e** saved by optimizing this project is equivalent to the carbon footprint of charging **{smartphones} smartphones**!")   
            
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