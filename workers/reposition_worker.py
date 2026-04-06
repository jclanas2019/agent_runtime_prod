"""
reposition_worker.py  —  Nivel producción
=========================================
Cambios vs versión anterior:
  ✔ Output estructurado JSON estricto (no texto libre)
  ✔ Validación Pydantic del schema de salida
  ✔ Policy engine: recommended_units calculado determinísticamente
  ✔ Métricas guardadas en metrics.json
  ✔ Error handling con fallback estructurado
  ✔ Retry con strip de markdown si el LLM escapa el JSON
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, ValidationError

load_dotenv()

# ── Workspace & config ────────────────────────────────────────────────────────
workspace   = Path(os.environ["WORKSPACE"])
agent_name  = os.environ.get("AGENT_NAME", "reposition")

task    = json.loads((workspace / "task.json").read_text(encoding="utf-8"))
payload = task.get("input", {})
sku     = payload.get("sku", "SKU-001")

# ── Schema de salida estructurada ─────────────────────────────────────────────
class RepositionDecision(BaseModel):
    sku:                str
    on_hand:            float   = Field(..., ge=0)
    safety_stock:       float   = Field(..., ge=0)
    daily_units:        float   = Field(..., gt=0)
    days_cover:         float   = Field(..., ge=0)
    needs_reposition:   bool
    recommended_units:  int     = Field(..., ge=0)


# ── Policy engine (fuente de verdad, no el LLM) ───────────────────────────────
def compute_policy(on_hand: float, safety_stock: float, daily_units: float) -> dict:
    """
    Calcula métricas de reposición de forma determinística.
    El LLM recopila datos; la policy decide la acción.
    """
    days_cover        = round(on_hand / daily_units, 1) if daily_units > 0 else 0.0
    needs_reposition  = on_hand < safety_stock
    # Target: cubrir 7 días + safety stock, restar lo que hay
    target            = safety_stock + daily_units * 7
    recommended_units = max(0, round(target - on_hand)) if needs_reposition else 0
    return {
        "days_cover":       days_cover,
        "needs_reposition": needs_reposition,
        "recommended_units": recommended_units,
    }


# ── Tools (datos mock; reemplazar con DB real) ────────────────────────────────
@tool
def get_stock(sku: str) -> dict:
    """Obtiene stock actual y safety stock de un SKU."""
    mock = {
        "SKU-001": {"on_hand": 8,  "safety_stock": 15},
        "SKU-002": {"on_hand": 40, "safety_stock": 20},
    }
    return mock.get(sku, {"on_hand": 0, "safety_stock": 10})


@tool
def get_sales_velocity(sku: str) -> dict:
    """Obtiene velocidad diaria de venta de un SKU."""
    mock = {
        "SKU-001": {"daily_units": 6},
        "SKU-002": {"daily_units": 2},
    }
    return mock.get(sku, {"daily_units": 1})


# ── JSON extractor  (tolerante a ```json ... ``` del LLM) ─────────────────────
_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

def extract_json(text: str) -> dict:
    """Intenta parsear JSON desde la respuesta del LLM, con limpieza."""
    # 1. Bloque de código markdown
    m = _JSON_BLOCK.search(text)
    if m:
        text = m.group(1)
    # 2. Primer { ... } completo
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


# ── Serialización segura ──────────────────────────────────────────────────────
def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return to_jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return to_jsonable(value.dict())
        except Exception:
            pass
    if hasattr(value, "content"):
        return {"type": value.__class__.__name__, "content": to_jsonable(value.content)}
    return str(value)


# ── Main ──────────────────────────────────────────────────────────────────────
t_start = time.monotonic()

try:
    if agent_name != "reposition":
        raise ValueError(f"unsupported agent: {agent_name}")

    llm = ChatOpenAI(model="gpt-4.1-mini", temperature=0)

    # ── Prompt que FUERZA JSON estructurado ───────────────────────────────────
    system_prompt = """Eres un agente de reposición retail.

SIEMPRE debes devolver JSON válido con EXACTAMENTE esta estructura:

{
  "sku": string,
  "on_hand": number,
  "safety_stock": number,
  "daily_units": number,
  "days_cover": number,
  "needs_reposition": boolean,
  "recommended_units": number
}

Instrucciones:
1. Usa las tools para obtener datos reales del SKU.
2. Calcula days_cover = on_hand / daily_units (redondea a 1 decimal).
3. needs_reposition = true si on_hand < safety_stock.
4. recommended_units = safety_stock + daily_units*7 - on_hand  (mínimo 0, solo si needs_reposition=true).
5. NO expliques. NO escribas texto adicional. SOLO JSON."""

    agent = create_agent(
        model=llm,
        tools=[get_stock, get_sales_velocity],
        system_prompt=system_prompt,
    )

    raw = agent.invoke({
        "messages": [
            {"role": "user", "content": f"Evalúa reposición para {sku}. Responde SOLO con el JSON requerido."}
        ]
    })

    # ── Extraer texto de la última mensaje ────────────────────────────────────
    last_msg = raw["messages"][-1] if isinstance(raw, dict) and "messages" in raw else raw
    raw_text = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

    # ── Parsear y validar JSON ────────────────────────────────────────────────
    try:
        llm_data = extract_json(raw_text)
    except (json.JSONDecodeError, ValueError) as parse_err:
        raise RuntimeError(
            f"El LLM no devolvió JSON válido: {parse_err}\n"
            f"Respuesta cruda: {raw_text[:500]}"
        )

    # ── Policy override: recalculamos on_hand / safety_stock / daily_units ────
    # El LLM puede equivocarse en las fórmulas; la policy engine es la fuente de
    # verdad para las métricas derivadas (days_cover, needs_reposition,
    # recommended_units).
    policy = compute_policy(
        on_hand       = float(llm_data.get("on_hand", 0)),
        safety_stock  = float(llm_data.get("safety_stock", 0)),
        daily_units   = float(llm_data.get("daily_units", 1)),
    )
    llm_data.update(policy)          # override con valores determinísticos
    llm_data["sku"] = sku            # siempre el SKU de la tarea

    # ── Validar schema final ──────────────────────────────────────────────────
    decision = RepositionDecision(**llm_data)

    elapsed = round(time.monotonic() - t_start, 3)

    # ── result.json (payload operativo) ──────────────────────────────────────
    result = {
        "ok":        True,
        "agent":     agent_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision":  decision.model_dump(),
    }

    # ── metrics.json (observabilidad) ─────────────────────────────────────────
    metrics = {
        "instance_id":       workspace.name,
        "agent":             agent_name,
        "sku":               sku,
        "latency_seconds":   elapsed,
        "needs_reposition":  decision.needs_reposition,
        "recommended_units": decision.recommended_units,
        "timestamp":         result["timestamp"],
    }

    (workspace / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (workspace / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps({
        "ok":        True,
        "workspace": str(workspace),
        "agent":     agent_name,
        "decision":  decision.model_dump(),
    }, ensure_ascii=False))

except (ValidationError, RuntimeError, ValueError, Exception) as e:
    elapsed = round(time.monotonic() - t_start, 3)
    err_payload = {
        "ok":              False,
        "agent":           agent_name,
        "sku":             sku,
        "error":           str(e),
        "latency_seconds": elapsed,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
    }
    (workspace / "result.json").write_text(
        json.dumps(err_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (workspace / "metrics.json").write_text(
        json.dumps({**err_payload, "instance_id": workspace.name}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    raise
