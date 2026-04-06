use axum::{
    extract::Path,
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use dotenvy::dotenv;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{env, fs, net::SocketAddr, path::PathBuf, process::Command};
use uuid::Uuid;

// ── Request / Response types ──────────────────────────────────────────────────

#[derive(Deserialize)]
struct Req {
    agent: String,
    input: Value,
}

#[derive(Serialize)]
struct SpawnResp {
    ok: bool,
    instance_id: String,
    agent: String,
    workspace: String,
}

#[derive(Serialize)]
struct InstanceResp {
    ok: bool,
    instance_id: String,
    workspace: String,
    status: String,            // "pending" | "ok" | "error"
    task_exists: bool,
    result_exists: bool,
    stdout_exists: bool,
    stderr_exists: bool,
    result: Option<Value>,
    decision: Option<Value>,   // shortcut: result.decision (structured output)
    metrics: Option<Value>,    // metrics.json if present
}

#[derive(Serialize)]
struct DecisionsResp {
    ok: bool,
    total: usize,
    decisions: Vec<Value>,
}

// ── Main ──────────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() {
    dotenv().ok();

    let app = Router::new()
        .route("/health",               get(health))
        .route("/spawn",                post(spawn))
        .route("/instances/:id",        get(inspect_instance))
        .route("/instances/:id/result", get(get_decision))   // clean decision endpoint
        .route("/decisions",            get(list_decisions)); // aggregate view

    let addr = SocketAddr::from(([127, 0, 0, 1], 8080));
    println!("Running on http://{}", addr);

    axum::serve(tokio::net::TcpListener::bind(addr).await.unwrap(), app)
        .await
        .unwrap();
}

// ── Handlers ──────────────────────────────────────────────────────────────────

async fn health() -> Json<Value> {
    Json(json!({ "ok": true, "service": "agent-runtime" }))
}

async fn spawn(Json(req): Json<Req>) -> Result<Json<SpawnResp>, (StatusCode, String)> {
    let repo_root   = detect_repo_root()?;
    let instance_id = Uuid::new_v4().to_string();
    let workspace   = repo_root.join("state").join("instances").join(&instance_id);

    fs::create_dir_all(&workspace).map_err(internal_err)?;

    let task = json!({ "agent": req.agent, "input": req.input });

    fs::write(workspace.join("task.json"), serde_json::to_string_pretty(&task).unwrap())
        .map_err(internal_err)?;

    let stdout_file = fs::File::create(workspace.join("stdout.log")).map_err(internal_err)?;
    let stderr_file = fs::File::create(workspace.join("stderr.log")).map_err(internal_err)?;

    let worker = repo_root.join("workers").join("reposition_worker.py");
    if !worker.exists() {
        return Err((StatusCode::INTERNAL_SERVER_ERROR,
            format!("worker not found at {}", worker.display())));
    }

    let agent_name = task["agent"].as_str().unwrap_or("unknown").to_string();
    let openai_key = env::var("OPENAI_API_KEY").unwrap_or_default();

    Command::new("python3")
        .arg(&worker)
        .env_clear()
        .env("PATH",           env::var("PATH").unwrap_or_default())
        .env("WORKSPACE",      workspace.to_string_lossy().to_string())
        .env("AGENT_NAME",     &agent_name)
        .env("OPENAI_API_KEY", openai_key)
        .stdout(stdout_file)
        .stderr(stderr_file)
        .spawn()
        .map_err(internal_err)?;

    Ok(Json(SpawnResp { ok: true, instance_id, agent: agent_name,
        workspace: workspace.to_string_lossy().to_string() }))
}

async fn inspect_instance(Path(id): Path<String>) -> Result<Json<InstanceResp>, (StatusCode, String)> {
    let (workspace, result, metrics) = load_instance(&id)?;

    let status = match &result {
        None                                          => "pending",
        Some(v) if v.get("ok") == Some(&json!(true)) => "ok",
        _                                             => "error",
    }.to_string();

    let decision = result.as_ref().and_then(|v| v.get("decision")).cloned();

    Ok(Json(InstanceResp {
        ok: true,
        instance_id: id,
        workspace: workspace.to_string_lossy().to_string(),
        status,
        task_exists:   workspace.join("task.json").exists(),
        result_exists: workspace.join("result.json").exists(),
        stdout_exists: workspace.join("stdout.log").exists(),
        stderr_exists: workspace.join("stderr.log").exists(),
        result,
        decision,
        metrics,
    }))
}

/// GET /instances/:id/result — returns ONLY the structured decision (integrable con backend)
async fn get_decision(Path(id): Path<String>) -> Result<Json<Value>, (StatusCode, String)> {
    let (_, result, _) = load_instance(&id)?;
    match result {
        None => Err((StatusCode::ACCEPTED, "pending".to_string())),
        Some(v) => {
            if v.get("ok") == Some(&json!(true)) {
                Ok(Json(v.get("decision").cloned().unwrap_or(v)))
            } else {
                Err((StatusCode::INTERNAL_SERVER_ERROR,
                    v.get("error").and_then(|e| e.as_str())
                     .unwrap_or("unknown error").to_string()))
            }
        }
    }
}

/// GET /decisions — aggregate all completed decisions via metrics.json
async fn list_decisions() -> Result<Json<DecisionsResp>, (StatusCode, String)> {
    let instances_dir = detect_repo_root()?.join("state").join("instances");
    let mut decisions = Vec::new();

    if instances_dir.exists() {
        for entry in fs::read_dir(&instances_dir).map_err(internal_err)?.flatten() {
            let p = entry.path().join("metrics.json");
            if p.exists() {
                if let Ok(raw) = fs::read_to_string(&p) {
                    if let Ok(v) = serde_json::from_str::<Value>(&raw) {
                        decisions.push(v);
                    }
                }
            }
        }
    }

    decisions.sort_by(|a, b| {
        let ta = a.get("timestamp").and_then(|v| v.as_str()).unwrap_or("");
        let tb = b.get("timestamp").and_then(|v| v.as_str()).unwrap_or("");
        tb.cmp(ta)
    });

    let total = decisions.len();
    Ok(Json(DecisionsResp { ok: true, total, decisions }))
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn load_instance(id: &str) -> Result<(PathBuf, Option<Value>, Option<Value>), (StatusCode, String)> {
    let workspace = detect_repo_root()?.join("state").join("instances").join(id);
    if !workspace.exists() {
        return Err((StatusCode::NOT_FOUND, format!("instance {} not found", id)));
    }
    let result  = read_json_opt(&workspace.join("result.json"))?;
    let metrics = read_json_opt(&workspace.join("metrics.json"))?;
    Ok((workspace, result, metrics))
}

fn read_json_opt(path: &PathBuf) -> Result<Option<Value>, (StatusCode, String)> {
    if !path.exists() { return Ok(None); }
    let raw = fs::read_to_string(path).map_err(internal_err)?;
    Ok(Some(serde_json::from_str::<Value>(&raw)
        .unwrap_or_else(|_| json!({ "raw": raw }))))
}

fn detect_repo_root() -> Result<PathBuf, (StatusCode, String)> {
    let cwd       = std::env::current_dir().map_err(internal_err)?;
    let repo_root = cwd.parent().and_then(|p| p.parent()).map(PathBuf::from).unwrap_or(cwd);
    let workers   = repo_root.join("workers");
    if workers.exists() { Ok(repo_root) }
    else {
        Err((StatusCode::INTERNAL_SERVER_ERROR,
            format!("repo root not found; expected workers dir at {}", workers.display())))
    }
}

fn internal_err<E: std::fmt::Display>(e: E) -> (StatusCode, String) {
    (StatusCode::INTERNAL_SERVER_ERROR, e.to_string())
}
