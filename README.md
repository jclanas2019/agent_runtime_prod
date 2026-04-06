# Agent Runtime — Nivel Producción

## Arquitectura

```
POST /spawn  →  Rust runtime  →  python3 reposition_worker.py
                                          │
                                    tools (stock + velocity)
                                          │
                                    LLM (JSON forzado)
                                          │
                                    policy engine (determinístico)
                                          │
                                    Pydantic validation
                                          │
                               result.json + metrics.json
```

## Qué cambió vs versión anterior

| Antes | Ahora |
|-------|-------|
| LLM devuelve texto libre | LLM devuelve JSON estructurado |
| `analysis` = string narrativo | `decision` = objeto validado con Pydantic |
| Sin métricas | `metrics.json` por instancia |
| Sin policy engine | `compute_policy()` es fuente de verdad para fórmulas |
| Un solo endpoint | `/instances/:id/result` + `/decisions` agregado |

## Flujo completo

```bash
# 1. Lanzar runtime Rust
cd core/runtime && cargo run

# 2. Spawning un agente
curl -X POST http://localhost:8080/spawn \
  -H 'Content-Type: application/json' \
  -d '{"agent":"reposition","input":{"sku":"SKU-001"}}'
# → {"ok":true,"instance_id":"<uuid>","agent":"reposition","workspace":"..."}

# 3. Polling (async)
curl http://localhost:8080/instances/<uuid>
# → {"status":"pending"|"ok"|"error", "decision": {...}, ...}

# 4. Decisión limpia (para integración con backend)
curl http://localhost:8080/instances/<uuid>/result
# → {"sku":"SKU-001","on_hand":8,"safety_stock":15,"daily_units":6,
#    "days_cover":1.3,"needs_reposition":true,"recommended_units":22}

# 5. Vista agregada de todas las decisiones
curl http://localhost:8080/decisions
```

## Output estructurado (`decision`)

```json
{
  "sku": "SKU-001",
  "on_hand": 8,
  "safety_stock": 15,
  "daily_units": 6,
  "days_cover": 1.3,
  "needs_reposition": true,
  "recommended_units": 22
}
```

## Policy engine

`recommended_units` **no** lo calcula el LLM. Lo calcula `compute_policy()`:

```python
target            = safety_stock + daily_units * 7
recommended_units = max(0, round(target - on_hand))  # solo si needs_reposition
```

El LLM recopila los datos crudos (`on_hand`, `safety_stock`, `daily_units`).
Las métricas derivadas siempre vienen del motor determinístico.

## Variables de entorno

```
OPENAI_API_KEY=sk-...
WORKSPACE=/path/to/instance/dir   # lo pone el runtime automáticamente
AGENT_NAME=reposition             # lo pone el runtime automáticamente
```
