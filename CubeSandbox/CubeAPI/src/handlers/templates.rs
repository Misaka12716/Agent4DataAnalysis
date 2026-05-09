// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

/// Template handlers — not implemented yet.
///
/// All endpoints return HTTP 501 Not Implemented.
/// The route definitions are retained so the API surface is stable.
use axum::{
    extract::{Path, Query, State},
    response::IntoResponse,
};
use serde::Deserialize;

use crate::{
    error::{AppError, AppResult},
    state::AppState,
};

const NOT_IMPLEMENTED_MSG: &str =
    "Template API is not yet implemented. See docs/cubemaster-api-requirements.md.";

// ─── GET /templates ───────────────────────────────────────────────────────────

pub async fn list_templates(State(_): State<AppState>) -> AppResult<impl IntoResponse> {
    Err::<(), _>(AppError::NotImplemented(NOT_IMPLEMENTED_MSG.to_string()))
}

// ─── GET /templates/:templateID ───────────────────────────────────────────────

pub async fn get_template(
    State(_): State<AppState>,
    Path(_template_id): Path<String>,
) -> AppResult<impl IntoResponse> {
    Err::<(), _>(AppError::NotImplemented(NOT_IMPLEMENTED_MSG.to_string()))
}

// ─── POST /templates ──────────────────────────────────────────────────────────

pub async fn create_template(
    State(_): State<AppState>,
    _body: axum::extract::Json<serde_json::Value>,
) -> AppResult<impl IntoResponse> {
    Err::<(), _>(AppError::NotImplemented(NOT_IMPLEMENTED_MSG.to_string()))
}

// ─── POST /templates/:templateID (rebuild) ────────────────────────────────────

pub async fn rebuild_template(
    State(_): State<AppState>,
    Path(_template_id): Path<String>,
    _body: axum::extract::Json<serde_json::Value>,
) -> AppResult<impl IntoResponse> {
    Err::<(), _>(AppError::NotImplemented(NOT_IMPLEMENTED_MSG.to_string()))
}

// ─── PATCH /templates/:templateID ─────────────────────────────────────────────

pub async fn update_template(
    State(_): State<AppState>,
    Path(_template_id): Path<String>,
    _body: axum::extract::Json<serde_json::Value>,
) -> AppResult<impl IntoResponse> {
    Err::<(), _>(AppError::NotImplemented(NOT_IMPLEMENTED_MSG.to_string()))
}

// ─── DELETE /templates/:templateID ────────────────────────────────────────────

pub async fn delete_template(
    State(_): State<AppState>,
    Path(_template_id): Path<String>,
) -> AppResult<impl IntoResponse> {
    Err::<(), _>(AppError::NotImplemented(NOT_IMPLEMENTED_MSG.to_string()))
}

// ─── POST /templates/:templateID/builds/:buildID ──────────────────────────────

pub async fn start_template_build(
    State(_): State<AppState>,
    Path((_template_id, _build_id)): Path<(String, String)>,
) -> AppResult<impl IntoResponse> {
    Err::<(), _>(AppError::NotImplemented(NOT_IMPLEMENTED_MSG.to_string()))
}

// ─── GET /templates/:templateID/builds/:buildID/status ────────────────────────

#[derive(Debug, Deserialize)]
pub struct BuildStatusQuery {
    #[serde(default)]
    #[allow(dead_code)]
    pub logs_offset: i32,
}

pub async fn get_template_build_status(
    State(_): State<AppState>,
    Path((_template_id, _build_id)): Path<(String, String)>,
    Query(_params): Query<BuildStatusQuery>,
) -> AppResult<impl IntoResponse> {
    Err::<(), _>(AppError::NotImplemented(NOT_IMPLEMENTED_MSG.to_string()))
}

// ─── GET /templates/:templateID/builds/:buildID/logs ─────────────────────────

#[derive(Debug, Deserialize)]
pub struct BuildLogsQuery {
    #[serde(default)]
    #[allow(dead_code)]
    pub offset: i32,
    #[serde(default = "default_log_limit")]
    #[allow(dead_code)]
    pub limit: i32,
}
fn default_log_limit() -> i32 {
    100
}

pub async fn get_template_build_logs(
    State(_): State<AppState>,
    Path((_template_id, _build_id)): Path<(String, String)>,
    Query(_params): Query<BuildLogsQuery>,
) -> AppResult<impl IntoResponse> {
    Err::<(), _>(AppError::NotImplemented(NOT_IMPLEMENTED_MSG.to_string()))
}
