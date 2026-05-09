// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package utils

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
)

func TestExec(t *testing.T) {
	out, err, execErr := Exec("echo 'hello world'", time.Second)
	assert.NoError(t, execErr)
	assert.Equal(t, "hello world\n", out)
	assert.Empty(t, err)
}

func TestExecV(t *testing.T) {
	out, err, execErr := ExecV([]string{"bash", "-c", "echo 'hello world'"}, time.Second)
	assert.NoError(t, execErr)
	assert.Equal(t, "hello world\n", out)
	assert.Empty(t, err)

	_, _, execErr = ExecV(nil, time.Second)
	assert.Error(t, execErr)
	assert.Contains(t, execErr.Error(), "cmd not found")

	_, _, execErr = ExecV([]string{"bash", "-c", "sleep 2"}, 100*time.Millisecond)
	assert.Error(t, execErr)

	assert.Contains(t, execErr.Error(), "signal: killed")
}

func TestExecVCtx(t *testing.T) {

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	out, err, execErr := ExecVCtx(ctx, []string{"bash", "-c", "echo 'hello world'"})
	assert.NoError(t, execErr)
	assert.Equal(t, "hello world\n", out)
	assert.Empty(t, err)

	_, _, execErr = ExecVCtx(ctx, nil)
	assert.Error(t, execErr)
	assert.Contains(t, execErr.Error(), "cmd not found")

	_, _, execErr = ExecVCtx(ctx, []string{"ls"})
	assert.NoError(t, execErr)

	timeoutCtx, timeoutCancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer timeoutCancel()

	_, _, execErr = ExecVCtx(timeoutCtx, []string{"bash", "-c", "sleep 2"})
	assert.Error(t, execErr)

	assert.Contains(t, execErr.Error(), "signal: killed")

	cancelledCtx, cancelFunc := context.WithCancel(context.Background())
	cancelFunc()

	_, _, execErr = ExecVCtx(cancelledCtx, []string{"bash", "-c", "echo 'test'"})
	assert.Error(t, execErr)
	assert.Contains(t, execErr.Error(), "context canceled")
}

func TestExecBin(t *testing.T) {

	out, err, execErr := ExecBin("echo", []string{"hello", "world"}, time.Second)
	assert.NoError(t, execErr)
	assert.Equal(t, "hello world\n", out)
	assert.Empty(t, err)

	out, err, execErr = ExecBin("pwd", nil, time.Second)
	assert.NoError(t, execErr)
	assert.NotEmpty(t, out)
	assert.Empty(t, err)

	out, err, execErr = ExecBin("pwd", []string{}, time.Second)
	assert.NoError(t, execErr)
	assert.NotEmpty(t, out)
	assert.Empty(t, err)

	_, _, execErr = ExecBin("nonexistentcommand", []string{}, time.Second)
	assert.Error(t, execErr)

	_, _, execErr = ExecBin("sleep", []string{"2"}, 100*time.Millisecond)
	assert.Error(t, execErr)

	assert.Contains(t, execErr.Error(), "signal: killed")

	_, stderrOut, execErr := ExecBin("ls", []string{"/nonexistent/path"}, time.Second)
	assert.Error(t, execErr)
	assert.NotEmpty(t, stderrOut)
}

func TestExecBinCtx(t *testing.T) {

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	out, err, execErr := ExecBinCtx(ctx, "echo", []string{"hello", "world"})
	assert.NoError(t, execErr)
	assert.Equal(t, "hello world\n", out)
	assert.Empty(t, err)

	out, err, execErr = ExecBinCtx(ctx, "pwd", nil)
	assert.NoError(t, execErr)
	assert.NotEmpty(t, out)
	assert.Empty(t, err)

	out, err, execErr = ExecBinCtx(ctx, "pwd", []string{})
	assert.NoError(t, execErr)
	assert.NotEmpty(t, out)
	assert.Empty(t, err)

	_, _, execErr = ExecBinCtx(ctx, "nonexistentcommand", []string{})
	assert.Error(t, execErr)

	timeoutCtx, timeoutCancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer timeoutCancel()

	_, _, execErr = ExecBinCtx(timeoutCtx, "sleep", []string{"2"})
	assert.Error(t, execErr)

	assert.Contains(t, execErr.Error(), "signal: killed")

	cancelledCtx, cancelFunc := context.WithCancel(context.Background())
	cancelFunc()

	_, _, execErr = ExecBinCtx(cancelledCtx, "echo", []string{"test"})
	assert.Error(t, execErr)
	assert.Contains(t, execErr.Error(), "context canceled")

	_, stderr, execErr := ExecBinCtx(ctx, "ls", []string{"/nonexistent/path"})
	assert.Error(t, execErr)
	assert.NotEmpty(t, stderr)
}
