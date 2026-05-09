// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

// Package version provides the version of the client and server
package version

import (
	"strings"

	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/version"
	"github.com/urfave/cli"
)

var Command = cli.Command{
	Name:  "version",
	Usage: "print the client and server versions",
	Flags: []cli.Flag{
		&cli.BoolFlag{
			Name:  "versiononly,v",
			Usage: "print server version only",
		},
		&cli.BoolFlag{
			Name:  "withclient,c",
			Usage: "print client version",
		},
	},
	Action: func(context *cli.Context) error {
		var buf strings.Builder
		if context.Bool("withclient") {
			buf.WriteString(version.ShowVersion() + "\n")
			buf.WriteString("Client:" + "\n")
			buf.WriteString("  Version: " + version.Version + "\n")
			buf.WriteString("  Revision :" + version.Revision + "\n")
			buf.WriteString("  Go version: " + version.GoVersion + "\n")
		}
		return nil
	},
}
