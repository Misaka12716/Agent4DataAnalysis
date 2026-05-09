// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

use std::path::PathBuf;

pub const ANNO_APP_SNAPSHOT_CONTAINER_ID: &str = "cube.appsnapshot.container.id";

#[derive(Debug)]
pub struct CPath {
    pub path: PathBuf,
}

impl CPath {
    pub fn new(p: &str) -> Self {
        CPath {
            path: PathBuf::from(p),
        }
    }

    pub fn join(&mut self, p: &str) -> &mut Self {
        if let Some(stripped) = p.strip_prefix('/') {
            self.path.push(stripped);
        } else {
            self.path.push(p);
        }
        self
    }

    pub fn to_str(&self) -> Option<&str> {
        self.path.to_str()
    }

    pub fn to_path_buf(&self) -> PathBuf {
        self.path.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cpath() {
        let mut cp = CPath::new("/a/b/c");
        cp.join("/d/e");

        //to_str
        let strp = cp.to_str();
        assert!(strp.is_some());
        let p = strp.unwrap();
        assert_eq!(p, "/a/b/c/d/e");

        //to_path_buf
        let p = cp.to_path_buf();
        let strp = p.to_str();
        assert!(strp.is_some());
        let p = strp.unwrap().to_string();
        assert_eq!(p, "/a/b/c/d/e");
    }
}
