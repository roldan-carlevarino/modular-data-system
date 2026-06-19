# Guía Presentación - Search Fund Dashboard

## ⏱️ Estructura de la Presentación (15 minutos)

### 1. Introducción (1 min)
"Este dashboard fue diseñado para que cualquier miembro del equipo entienda el estado del pipeline de adquisiciones de un vistazo."

### 2. Demostración Vistas (12 min)

#### Vista 1: Funnel de Adquisición (3 min)
- Mostrar el gráfico funnel: "Aquí vemos los 14 stages del pipeline"
- "Cada etapa muestra cuántos deals activos tenemos en ese punto"
- "Las conversiones nos muestran en qué etapas perdemos más oportunidades"
- "También tracking de deals descartados por etapa"

**KPIs clave:**
- 10 deals activos de 12 totales
- Tasa descarte: X%

#### Vista 2: Efectividad de Outreach (3 min)
- "Aquí integramos datos de HubSpot"
- Scatter plot: "Correos enviados vs tasa de apertura"
- "Podemos ver qué campañas funcionan mejor"
- Top 10 campañas: "Cuáles son nuestras mejores estrategias"

**KPIs clave:**
- Tasa apertura promedio: ~28%
- Tasa respuesta promedio: ~12%

#### Vista 3: Mix Sectorial (2 min)
- Pie chart: "Distribución actual de empresas por sector"
- "Logística, Salud, Manufactura y Educación"
- Matriz cruzada: "¿Dónde están concentrados nuestros deals en cada sector?"
- Ingresos totales: "Cuál sector tiene mayor potencial"

#### Vista 4: Información por Empresa (2 min)
- "Detalle completo de cada oportunidad"
- Filtros: "Podemos buscar por sector, etapa o status"
- Métricas: "Ventas, EBITDA $ y EBITDA %"
- Ordenamiento: "Podemos priorizar por tamaño"

### 3. Conclusión (2 min)
- "El dashboard actualiza automáticamente"
- "Totalmente visual y fácil de usar"
- "Listo para tomar decisiones día a día"

---

## 🎯 Puntos Clave a Remarcar

✅ **Visual:** Cualquiera entiende en menos de 1 minuto  
✅ **Interactivo:** Los gráficos se pueden explorar (zoom, hover)  
✅ **Accesible:** Funciona en cualquier navegador  
✅ **Escalable:** Fácil de conectar a datos reales  

---

## 🔧 Instrucciones para Ejecutar

### Antes de la presentación:

1. **Abrir PowerShell/Terminal** en la carpeta del proyecto
2. **Ejecutar:**
   ```
   cd dashboard_search_fund
   pip install -r requirements.txt
   streamlit run app.py
   ```
3. **El navegador abrirá automáticamente** en `http://localhost:8501`

### Durante la presentación:
- Dejar que los filtros funcionen
- Hacer hover en los gráficos para ver detalles
- Navegar entre las 4 pestañas

---

## 📱 Datos de Demostración

Todos los datos son ficticios:
- 12 empresas de ejemplo (LogisticaPro, TransHealth, etc.)
- 4 sectores: Logística, Salud, Manufactura, Educación
- 14 etapas del pipeline
- 15 campañas de outreach
- Ventas, EBITDA y percentajes generados aleatoriamente

---

## 💡 Posibles Preguntas y Respuestas

**P: ¿De dónde vienen los datos?**  
R: Actualmente son ficticios para la demostración. Cuando tengas acceso a los datos reales de HubSpot y tu base de datos de empresas, integramos directamente.

**P: ¿Se puede modificar el dashboard?**  
R: Claro, el código es simple de personalizar. Podemos agregar más métricas, cambiar colores, filtros, etc.

**P: ¿Qué tecnología usa?**  
R: Streamlit con Python. Es muy rápido de iterar y muy visual.

**P: ¿Se puede mostrar en tiempo real?**  
R: Sí, podemos configurar una conexión directa a HubSpot y tu base de datos.

---

**Buena suerte el lunes! 🚀**
