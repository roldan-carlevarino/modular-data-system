import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import numpy as np
import warnings
import logging
import os

# Suprimir warnings
warnings.filterwarnings('ignore')

# Suprimir logs de kaleido y streamlit
logging.getLogger('kaleido').setLevel(logging.ERROR)
logging.getLogger('streamlit').setLevel(logging.ERROR)
os.environ['STREAMLIT_SERVER_LOGGER_LEVEL'] = 'error'

st.set_page_config(page_title="Pipeline Dashboard", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
    <style>
    * {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }
    
    :root {
        color-scheme: light dark;
    }
    
    [data-theme="light"] {
        --text-primary: #1f2937;
        --text-secondary: #6b7280;
        --bg-primary: #f5f7fa;
        --border-color: #e6ebf1;
        --accent-color: #FF6B35;
    }
    
    [data-theme="dark"] {
        --text-primary: #e5e7eb;
        --text-secondary: #9ca3af;
        --bg-primary: #1f2937;
        --border-color: #374151;
        --accent-color: #FF6B35;
    }
    
    .stMetric {
        background-color: rgba(255, 255, 255, 0.05);
        border: 1px solid var(--border-color, #e6ebf1);
        padding: 15px;
        border-radius: 4px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    
    .stMetricLabel {
        color: var(--text-secondary, #6b7280);
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    
    .stMetricValue {
        color: var(--text-primary, #1f2937);
        font-size: 24px;
        font-weight: 700;
    }
    
    h1, h2, h3 {
        color: var(--text-primary, #1f2937);
        font-weight: 600;
    }
    
    .stTabs [data-baseweb="tab-list"] {
        gap: 0;
        border-bottom: 2px solid var(--border-color, #e6ebf1);
    }
    
    .stTabs [data-baseweb="tab"] {
        border-bottom: 3px solid transparent;
        color: var(--text-secondary, #6b7280);
        font-weight: 500;
        padding: 12px 16px;
    }
    
    .stTabs [data-baseweb="tab"]:hover {
        color: var(--text-primary, #1f2937);
    }
    
    .stTabs [aria-selected="true"] [data-baseweb="tab"] {
        border-bottom: 3px solid #FF6B35;
        color: #FF6B35;
    }
    
    .stDataFrame {
        border: 1px solid var(--border-color, #e6ebf1);
        border-radius: 4px;
        background-color: var(--bg-primary, #ffffff);
    }
    
    .stButton > button {
        background-color: #FF6B35;
        color: white;
        border: none;
        border-radius: 4px;
        font-weight: 600;
        padding: 8px 16px;
        transition: background-color 0.2s;
    }
    
    .stButton > button:hover {
        background-color: #E55A24;
    }
    
    .stTextInput > div > div > input,
    .stSelectbox > div > div > select,
    .stNumberInput > div > div > input,
    .stSlider > div > div > div {
        border: 1px solid var(--border-color, #e6ebf1);
        border-radius: 4px;
        padding: 8px 12px;
        font-size: 14px;
        background-color: var(--bg-primary, #ffffff);
        color: var(--text-primary, #1f2937);
    }
    
    .stSuccess {
        background-color: #d4edda;
        color: #155724;
        border: 1px solid #c3e6cb;
        border-radius: 4px;
        padding: 12px;
    }
    
    .stWarning {
        background-color: #fff3cd;
        color: #856404;
        border: 1px solid #ffeaa7;
        border-radius: 4px;
        padding: 12px;
    }
    
    .stInfo {
        background-color: #d1ecf1;
        color: #0c5460;
        border: 1px solid #bee5eb;
        border-radius: 4px;
        padding: 12px;
    }
    
    /* Estilos minimalistas para tags */
    [data-baseweb="tag"] {
        background-color: #f0f0f0 !important;
        color: #4a5568 !important;
        border: 1px solid #d0d0d0 !important;
        border-radius: 4px;
        padding: 6px 12px !important;
        font-weight: 500;
        font-size: 13px;
    }
    
    [data-baseweb="tag"] svg {
        color: #9a9a9a !important;
    }
    
    [data-baseweb="tag"]:hover {
        background-color: #e8e8e8 !important;
        border-color: #b0b0b0 !important;
    }
    </style>
""", unsafe_allow_html=True)

# Initialize session state for new companies
if 'new_companies' not in st.session_state:
    st.session_state.new_companies = []



# =====================
# 1. GENERATE FAKE DATA
# =====================

def generate_fake_data():
    """Generate realistic fake data for the search fund"""
    
    # Pipeline stages
    pipeline_stages = [
        "Teaser recibido",
        "NDA firmado",
        "CIM recibido",
        "Llamada con broker",
        "Llamada/reunión con propietario",
        "Análisis empresa",
        "Análisis industria",
        "Feedback",
        "Inversionistas",
        "LOI enviado",
        "LOI firmado",
        "Due diligence",
        "Financiamiento",
        "Cierre"
    ]
    
    # Sectors
    sectors = ["Logística", "Salud", "Manufactura", "Educación"]
    
    # Sample companies with deals at different stages
    companies_data = []
    company_names = [
        "LogisticaPro", "TransHealth", "ManuFactura SA", "EduTech Academy",
        "HealthFlow", "LogisticsHub", "SmartFactory", "LearnHub Plus",
        "MediCare Plus", "FastShip Logistics", "PrecisionMfg", "UniversidadPlus",
        "BioDynamics", "SupplyChainCo", "AutomationWorks", "OnlineEdu Corp",
        "CargaExpress", "DrugMaster", "RoboFactory", "EscuelaVirtual",
        "ClínicaPlus", "TransportGo", "IndustriaX", "AcademiaOnline",
        "FarmaLab", "ShipFast", "TechManu", "EduConnect",
        "HealthTech", "LogisticHub", "Factory Pro", "EduGlobal"
    ]
    
    # Create companies with their data
    for i, company_name in enumerate(company_names[:28]):
        sector = sectors[i % len(sectors)]
        current_stage = np.random.randint(0, 14)
        
        # Descarte más probable en etapas tempranas, menos en etapas finales
        if current_stage < 4:
            probabilidad_descarte = 0.45  # 45% en etapas tempranas
        elif current_stage < 9:
            probabilidad_descarte = 0.25  # 25% en etapas intermedias
        else:
            probabilidad_descarte = 0.08  # 8% en etapas finales
        
        companies_data.append({
            "Empresa": company_name,
            "Sector": sector,
            "Etapa": pipeline_stages[current_stage],
            "Etapa_Num": current_stage,
            "Ventas": np.random.uniform(500000, 5000000),
            "EBITDA": np.random.uniform(50000, 800000),
            "EBITDA_Pct": np.random.uniform(5, 25),
            "Status": "Activo" if current_stage < 14 else "Cerrado",
            "Descartado": np.random.random() < probabilidad_descarte,
            "Dias_Etapa": np.random.randint(5, 120)
        })
    
    df_companies = pd.DataFrame(companies_data)
    
    # Add new companies from session state
    if st.session_state.new_companies:
        df_new = pd.DataFrame(st.session_state.new_companies)
        df_companies = pd.concat([df_companies, df_new], ignore_index=True)
    
    return pipeline_stages, sectors, df_companies

pipeline_stages, sectors, df_companies = generate_fake_data()

# =====================
# TABS FOR VIEWS
# =====================
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "Funnel de Adquisición",
    "Efectividad Outreach",
    "Mix Sectorial",
    "Información por Empresa",
    "Velocidad del Pipeline",
    "Registrar Operación"
])

# =====================
# TAB 1: FUNNEL DE ADQUISICIÓN
# =====================
with tab1:
    st.subheader("Pipeline de Adquisición")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        # Count deals by stage
        deals_by_stage = df_companies[~df_companies["Descartado"]].groupby("Etapa_Num").size().reset_index(name="Cantidad")
        deals_by_stage["Etapa"] = deals_by_stage["Etapa_Num"].map(
            {i: pipeline_stages[i] for i in range(len(pipeline_stages))}
        )
        deals_by_stage = deals_by_stage.sort_values("Etapa_Num")
        
        # Create funnel chart
        fig_funnel = go.Figure(data=[go.Funnel(
            y=deals_by_stage["Etapa"],
            x=deals_by_stage["Cantidad"],
            marker=dict(color="#FF6B35"),
            textposition="inside",
            textinfo="value+percent initial"
        )])
        
        fig_funnel.update_layout(
            height=600,
            title="Funnel_Deals_Activos_por_Etapa",
            margin=dict(l=200),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="sans-serif", color="#1f2937")
        )
        st.plotly_chart(fig_funnel, width='stretch', config={'displayModeBar': True, 'displaylogo': False, 'modeBarButtonsToAdd': [], 'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'resetScale2d', 'resetViewMapbox', 'zoom2d', 'pan2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'toggleHover', 'toggleSpikelines'], 'toImageButtonOptions': {'format': 'png', 'filename': 'Funnel_Deals_Activos_por_Etapa', 'height': 800, 'width': 1200, 'scale': 2}})
    
    # Deals descartados por etapa (incluir todas las etapas, aunque tengan 0)
    discarded_by_stage = df_companies[df_companies["Descartado"]].groupby("Etapa_Num").size().reset_index(name="Descartados")
    
    # Asegurar que todas las etapas aparecen (incluso con 0 descartados)
    all_stages = pd.DataFrame({"Etapa_Num": range(len(pipeline_stages))})
    discarded_by_stage = all_stages.merge(discarded_by_stage, on="Etapa_Num", how="left").fillna(0)
    discarded_by_stage["Descartados"] = discarded_by_stage["Descartados"].astype(int)
    
    discarded_by_stage["Etapa"] = discarded_by_stage["Etapa_Num"].map(
        {i: pipeline_stages[i] for i in range(len(pipeline_stages))}
    )
    discarded_by_stage = discarded_by_stage.sort_values("Descartados", ascending=True)
    
    with col2:
        fig_discarded = px.bar(
            discarded_by_stage,
            x="Descartados",
            y="Etapa",
            color="Descartados",
            color_continuous_scale=["#fee8e0", "#FF6B35"],
            title="Deals_Descartados_por_Etapa",
            orientation="h"
        )
        fig_discarded.update_xaxes(dtick=1)
        fig_discarded.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="sans-serif", color="#1f2937"),
            showlegend=False
        )
        st.plotly_chart(fig_discarded, width='stretch', config={'displayModeBar': True, 'displaylogo': False, 'modeBarButtonsToAdd': [], 'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'resetScale2d', 'resetViewMapbox', 'zoom2d', 'pan2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'toggleHover', 'toggleSpikelines'], 'toImageButtonOptions': {'format': 'png', 'filename': 'Deals_Descartados_por_Etapa', 'height': 600, 'width': 800, 'scale': 2}})
    
    st.divider()
    
    # Resumen
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(label="Deals Totales", value=len(df_companies))
    
    with col2:
        st.metric(label="Deals Activos", value=len(df_companies[~df_companies['Descartado']]))
    
    with col3:
        st.metric(label="Deals Descartados", value=len(df_companies[df_companies['Descartado']]))
    
    with col4:
        tasa_descarte = (len(df_companies[df_companies['Descartado']]) / len(df_companies)) * 100
        st.metric(label="Tasa Descarte", value=f"{tasa_descarte:.1f}%")

# =====================
# TAB 2: EFECTIVIDAD OUTREACH
# =====================
with tab2:
    st.subheader("Métricas de Outreach (HubSpot)")
    
    # Generate outreach data
    outreach_data = []
    for i in range(15):
        outreach_data.append({
            "Campaña": f"Campaña {chr(65+i)}",
            "Correos_Enviados": np.random.randint(50, 300),
            "Tasa_Apertura": np.random.uniform(15, 45),
            "Tasa_Respuesta": np.random.uniform(5, 20),
        })
    
    df_outreach = pd.DataFrame(outreach_data)
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        avg_open_rate = df_outreach["Tasa_Apertura"].mean()
        st.metric(label="Tasa Apertura Promedio", value=f"{avg_open_rate:.1f}%")
    
    with col2:
        avg_response_rate = df_outreach["Tasa_Respuesta"].mean()
        st.metric(label="Tasa Respuesta Promedio", value=f"{avg_response_rate:.1f}%")
    
    with col3:
        total_emails = df_outreach["Correos_Enviados"].sum()
        st.metric(label="Total Correos Enviados", value=f"{total_emails:,}")
    
    st.divider()
    
    # Charts
    col1, col2 = st.columns(2)
    
    with col1:
        fig_open = px.scatter(
            df_outreach,
            x="Correos_Enviados",
            y="Tasa_Apertura",
            size="Tasa_Respuesta",
            hover_data=["Campaña"],
            title="Outreach_Correos_vs_Apertura",
            labels={"Correos_Enviados": "Correos Enviados", "Tasa_Apertura": "Tasa Apertura (%)"}
        )
        fig_open.update_traces(marker=dict(color="#FF6B35", opacity=0.7))
        fig_open.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="sans-serif", color="#1f2937")
        )
        st.plotly_chart(fig_open, width='stretch', config={'displayModeBar': True, 'displaylogo': False, 'modeBarButtonsToAdd': [], 'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'resetScale2d', 'resetViewMapbox', 'zoom2d', 'pan2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'toggleHover', 'toggleSpikelines'], 'toImageButtonOptions': {'format': 'png', 'filename': 'Outreach_Correos_vs_Apertura', 'height': 600, 'width': 1000, 'scale': 2}})
    
    with col2:
        df_outreach_sorted = df_outreach.sort_values("Tasa_Respuesta", ascending=True).tail(10)
        fig_response = px.bar(
            df_outreach_sorted,
            x="Tasa_Respuesta",
            y="Campaña",
            color="Tasa_Respuesta",
            color_continuous_scale=["#E8F4F8", "#FF6B35"],
            title="Top_10_Campanas_Respuesta",
            orientation="h"
        )
        fig_response.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="sans-serif", color="#1f2937"),
            showlegend=False
        )
        st.plotly_chart(fig_response, width='stretch', config={'displayModeBar': True, 'displaylogo': False, 'modeBarButtonsToAdd': [], 'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'resetScale2d', 'resetViewMapbox', 'zoom2d', 'pan2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'toggleHover', 'toggleSpikelines'], 'toImageButtonOptions': {'format': 'png', 'filename': 'Top_10_Campanas_Respuesta', 'height': 600, 'width': 900, 'scale': 2}})
    
    # Data table
    st.write("**Detalle de Campañas:**")
    df_outreach_display = df_outreach.copy()
    df_outreach_display["Tasa_Apertura"] = df_outreach_display["Tasa_Apertura"].round(1).astype(str) + "%"
    df_outreach_display["Tasa_Respuesta"] = df_outreach_display["Tasa_Respuesta"].round(1).astype(str) + "%"
    st.dataframe(df_outreach_display, width='stretch')

# =====================
# TAB 3: MIX SECTORIAL
# =====================
with tab3:
    st.subheader("Distribución Sectorial")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Companies by sector
        sector_counts = df_companies["Sector"].value_counts()
        
        fig_sector = px.pie(
            values=sector_counts.values,
            names=sector_counts.index,
            title="Mix_Empresas_por_Sector",
            color_discrete_sequence=["#FF6B35", "#1E90FF", "#6B7280", "#FFA500"]
        )
        fig_sector.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="sans-serif", color="#1f2937")
        )
        st.plotly_chart(fig_sector, width='stretch', config={'displayModeBar': True, 'displaylogo': False, 'modeBarButtonsToAdd': [], 'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'resetScale2d', 'resetViewMapbox', 'zoom2d', 'pan2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'toggleHover', 'toggleSpikelines'], 'toImageButtonOptions': {'format': 'png', 'filename': 'Mix_Empresas_por_Sector', 'height': 700, 'width': 800, 'scale': 2}})
    
    with col2:
        # Revenue by sector
        sector_revenue = df_companies.groupby("Sector")["Ventas"].sum().sort_values(ascending=True)
        
        fig_revenue = px.bar(
            x=sector_revenue.values,
            y=sector_revenue.index,
            color=sector_revenue.values,
            color_continuous_scale=["#E8F4F8", "#FF6B35"],
            title="Ingresos_Totales_por_Sector",
            labels={"x": "Ingresos ($)", "y": "Sector"},
            orientation="h"
        )
        fig_revenue.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="sans-serif", color="#1f2937"),
            showlegend=False
        )
        st.plotly_chart(fig_revenue, width='stretch', config={'displayModeBar': True, 'displaylogo': False, 'modeBarButtonsToAdd': [], 'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'resetScale2d', 'resetViewMapbox', 'zoom2d', 'pan2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'toggleHover', 'toggleSpikelines'], 'toImageButtonOptions': {'format': 'png', 'filename': 'Ingresos_Totales_por_Sector', 'height': 600, 'width': 900, 'scale': 2}})
    
    st.divider()
    
    # Cruzado: Sector vs Stage
    st.write("**Matriz: Sector vs Etapa del Pipeline**")
    
    # Create pivot table
    pivot_data = pd.crosstab(
        df_companies["Sector"],
        df_companies["Etapa"].str[:15],  # Shorten for display
        margins=True
    )
    
    fig_heatmap = px.imshow(
        pivot_data.iloc[:-1, :-1],
        labels=dict(x="Etapa Pipeline", y="Sector", color="Deals"),
        color_continuous_scale=["#F5F7FA", "#FF6B35"],
        title="Heatmap_Deals_Sector_Etapa",
        text_auto=True
    )
    fig_heatmap.update_layout(
        height=400,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="sans-serif", color="#1f2937")
    )
    st.plotly_chart(fig_heatmap, width='stretch', config={'displayModeBar': True, 'displaylogo': False, 'modeBarButtonsToAdd': [], 'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'resetScale2d', 'resetViewMapbox', 'zoom2d', 'pan2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'toggleHover', 'toggleSpikelines'], 'toImageButtonOptions': {'format': 'png', 'filename': 'Heatmap_Deals_Sector_Etapa', 'height': 600, 'width': 1200, 'scale': 2}})
    
    # Summary stats by sector
    st.write("**Resumen por Sector:**")
    
    sector_summary = df_companies.groupby("Sector").agg({
        "Empresa": "count",
        "Ventas": "sum",
        "EBITDA": "mean",
        "EBITDA_Pct": "mean"
    }).round(2)
    
    sector_summary.columns = ["Empresas", "Ventas Total", "EBITDA Promedio", "EBITDA %"]
    sector_summary["Ventas Total"] = "$" + (sector_summary["Ventas Total"] / 1e6).round(2).astype(str) + "M"
    sector_summary["EBITDA Promedio"] = "$" + (sector_summary["EBITDA Promedio"] / 1e3).round(0).astype(str) + "K"
    sector_summary["EBITDA %"] = sector_summary["EBITDA %"].round(1).astype(str) + "%"
    
    st.dataframe(sector_summary, width='stretch')

# =====================
# TAB 4: INFORMACIÓN POR EMPRESA
# =====================
with tab4:
    st.subheader("Datos Clave por Empresa")
    
    # Filter options
    col1, col2 = st.columns(2)
    
    with col1:
        sector_filter = st.multiselect(
            "Filtrar por Sector:",
            options=sectors,
            default=sectors
        )
    
    with col2:
        status_filter = st.multiselect(
            "Filtrar por Status:",
            options=["Activo", "Cerrado"],
            default=["Activo", "Cerrado"]
        )
    
    # Apply filters
    df_filtered = df_companies[
        (df_companies["Sector"].isin(sector_filter)) &
        (df_companies["Status"].isin(status_filter))
    ].copy()
    
    # Format for display
    df_display = df_filtered[["Empresa", "Sector", "Etapa", "Ventas", "EBITDA", "EBITDA_Pct", "Status"]].copy()
    df_display["Ventas"] = "$" + (df_display["Ventas"] / 1e6).round(2).astype(str) + "M"
    df_display["EBITDA"] = "$" + (df_display["EBITDA"] / 1e3).round(0).astype(str) + "K"
    df_display["EBITDA_Pct"] = df_display["EBITDA_Pct"].round(1).astype(str) + "%"
    df_display.columns = ["Empresa", "Sector", "Etapa", "Ventas", "EBITDA", "EBITDA %", "Status"]
    
    # Display table
    st.dataframe(df_display, width='stretch', hide_index=True)
    
    st.divider()
    
    # Analytics
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric(label="Empresas Mostradas", value=len(df_filtered))
    
    with col2:
        avg_sales = df_filtered["Ventas"].mean()
        st.metric(label="Ventas Promedio", value=f"${avg_sales/1e6:.2f}M")
    
    with col3:
        avg_ebitda_pct = df_filtered["EBITDA_Pct"].mean()
        st.metric(label="EBITDA % Promedio", value=f"{avg_ebitda_pct:.1f}%")
    
    # Sorted view
    st.write("**Empresas Ordenadas por Ventas:**")
    
    df_sorted = df_filtered.sort_values("Ventas", ascending=False)[
        ["Empresa", "Sector", "Etapa", "Ventas", "EBITDA_Pct"]
    ].copy()
    
    df_sorted["Ventas"] = "$" + (df_sorted["Ventas"] / 1e6).round(2).astype(str) + "M"
    df_sorted["EBITDA_Pct"] = df_sorted["EBITDA_Pct"].round(1).astype(str) + "%"
    df_sorted.columns = ["Empresa", "Sector", "Etapa", "Ventas", "EBITDA %"]
    
    st.dataframe(df_sorted, width='stretch', hide_index=True)

# =====================
# TAB 5: VELOCIDAD DEL PIPELINE
# =====================
with tab5:
    st.subheader("Velocidad del Pipeline - Cuellos de Botella")
    
    # Calculate average days per stage
    dias_por_etapa = df_companies.groupby("Etapa_Num")["Dias_Etapa"].mean().reset_index()
    dias_por_etapa["Etapa"] = dias_por_etapa["Etapa_Num"].map(
        {i: pipeline_stages[i] for i in range(len(pipeline_stages))}
    )
    dias_por_etapa = dias_por_etapa.sort_values("Etapa_Num")
    
    # Color code based on duration
    dias_por_etapa["Color"] = dias_por_etapa["Dias_Etapa"].apply(
        lambda x: "#d62728" if x > 80 else ("#ff7f0e" if x > 50 else "#2ca02c")
    )
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        fig_velocity = px.bar(
            dias_por_etapa,
            x="Etapa",
            y="Dias_Etapa",
            color="Dias_Etapa",
            color_continuous_scale=["#90EE90", "#FFB347", "#FF6B35"],
            title="Pipeline_Dias_Promedio_Etapa",
            labels={"Dias_Etapa": "Días", "Etapa": ""}
        )
        fig_velocity.update_xaxes(tickangle=-45)
        fig_velocity.update_layout(
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            font=dict(family="sans-serif", color="#1f2937"),
            showlegend=False
        )
        st.plotly_chart(fig_velocity, width='stretch', config={'displayModeBar': True, 'displaylogo': False, 'modeBarButtonsToAdd': [], 'modeBarButtonsToRemove': ['lasso2d', 'select2d', 'resetScale2d', 'resetViewMapbox', 'zoom2d', 'pan2d', 'zoomIn2d', 'zoomOut2d', 'autoScale2d', 'toggleHover', 'toggleSpikelines'], 'toImageButtonOptions': {'format': 'png', 'filename': 'Pipeline_Dias_Promedio_Etapa', 'height': 600, 'width': 1200, 'scale': 2}})
    
    with col1:
        st.write("**Análisis de Cuellos de Botella:**")
        
        # Identify bottlenecks
        slow_stages = dias_por_etapa[dias_por_etapa["Dias_Etapa"] > dias_por_etapa["Dias_Etapa"].quantile(0.75)]
        
        if len(slow_stages) > 0:
            for _, row in slow_stages.iterrows():
                st.warning(f"{row['Etapa']}: {row['Dias_Etapa']:.0f} días")
        else:
            st.info("Todas las etapas están dentro de tiempos normales")
    
    with col2:
        st.metric(label="Días Promedio Total", value=f"{dias_por_etapa['Dias_Etapa'].mean():.0f}")
        st.metric(label="Etapa Más Rápida", value=dias_por_etapa.loc[dias_por_etapa['Dias_Etapa'].idxmin(), 'Etapa'][:20])
        st.metric(label="Cuello de Botella", value=dias_por_etapa.loc[dias_por_etapa['Dias_Etapa'].idxmax(), 'Etapa'][:20])

# =====================
# TAB 6: REGISTRAR OPERACIÓN
# =====================
with tab6:
    st.subheader("Gestionar Operaciones")
    
    sub_tab1, sub_tab2 = st.tabs(["Registrar Nueva", "Actualizar Etapa"])
    
    with sub_tab1:
        with st.form("new_operation"):
            col1, col2 = st.columns(2)
            
            with col1:
                empresa = st.text_input("Nombre de Empresa")
                sector = st.selectbox("Sector", sectors)
                etapa = st.selectbox("Etapa Pipeline", pipeline_stages)
            
            with col2:
                ventas = st.number_input("Ventas Anuales ($)", min_value=0, step=100000)
                ebitda = st.number_input("EBITDA ($)", min_value=0, step=10000)
                ebitda_pct = st.number_input("EBITDA %", min_value=0.0, max_value=100.0, step=0.1)
            
            dias_etapa = st.slider("Días en Etapa", min_value=0, max_value=120, value=30)
            
            submitted = st.form_submit_button("Registrar Operación")
            
            if submitted and empresa:
                etapa_num = pipeline_stages.index(etapa)
                new_company = {
                    "Empresa": empresa,
                    "Sector": sector,
                    "Etapa": etapa,
                    "Etapa_Num": etapa_num,
                    "Ventas": ventas,
                    "EBITDA": ebitda,
                    "EBITDA_Pct": ebitda_pct,
                    "Status": "Activo" if etapa_num < 13 else "Cerrado",
                    "Descartado": False,
                    "Dias_Etapa": dias_etapa
                }
                
                st.session_state.new_companies.append(new_company)
                st.success(f"{empresa} registrada correctamente")
                st.rerun()
        
        if st.session_state.new_companies:
            st.divider()
            st.write("**Operaciones Registradas en esta Sesión:**")
            df_new_display = pd.DataFrame(st.session_state.new_companies)
            df_new_display = df_new_display[["Empresa", "Sector", "Etapa", "Ventas", "EBITDA", "EBITDA_Pct"]]
            df_new_display["Ventas"] = "$" + (df_new_display["Ventas"] / 1e6).round(2).astype(str) + "M"
            df_new_display["EBITDA"] = "$" + (df_new_display["EBITDA"] / 1e3).round(0).astype(str) + "K"
            df_new_display["EBITDA_Pct"] = df_new_display["EBITDA_Pct"].round(1).astype(str) + "%"
            st.dataframe(df_new_display, width='stretch', hide_index=True)
    
    with sub_tab2:
        st.write("Selecciona una operación para actualizar su etapa")
        
        all_companies = df_companies.copy()
        company_list = all_companies["Empresa"].tolist()
        
        if len(company_list) > 0:
            selected_company = st.selectbox("Empresa", company_list)
            
            company_data = all_companies[all_companies["Empresa"] == selected_company].iloc[0]
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.write(f"**Información Actual:**")
                st.write(f"Sector: {company_data['Sector']}")
                st.write(f"Etapa Actual: {company_data['Etapa']}")
                st.write(f"Ventas: ${company_data['Ventas']/1e6:.2f}M")
            
            with col2:
                new_etapa = st.selectbox("Nueva Etapa", pipeline_stages, 
                                        index=int(company_data['Etapa_Num']),
                                        key="update_etapa")
                st.info("Los días se reiniciarán automáticamente al cambiar de etapa")
                
                if st.button("Actualizar Etapa"):
                    new_etapa_num = pipeline_stages.index(new_etapa)
                    
                    # Update in session state if it's a new company
                    for i, comp in enumerate(st.session_state.new_companies):
                        if comp["Empresa"] == selected_company:
                            st.session_state.new_companies[i]["Etapa"] = new_etapa
                            st.session_state.new_companies[i]["Etapa_Num"] = new_etapa_num
                            st.session_state.new_companies[i]["Dias_Etapa"] = 0
                            st.session_state.new_companies[i]["Status"] = "Activo" if new_etapa_num < 13 else "Cerrado"
                            st.success(f"Etapa actualizada a {new_etapa} (días reiniciados a 0)")
                            st.rerun()
                            break
        else:
            st.info("No hay operaciones registradas aun")
