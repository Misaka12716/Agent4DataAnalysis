// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

use crate::{error::AppError, state::AppState};
use axum::{
    extract::{Request, State},
    middleware::Next,
    response::Response,
};

/// 鉴权凭证，从请求 header 中提取。
#[derive(Debug)]
enum AuthCredential {
    /// `Authorization: Bearer <token>`
    Bearer(String),
    /// `X-API-Key: <key>`
    ApiKey(String),
}

/// 从请求 headers 中提取鉴权凭证（优先 Bearer，次选 X-API-Key）。
fn extract_credential(request: &Request) -> Option<AuthCredential> {
    let headers = request.headers();

    // 优先检查 Authorization: Bearer
    if let Some(auth_val) = headers.get("Authorization") {
        if let Ok(auth_str) = auth_val.to_str() {
            if let Some(token) = auth_str.strip_prefix("Bearer ") {
                let token = token.trim().to_string();
                if !token.is_empty() {
                    return Some(AuthCredential::Bearer(token));
                }
            }
        }
    }

    // 次选 X-API-Key
    if let Some(key_val) = headers.get("X-API-Key") {
        if let Ok(key_str) = key_val.to_str() {
            let key = key_str.trim().to_string();
            if !key.is_empty() {
                return Some(AuthCredential::ApiKey(key));
            }
        }
    }

    None
}

/// 统一鉴权中间件。
///
/// 行为：
/// - 如果 `config.auth_callback_url` 未配置（`None`），直接放行。
/// - 如果已配置：
///   1. 从请求 header 中提取 Bearer token 或 X-API-Key（优先 Bearer）。
///   2. 向回调 URL 发送 HTTP POST，附带：
///      - `Authorization: Bearer <token>`  （Bearer 模式时透传）
///      - `X-API-Key: <key>`              （API Key 模式时透传）
///      - `X-Request-Path: <原始请求路径>` （客户端访问的路径）
///   3. 回调返回 HTTP 200 → 放行；其他状态码 → 401 Unauthorized。
///
/// 回调方无需额外的类型标识：`Authorization` header 存在即为 Bearer，
/// `X-API-Key` header 存在即为 API Key，两者互斥。
pub async fn unified_auth(
    State(state): State<AppState>,
    request: Request,
    next: Next,
) -> Result<Response, AppError> {
    // 未配置回调地址，直接放行
    let callback_url = match state.config.auth_callback_url.as_deref() {
        Some(url) if !url.is_empty() => url.to_string(),
        _ => return Ok(next.run(request).await),
    };

    // 提取请求路径，透传给回调
    let request_path = request.uri().path().to_string();

    // 提取鉴权凭证
    let credential = extract_credential(&request).ok_or_else(|| {
        AppError::Unauthorized(
            "Missing authentication: provide 'Authorization: Bearer <token>' or 'X-API-Key: <key>'"
                .to_string(),
        )
    })?;

    // 构造回调 POST 请求（仅透传标准凭证 header + 请求路径，不加自定义类型标识）
    let req_builder = state
        .http_client
        .post(&callback_url)
        .header("X-Request-Path", &request_path);

    let req_builder = match &credential {
        AuthCredential::Bearer(token) => {
            req_builder.header("Authorization", format!("Bearer {}", token))
        }
        AuthCredential::ApiKey(key) => req_builder.header("X-API-Key", key.as_str()),
    };

    let callback_resp = req_builder.send().await.map_err(|e| {
        tracing::error!(error = %e, callback_url = %callback_url, "auth callback request failed");
        AppError::Internal(anyhow::anyhow!("Auth callback unreachable: {}", e))
    })?;

    let auth_type = match &credential {
        AuthCredential::Bearer(_) => "bearer",
        AuthCredential::ApiKey(_) => "api_key",
    };

    if callback_resp.status().as_u16() == 200 {
        tracing::debug!(
            path = %request_path,
            auth_type = auth_type,
            "auth callback approved"
        );
        Ok(next.run(request).await)
    } else {
        tracing::warn!(
            status = %callback_resp.status(),
            path = %request_path,
            auth_type = auth_type,
            "auth callback rejected request"
        );
        Err(AppError::Unauthorized(
            "Authentication rejected by callback".to_string(),
        ))
    }
}
