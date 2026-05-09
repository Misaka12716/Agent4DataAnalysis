// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package version

import (
	"fmt"
	"os"
	"runtime"
)

var (
	Version = "release"

	Package = "github.com/tencentcloud/CubeSandbox/Cubelet"

	Revision = "v1"

	GoVersion = runtime.Version()
)

func ShowAndExit(show bool) {
	if show {
		fmt.Println("Version: " + Version)
		os.Exit(0)
	}
}

func ShowVersion() string {
	return Version
}
