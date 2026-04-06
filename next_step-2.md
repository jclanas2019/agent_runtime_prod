# next_step.md — Próximos pasos algorítmicos del runtime

## Corrección de foco

El siguiente documento no propone robótica ni arquitectura IoT.  
Se concentra en los **próximos pasos algorítmicos reales** del sistema que hoy existe:

- `reposition_worker.py`
- tools mock: `get_stock()` y `get_sales_velocity()`
- policy determinística: `compute_policy()`
- validación estructurada con `Pydantic`
- ejecución vía runtime externo y salida en `result.json` + `metrics.json`

---

## 1. Diagnóstico algorítmico del estado actual

El algoritmo actual resuelve una reposición muy simple:

### Inputs efectivos
- `on_hand`
- `safety_stock`
- `daily_units`

### Variables derivadas
- `days_cover = on_hand / daily_units`
- `needs_reposition = on_hand < safety_stock`

### Regla de reposición
```python
target = safety_stock + daily_units * 7
recommended_units = max(0, round(target - on_hand)) if needs_reposition else 0
```

### Características actuales
1. La decisión depende de **3 variables**.
2. `daily_units` se trata como un valor fijo, no probabilístico.
3. El horizonte está hardcodeado a **7 días**.
4. La condición de disparo usa sólo `on_hand < safety_stock`.
5. No existe:
   - tendencia
   - estacionalidad
   - variabilidad
   - lead time
   - costo
   - criticidad
   - nivel de servicio
   - incertidumbre

En otras palabras: hoy el sistema implementa una **heurística determinística básica**, útil como primera versión, pero todavía no una política de inventario robusta.

---

## 2. Objetivo del siguiente ciclo algorítmico

Evolucionar desde una regla fija de reposición a un **motor de decisión de inventario** con estas propiedades:

1. demanda esperada
2. incertidumbre explícita
3. lead time
4. nivel de servicio
5. priorización por riesgo
6. explicación estructurada de la decisión
7. política separada por estrategia

No se debe empezar por más agentes.  
Se debe empezar por **mejorar la función de decisión**.

---

## 3. Próximo paso algorítmico inmediato: sacar las fórmulas del prompt y dejar al LLM fuera del cálculo

Aunque el código ya recalcula la policy de forma determinística, el prompt todavía le pide al LLM que calcule:

- `days_cover`
- `needs_reposition`
- `recommended_units`

Eso introduce ruido innecesario.

## Cambio recomendado
Reducir el rol del LLM a sólo recopilar datos crudos:

```json
{
  "sku": "SKU-001",
  "on_hand": 8,
  "safety_stock": 15,
  "daily_units": 6
}
```

Y mover **todo lo derivado** a la policy engine:

```python
policy = compute_policy(
    on_hand=float(raw_data["on_hand"]),
    safety_stock=float(raw_data["safety_stock"]),
    daily_units=float(raw_data["daily_units"]),
)
```

## Ventaja
- menos error
- menos tokens
- menos complejidad
- más auditabilidad
- menos dependencia del formato del LLM

---

## 4. Segundo paso algorítmico: reemplazar `daily_units` fijo por demanda estimada

`daily_units` hoy es una constante mock.  
Ese es el cuello de botella principal.

La reposición no debería basarse en una cifra fija sino en una **estimación de demanda** calculada desde histórico.

## 4.1. Propuesta mínima
Crear una función determinística:

```python
def estimate_daily_demand(history: list[float]) -> dict:
    ...
```

### Salida sugerida
```json
{
  "mean_daily_demand": 6.2,
  "std_daily_demand": 1.8,
  "p50_daily_demand": 6.0,
  "p90_daily_demand": 8.5,
  "trend": "stable"
}
```

## 4.2. Primer algoritmo recomendado
Empezar simple:

- media móvil de 7 días
- media móvil de 28 días
- comparación corta vs larga para detectar tendencia

Ejemplo:
```python
short_avg = mean(last_7_days)
long_avg = mean(last_28_days)
trend_ratio = short_avg / long_avg
```

### Interpretación
- `trend_ratio > 1.10` → demanda en alza
- `trend_ratio < 0.90` → demanda en baja
- resto → estable

## Ventaja
Se mejora el insumo central del policy engine sin necesidad de modelos complejos.

---

## 5. Tercer paso algorítmico: incorporar lead time

La fórmula actual usa 7 días fijos.  
Eso no representa una operación real.

La política debe calcular reposición según tiempo de abastecimiento:

```python
reorder_horizon = lead_time_days + review_period_days
```

## Nueva fórmula base
```python
target_stock = safety_stock + expected_daily_demand * reorder_horizon
recommended_units = max(0, ceil(target_stock - on_hand))
```

## Nuevos inputs requeridos
- `lead_time_days`
- `review_period_days`

## Ejemplo
```python
lead_time_days = 3
review_period_days = 4
reorder_horizon = 7
```

## Resultado
La política deja de estar rígida y pasa a ser operacional.

---

## 6. Cuarto paso algorítmico: redefinir `safety_stock`

Hoy `safety_stock` entra como dato fijo.  
Eso impide adaptación.

El próximo paso correcto es calcularlo desde demanda y nivel de servicio.

## Fórmula estándar
```python
safety_stock = z * sigma_demand * sqrt(lead_time_days)
```

Donde:
- `z` depende del nivel de servicio deseado
- `sigma_demand` es desviación estándar de demanda diaria

## Tabla inicial útil
- 90% → z ≈ 1.28
- 95% → z ≈ 1.65
- 97.5% → z ≈ 1.96
- 99% → z ≈ 2.33

## Cambio conceptual importante
`safety_stock` deja de ser un número manual y se transforma en una variable calculada.

## Ventaja
El sistema empieza a decidir por riesgo y no sólo por umbral fijo.

---

## 7. Quinto paso algorítmico: cambiar la condición de disparo

La condición actual:

```python
needs_reposition = on_hand < safety_stock
```

es demasiado pobre.

La condición correcta debiera ser una de estas dos formas.

## Opción A — reorder point
```python
reorder_point = expected_demand_during_lead_time + safety_stock
needs_reposition = inventory_position <= reorder_point
```

## Opción B — days cover threshold
```python
needs_reposition = days_cover <= min_days_cover
```

## Recomendación
Usar `inventory_position`, no sólo `on_hand`.

## Nueva variable
```python
inventory_position = on_hand + on_order - allocated
```

## Ventaja
La política deja de sobrerreaccionar cuando ya hay compras en curso.

---

## 8. Sexto paso algorítmico: introducir clasificación ABC/XYZ

No todos los SKU deben usar la misma policy.

## 8.1. ABC
Clasificación por impacto económico:
- A = alto impacto
- B = medio
- C = bajo

## 8.2. XYZ
Clasificación por variabilidad de demanda:
- X = estable
- Y = moderada
- Z = volátil

## Política resultante
Un SKU `AX` debe tener:
- mayor nivel de servicio
- menor tolerancia a quiebre
- revisión más frecuente

Un SKU `CZ` puede tolerar política más laxa.

## Implementación mínima
Agregar una función:

```python
def classify_sku(revenue_share: float, cv_demand: float) -> dict:
    ...
```

## Ventaja
La policy pasa de universal a segmentada.

---

## 9. Séptimo paso algorítmico: introducir score de prioridad

Hoy el sistema devuelve una sola acción binaria:
- reponer
- no reponer

Eso es insuficiente cuando hay muchos SKU.

Debe devolver además un **priority_score** para ordenar ejecución.

## Fórmula sugerida
```python
priority_score = (
    w1 * stockout_risk +
    w2 * demand_volatility +
    w3 * margin_weight +
    w4 * criticality_weight
)
```

## Ejemplo de output
```json
{
  "needs_reposition": true,
  "recommended_units": 22,
  "priority_score": 0.87,
  "priority_band": "HIGH"
}
```

## Ventaja
Permite planificación operativa y cola de ejecución.

---

## 10. Octavo paso algorítmico: explicar la decisión con features internas, no con texto libre

No conviene volver a narrativa abierta del LLM.  
Conviene emitir una explicación estructurada.

## Ejemplo
```json
{
  "explanation": {
    "trigger": "inventory_position_below_reorder_point",
    "reorder_point": 31.5,
    "inventory_position": 8,
    "expected_daily_demand": 6.2,
    "lead_time_days": 3,
    "service_level": 0.95
  }
}
```

## Ventaja
- trazabilidad
- debugging
- reporting
- integración con dashboards

---

## 11. Noveno paso algorítmico: fallback y degradación determinística

Hoy si falla el LLM, el proceso cae a error.  
En producción eso es débil.

## Estrategia recomendada
Si el LLM falla:
1. ir directo a fuentes determinísticas
2. usar defaults controlados
3. marcar `decision_mode = "degraded"`

## Ejemplo
```json
{
  "decision_mode": "degraded",
  "data_quality": "partial",
  "needs_reposition": true
}
```

## Ventaja
El sistema no se detiene por un problema de formato o timeout del modelo.

---

## 12. Décimo paso algorítmico: aprender de error real

Una policy productiva debe cerrarse con feedback.

## Métricas que faltan
- stockout real posterior
- sobrestock generado
- fill rate
- forecast error
- bias
- MAPE / WAPE
- frecuencia de reposición innecesaria

## Loop recomendado
```python
decision -> execution -> realized demand -> forecast error -> policy tuning
```

## Primer ajuste útil
Comparar:
- `recommended_units`
- consumo real siguiente horizonte

Y recalibrar:
- `service_level`
- `review_period_days`
- pesos del `priority_score`

---

## 13. Refactor algorítmico sugerido del código

## 13.1. Separar módulos
Estructura recomendada:

```text
workers/
  reposition_worker.py

domain/
  demand.py
  inventory_policy.py
  classification.py
  priority.py
  schemas.py
```

## 13.2. Funciones concretas
```python
def estimate_demand(history: list[float]) -> DemandEstimate: ...
def compute_safety_stock(std_demand: float, lead_time_days: float, z: float) -> float: ...
def compute_reorder_point(expected_daily_demand: float, lead_time_days: float, safety_stock: float) -> float: ...
def compute_inventory_position(on_hand: float, on_order: float, allocated: float) -> float: ...
def compute_recommendation(...) -> Recommendation: ...
def compute_priority(...) -> PriorityScore: ...
```

## Ventaja
El worker deja de ser un archivo monolítico y pasa a ser una composición de políticas.

---

## 14. Secuencia correcta de implementación

## Fase 1 — limpieza inmediata
1. Quitar cálculo derivado del prompt
2. Pedir al LLM sólo datos crudos
3. Mantener `compute_policy()` como única fuente matemática

## Fase 2 — demanda
4. Crear `estimate_demand()` con medias móviles
5. Reemplazar `daily_units` fijo por `expected_daily_demand`

## Fase 3 — inventario real
6. Agregar `lead_time_days`
7. Agregar `review_period_days`
8. Calcular `reorder_point`
9. Cambiar trigger a `inventory_position`

## Fase 4 — riesgo
10. Calcular `safety_stock` desde nivel de servicio
11. Agregar variabilidad de demanda
12. Agregar clasificación ABC/XYZ

## Fase 5 — priorización
13. Calcular `priority_score`
14. Agregar bandas HIGH / MEDIUM / LOW

## Fase 6 — aprendizaje
15. Registrar demanda realizada
16. Medir error
17. recalibrar parámetros

---

## 15. Qué NO haría todavía

No haría aún:

- multiagentes
- memoria conversacional
- planner complejo
- RAG vectorial
- optimización por reinforcement learning
- robotización
- arquitectura distribuida extra

Razón: el cuello de botella no está ahí.  
El cuello de botella está en la **política algorítmica de reposición**.

---

## 16. Recomendación final

El próximo salto de calidad de este sistema no es “más IA”.  
Es esto:

1. estimar mejor la demanda
2. modelar incertidumbre
3. incorporar lead time
4. calcular reorder point real
5. priorizar por riesgo
6. cerrar el loop con feedback

Eso convierte el runtime actual en un verdadero **motor de decisión de inventario**.

---

## 17. Versión objetivo de output

La siguiente versión razonable del schema debiera parecerse a esto:

```json
{
  "sku": "SKU-001",
  "on_hand": 8,
  "on_order": 10,
  "allocated": 4,
  "inventory_position": 14,
  "expected_daily_demand": 6.2,
  "demand_std": 1.8,
  "lead_time_days": 3,
  "review_period_days": 4,
  "service_level": 0.95,
  "safety_stock": 5.1,
  "reorder_point": 23.7,
  "days_cover": 1.3,
  "needs_reposition": true,
  "recommended_units": 18,
  "priority_score": 0.87,
  "priority_band": "HIGH",
  "decision_mode": "deterministic",
  "explanation": {
    "trigger": "inventory_position_below_reorder_point"
  }
}
```

Ese es el norte correcto.
