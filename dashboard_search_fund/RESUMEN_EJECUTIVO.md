# 📊 Search Fund Dashboard - Resumen Ejecutivo

## ¿Qué es?

Dashboard visual interactivo diseñado para que el equipo complete entienda de un vistazo cómo van las operaciones de adquisición, día a día.

## ✨ 4 Vistas Principales

### 1️⃣ **Funnel de Adquisición** 
- Pipeline visual con 14 etapas
- Deals activos por etapa
- Tasas de conversión entre fases
- Identificación de etapas donde se pierden oportunidades
- Análisis de deals descartados por etapa

**Ejemplo:** "Vemos que pasamos de 12 deals a 2 en la etapa de LOI firmado. Tenemos un 16% de conversión."

---

### 2️⃣ **Efectividad de Outreach**
- Integración directa con HubSpot
- Tasa de apertura por campaña
- Tasa de respuesta por campaña
- Correlación: Correos enviados vs Tasa de apertura
- Ranking de mejores campañas

**Ejemplo:** "Campaña A tiene 35% de apertura, pero la respuesta es solo 7%. Tenemos que mejorar el CTA."

---

### 3️⃣ **Mix Sectorial**
- Distribución de empresas por sector (Logística, Salud, Manufactura, Educación)
- Ingresos potenciales por sector
- **Matriz interactiva:** Sector vs Etapa del Pipeline
- Análisis de concentración de riesgo

**Ejemplo:** "70% de nuestras oportunidades de salud están en due diligence. Tenemos buen flujo en ese sector."

---

### 4️⃣ **Información por Empresa**
- Tabla completa de todas las oportunidades
- Filtros por: Sector, Etapa, Status
- Datos clave: Ventas, EBITDA $, EBITDA %
- Ordenamiento por tamaño de oportunidad

**Ejemplo:** "LogisticaPro es nuestra mejor oportunidad: $3.2M de ingresos, 18% EBITDA, en etapa de due diligence."

---

## 🎯 Beneficios

✅ **Claridad**: Entiende el pipeline completo en <1 minuto  
✅ **Toma de decisiones**: Identifica dónde enfocarse  
✅ **Tracking**: Monitorea conversiones y descartaciones  
✅ **Métricas**: KPIs clave siempre visibles  
✅ **Interactivo**: Explora datos con filtros y gráficos dinámicos  

---

## 🛠️ Stack Tecnológico

- **Framework:** Streamlit (Python)
- **Visualización:** Plotly (gráficos interactivos)
- **Datos:** Integrable con HubSpot, PostgreSQL, Google Sheets, CSV
- **Hosting:** Puede desplegarse en Streamlit Cloud (gratis)

---

## 📈 Casos de Uso

| Caso | Pregunta | Respuesta |
|------|----------|-----------|
| **Weekly Check-in** | ¿Cómo va el pipeline esta semana? | Ver funnel y deals activos |
| **Decisión de inversión** | ¿Cuál es nuestra mejor oportunidad? | Tab 4: Información por empresa |
| **Optimización de outreach** | ¿Qué campañas funcionan? | Tab 2: Efectividad outreach |
| **Diversificación** | ¿Estamos concentrados en un sector? | Tab 3: Mix sectorial |

---

## 🚀 Cómo Usar

1. **Instalar dependencias:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Ejecutar dashboard:**
   ```bash
   streamlit run app.py
   ```

3. **Abrir navegador:**
   Se abre automáticamente en `http://localhost:8501`

---

## 🔄 Actualización de Datos

### Datos Ficticios (Demostración)
- 12 empresas de ejemplo
- 4 sectores
- 15 campañas

### Datos Reales (Producción)
Conectar directamente a:
- **HubSpot CRM** - Deals y contactos
- **Base de datos propia** - Empresas y análisis
- **Google Sheets** - Para colaboración en equipo

Ver `INTEGRACION_DATOS_REALES.md` para detalles técnicos.

---

## 💡 Características Avanzadas

- ✅ Filtros multiselector
- ✅ Gráficos interactivos (zoom, hover, descarga)
- ✅ Métricas agregadas automáticas
- ✅ Cálculos de conversión en tiempo real
- ✅ Responsive (funciona en móvil)

---

## 📊 Métricas Principales que Muestra

| Métrica | Ubicación | Uso |
|---------|-----------|-----|
| Deals Activos | Header | Ver volumen del pipeline |
| Tasa Descarte | Tab 1 | Identificar fugas |
| Tasa Apertura | Tab 2 | Mejorar campañas |
| EBITDA % Promedio | Header | Calidad del pipeline |
| Mix Sectorial | Tab 3 | Diversificar riesgo |

---

## 🎨 Diseño

- **Colores:** Azul (primario), Verde (outreach), Rojo (descartados)
- **Layout:** 4 pestañas independientes
- **Responsive:** Funciona en desktop y tablets
- **Velocidad:** Carga instantánea (caché inteligente)

---

## 🔐 Seguridad

- Datos sensibles guardados en `.streamlit/secrets.toml` (no en código)
- Puede desplegarse con autenticación en Streamlit Cloud
- Compatible con Single Sign-On (SSO)

---

## 📞 Soporte y Mejoras

Posibles mejoras futuras:
- Dashboard móvil dedicado
- Alertas automáticas (ej: deal sin actividad 30 días)
- Análisis predictivo (qué deals tienen mayor probabilidad de cerrar)
- Integración con Slack para notificaciones
- Comparativa vs histórico (mes anterior, trimestre anterior)

---

**Listo para presentar y tomar decisiones basadas en datos** 🎯
