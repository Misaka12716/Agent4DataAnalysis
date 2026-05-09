// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package utils

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"syscall"
	"time"
)

const DefaultTimeout = time.Second * 3

func Exec(arg string, timeout time.Duration) (string, string, error) {
	var stderrBuffer bytes.Buffer
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, "/usr/bin/bash", "-lc", arg)
	cmd.Stderr = &stderrBuffer
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	output, err := cmd.Output()
	return string(output), stderrBuffer.String(), err
}

func ExecV(argv []string, timeout time.Duration) (string, string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	return ExecVCtx(ctx, argv)
}

func PipeExecV(argv []string, timeout time.Duration) (string, string, error) {
	return "", "", nil
}

func ExecVCtx(ctx context.Context, argv []string) (string, string, error) {
	if len(argv) == 0 {
		return "", "cmd not found", fmt.Errorf("cmd not found")
	}
	var stderrBuffer bytes.Buffer
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Stderr = &stderrBuffer
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	output, err := cmd.Output()
	return string(output), stderrBuffer.String(), err
}

func ExecBin(name string, args []string, timeout time.Duration) (string, string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	return ExecBinCtx(ctx, name, args)
}

func ExecBinCtx(ctx context.Context, name string, args []string) (string, string, error) {
	var stderrBuffer bytes.Buffer
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Stderr = &stderrBuffer
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	output, err := cmd.Output()
	return string(output), stderrBuffer.String(), err
}
