# NEXT STEP — Evolución a Sistema Autónomo IoT + Robótica

## 1. Objetivo

Transformar el runtime actual en un sistema autónomo capaz de:

- Detectar desviaciones térmicas en frigoríficos
- Evaluar riesgo sobre la mercadería
- Decidir acciones de reposición o movimiento
- Ejecutar acciones mediante robots

---

## 2. Arquitectura Target

### Capas

1. **Ingesta IoT**
   - Sensores de temperatura (tiempo real)
   - Posición espacial (cubicación interna)
   - Estado de puertas / flujo térmico

2. **Data Layer**
   - Data Lake (histórico)
   - Stream processing (Kafka / Redpanda)

3. **Modelo Predictivo**
   - State-JEPA (predicción de estados térmicos)
   - Detección de anomalías

4. **Agent Runtime (existente)**
   - Evaluación de stock + riesgo
   - Decisión determinística

5. **Execution Layer**
   - Robots móviles (AMR)
   - Sistema WMS

---

## 3. Extensión del Worker

### Nuevos inputs

```json
{
  "sku_id": "SKU-123",
  "location_id": "CAMARA-1",
  "on_hand": 120,
  "safety_stock": 100,
  "temp_current": -18.5,
  "temp_variance": 3.2,
  "risk_score": 0.78
}
```

### Nueva lógica

- Si `risk_score > threshold`:
  - Priorizar movimiento de mercadería
- Si `temp_variance` alta:
  - Reubicar productos sensibles

---

## 4. Policy Engine v2

Agregar variables:

- riesgo térmico
- sensibilidad del producto
- tiempo de exposición

Ejemplo:

```python
if risk_score > 0.7:
    action = "MOVE"
elif on_hand < safety_stock:
    action = "REPLENISH"
else:
    action = "HOLD"
```

---

## 5. Integración con Robótica

### Tipos de robots

- AMR (Autonomous Mobile Robots)
- Brazo robótico para picking

### Flujo

1. Agent decide acción
2. Se genera orden:
   ```json
   {
     "action": "MOVE",
     "from": "ZONA_A",
     "to": "ZONA_B",
     "priority": "HIGH"
   }
   ```
3. Robot ejecuta
4. Feedback al sistema

---

## 6. Loop de Aprendizaje

Agregar:

- registro de decisiones
- resultado real (éxito / fallo)
- ajuste de thresholds

---

## 7. Roadmap

### Fase 1
- Integrar sensores IoT
- Extender schema del agente

### Fase 2
- Implementar modelo predictivo (State-JEPA)
- Generar risk_score

### Fase 3
- Integrar robots (API / MQTT)

### Fase 4
- Closed-loop system (auto aprendizaje)

---

## 8. Riesgos

- Latencia en decisiones
- Fallas de sensores
- Coordinación multi-robot

---

## 9. Resultado esperado

Sistema autónomo capaz de:

- Prevenir pérdidas
- Optimizar logística interna
- Operar sin intervención humana

