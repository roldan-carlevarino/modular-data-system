# 📊 Search Fund Dashboard

Dashboard visual para analizar el pipeline de adquisiciones, efectividad de outreach, mix sectorial e información por empresa.

## 🚀 Características

✅ **4 Vistas principales:**
1. **Funnel de Adquisición** - 14 etapas del pipeline, tasa de conversión, deals descartados
2. **Efectividad del Outreach** - Datos de campañas de email (apertura, respuesta)
3. **Mix Sectorial** - Distribución por sector, ingresos, matriz sector/etapa
4. **Información por Empresa** - Datos clave: ventas, EBITDA, filtros avanzados

✅ **Datos ficticios** - Generados automáticamente para demostración
✅ **Totalmente visual** - Gráficos interactivos con Plotly
✅ **Fácil de usar** - Filtros y métricas clave al vistazo

## 📋 Requisitos

- Python 3.8+
- pip

## 💻 Instalación y Ejecución

### 1. Instalar dependencias

```bash
cd dashboard_search_fund
pip install -r requirements.txt
```

### 2. Ejecutar el dashboard

```bash
streamlit run app.py
```

El dashboard se abrirá automáticamente en tu navegador en `http://localhost:8501`

## 📊 Vistas Disponibles

### 🎯 Tab 1: Funnel de Adquisición
- Gráfico funnel con deals activos por etapa
- Tasas de conversión entre etapas
- Deals descartados por etapa
- Resumen ejecutivo

### 📧 Tab 2: Efectividad Outreach
- Métricas de campañas de email
- Tasa de apertura y respuesta
- Scatter plot: Correos vs Tasa de Apertura
- Ranking de mejores campañas

### 🏭 Tab 3: Mix Sectorial
- Pie chart de distribución por sector (Logística, Salud, Manufactura, Educación)
- Ingresos totales por sector
- Matriz heatmap: Sector vs Etapa del Pipeline
- Resumen estadístico por sector

### 📋 Tab 4: Información por Empresa
- Tabla filtrable por sector, etapa y status
- Datos clave: Ventas, EBITDA $, EBITDA %
- Ordenamiento por ventas
- Análisis rápido

## 🎨 Diseño

- **Limpio y minimalista** - Fácil de entender a primera vista
- **Colores intuitivos** - Código cromático para cada métrica
- **Responsive** - Funciona en desktop y tablets
- **Métricas clave en el header** - KPIs principales visibles

## 📝 Datos Generados

El dashboard genera automáticamente:
- 12 empresas ficticias distribuidas en 4 sectores
- Pipeline con 14 etapas
- 15 campañas de outreach con métricas variadas
- Datos de ventas, EBITDA y EBITDA %
- Status de deals (activos/cerrados)

## 🔄 Cómo modificar datos

Para cambiar los datos ficticios, edita la función `generate_fake_data()` en `app.py`:

```python
def generate_fake_data():
    # Modifica aquí los nombres de empresas, sectores, etc.
```

## 📞 Notas

- Dashboard completamente en español
- Datos son 100% ficticios para demostración
- Actualiza la fecha/hora automáticamente
- Todos los gráficos son interactivos (zoom, hover, etc.)

---

**Listo para presentar el lunes 15 de junio a las 13:00** 🎯
