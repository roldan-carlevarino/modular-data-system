# Conectar Datos Reales

Este documento explica cómo integrar datos reales de HubSpot, una base de datos o un API.

## 📊 Opción 1: Integración con HubSpot

### Paso 1: Obtener API Key de HubSpot

1. Ir a https://app.hubspot.com/
2. Ir a Settings → Integrations → Private apps
3. Crear nueva app con permisos: `crm.objects.contacts.read`, `crm.objects.deals.read`
4. Copiar el token

### Paso 2: Modificar el código

Reemplaza la función `generate_fake_data()` con:

```python
import requests

@st.cache_data
def get_hubspot_data():
    HUBSPOT_API_KEY = "YOUR_API_KEY_HERE"
    
    # Obtener deals
    url = "https://api.hubapi.com/crm/v3/objects/deals"
    headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}
    
    params = {
        "properties": ["dealname", "amount", "stage", "closedate"],
        "limit": 100
    }
    
    response = requests.get(url, headers=headers, params=params)
    deals = response.json()["results"]
    
    # Procesar datos...
    return df_companies
```

---

## 📚 Opción 2: Integración con Base de Datos (PostgreSQL)

```python
import psycopg2
import pandas as pd

@st.cache_data
def get_database_data():
    conn = psycopg2.connect(
        host="your_host",
        database="your_db",
        user="your_user",
        password="your_password"
    )
    
    # Obtener empresas
    df_companies = pd.read_sql("""
        SELECT 
            id, nombre, sector, etapa_pipeline, 
            ventas_anuales, ebitda, ebitda_pct
        FROM empresas
        WHERE estado = 'activo'
    """, conn)
    
    conn.close()
    return df_companies
```

---

## 🔌 Opción 3: Integración con Google Sheets

```python
from google.oauth2 import service_account
import gspread
import pandas as pd

@st.cache_data
def get_sheets_data():
    credentials = service_account.Credentials.from_service_account_file(
        "service_account.json"
    )
    
    gc = gspread.authorize(credentials)
    
    # Abrir sheet
    sheet = gc.open("Search Fund Data").worksheet("Companies")
    data = sheet.get_all_records()
    
    df_companies = pd.DataFrame(data)
    return df_companies
```

---

## 🔄 Opción 4: CSV o Excel

Más simple para empezar:

```python
@st.cache_data
def get_csv_data():
    df_companies = pd.read_csv("companies.csv")
    df_outreach = pd.read_csv("outreach_campaigns.csv")
    return df_companies, df_outreach
```

---

## 💾 Estructura de Datos Esperada

### Tabla: Empresas

```
| id  | Empresa         | Sector      | Etapa                    | Ventas   | EBITDA   | EBITDA_Pct |
|-----|-----------------|-------------|--------------------------|----------|----------|------------|
| 1   | LogisticaPro    | Logística   | Análisis empresa         | 2500000  | 300000   | 12         |
| 2   | HealthFlow      | Salud       | NDA firmado              | 1800000  | 250000   | 14         |
```

### Tabla: Outreach

```
| id  | Campaña      | Correos_Enviados | Tasa_Apertura | Tasa_Respuesta |
|-----|--------------|------------------|---------------|----------------|
| 1   | Campaña A    | 150              | 32.5          | 8.2            |
| 2   | Campaña B    | 200              | 28.1          | 10.5           |
```

---

## 🛠️ Ejemplos Completos

### Ejemplo con PostgreSQL + HubSpot

```python
import streamlit as st
import pandas as pd
import psycopg2
import requests

HUBSPOT_API_KEY = st.secrets["hubspot_api_key"]
DB_CONNECTION = st.secrets["database_url"]

@st.cache_data
def load_all_data():
    # Datos locales
    conn = psycopg2.connect(DB_CONNECTION)
    df_companies = pd.read_sql("SELECT * FROM empresas", conn)
    conn.close()
    
    # Datos de HubSpot
    headers = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}
    response = requests.get(
        "https://api.hubapi.com/crm/v3/objects/deals",
        headers=headers
    )
    deals = response.json()["results"]
    
    return df_companies, deals

df_companies, hubspot_deals = load_all_data()
```

---

## 🔐 Guardar Credenciales de Forma Segura

En Streamlit, usa el archivo `.streamlit/secrets.toml`:

```toml
# .streamlit/secrets.toml
hubspot_api_key = "your-key-here"
database_url = "postgresql://user:password@host/db"
```

Nunca pongas esto en GitHub. Streamlit Cloud lo maneja automáticamente.

---

## 🚀 Siguiente Paso

Una vez tengas datos reales, solo necesitas:

1. Cambiar `generate_fake_data()` por tu función de datos reales
2. Ejecutar `streamlit run app.py`
3. El dashboard se actualiza automáticamente con datos reales

¡Listo! 🎉
