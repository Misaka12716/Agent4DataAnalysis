// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

use crate::log::Log;
use crate::{common::CResult, sandbox::sb::SandBox};
use std::collections::HashMap;
/*
use serde::{Deserialize, Serialize};

use crate::common::utils::Utils;
use crate::warnf;
const ANNO_UPDATE_EXT_ACT: &str = "cube.shimapi.update.action";
const ANNO_UPDATE_EXT_DATA: &str = "cube.shimapi.update.data";

trait Update {
    const ACTION: &'static str;
    async fn process(&self, sb: &SandBox) -> CResult<()>;
}

#[derive(Debug, Serialize, Deserialize)]
struct AppSnapshotCreate {
    pub dest_path: String,
}

impl Update for AppSnapshotCreate {
    const ACTION: &'static str = "AppSnapshotCreate";
    async fn process(&self, sb: &SandBox) -> CResult<()> {
        sb.create_snapshot(self.dest_path.as_str()).await
    }
}*/

pub async fn update_route(
    _sb: &SandBox,
    _annos: &HashMap<String, String>,
    _log: &Log,
) -> CResult<()> {
    Ok(())
}
