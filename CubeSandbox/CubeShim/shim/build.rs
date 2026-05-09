// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

use std::process::Command;

const ENV_BUILD_ENV_K: &str = "BUILD_ENV";
const ENV_BUILD_ENV_V: &str = "zhiyan";
fn main() {
    let output = Command::new("git")
        .args(["rev-parse", "--short=8", "HEAD"])
        .output()
        .expect("Failed to execute git command");

    let commit_hash = String::from_utf8(output.stdout)
        .expect("Invalid UTF-8")
        .trim()
        .to_string();

    let output = Command::new("git")
        .args(["status", "--porcelain"])
        .output()
        .expect("Failed to execute git command");

    let mut zhiyan = false;
    match std::env::var(ENV_BUILD_ENV_K) {
        Ok(v) => {
            if v == ENV_BUILD_ENV_V {
                zhiyan = true
            }
        }
        Err(_e) => {}
    }
    let status = String::from_utf8(output.stdout).expect("Invalid UTF-8");
    let status = status.replace([' ', '\t', '\n'], "");
    let commit_info = {
        if !status.is_empty() && !zhiyan {
            format!("{}--dirty", commit_hash)
        } else {
            commit_hash
        }
    };
    /*
    let output = Command::new("/bin/bash")
        .args(["-c", "cat Cargo.toml | grep cube-hypervisor | awk -F 'rev' '{print $2}' | awk -F '\"' '{print $2}'"])
        .output()
        .expect("Failed to execute cargo metadata command");

    let ch_version = String::from_utf8(output.stdout)
        .expect("Invalid UTF-8")
        .trim()
        .to_string();
    */
    println!("shim version: {}", commit_info.clone());
    //println!("ch version:{}", &ch_version);
    //println!("cargo:rustc-env=CH_GIT_COMMIT_INFO={}", ch_version);
    println!("cargo:rustc-env=GIT_COMMIT_INFO={}", commit_info);
    println!("cargo:rerun-if-changed=build.rs");
}
