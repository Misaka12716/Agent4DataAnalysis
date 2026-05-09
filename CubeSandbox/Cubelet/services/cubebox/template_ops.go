// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cubebox

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime/debug"
	"strings"

	"github.com/tencentcloud/CubeSandbox/Cubelet/api/services/cubebox/v1"
	"github.com/tencentcloud/CubeSandbox/Cubelet/api/services/errorcode/v1"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/constants"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/log"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/pathutil"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/recov"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/ret"
	"github.com/tencentcloud/CubeSandbox/Cubelet/storage"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

func (s *service) CommitSandbox(ctx context.Context, req *cubebox.CommitSandboxRequest) (*cubebox.CommitSandboxResponse, error) {
	rsp := &cubebox.CommitSandboxResponse{
		RequestID:  req.GetRequestID(),
		SandboxID:  req.GetSandboxID(),
		TemplateID: strings.TrimSpace(req.GetTemplateID()),
		Ret:        &errorcode.Ret{RetCode: errorcode.ErrorCode_Success},
	}
	if rsp.TemplateID == "" {
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = "templateID is required"
		return rsp, nil
	}
	if err := pathutil.ValidateSafeID(rsp.TemplateID); err != nil {
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = fmt.Sprintf("invalid templateID: %v", err)
		return rsp, nil
	}
	if rsp.SandboxID == "" {
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = "sandboxID is required"
		return rsp, nil
	}

	rt := &CubeLog.RequestTrace{
		Action:       "CommitSandbox",
		RequestID:    req.GetRequestID(),
		Caller:       constants.CubeboxServiceID.ID(),
		Callee:       s.engine.ID(),
		CalleeAction: "CommitSandbox",
	}
	ctx = CubeLog.WithRequestTrace(ctx, rt)
	stepLog := log.G(ctx).WithFields(CubeLog.Fields{
		"step":       "commitSandbox",
		"templateID": rsp.TemplateID,
		"sandboxID":  rsp.SandboxID,
	})

	defer recov.HandleCrash(func(panicError interface{}) {
		stepLog.Fatalf("CommitSandbox panic info:%s, stack:%s", panicError, string(debug.Stack()))
		rsp.Ret.RetMsg = string(debug.Stack())
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
	})

	spec, err := s.getCubeboxSnapshotSpec(ctx, rsp.SandboxID)
	if err != nil {
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
		rsp.Ret.RetMsg = fmt.Sprintf("failed to get cubebox spec: %v", err)
		return rsp, nil
	}

	var resourceSpec ResourceSpec
	if err := json.Unmarshal(spec.Resource, &resourceSpec); err != nil {
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
		rsp.Ret.RetMsg = fmt.Sprintf("failed to parse resource spec: %v", err)
		return rsp, nil
	}
	if resourceSpec.CPU <= 0 || resourceSpec.Memory <= 0 {
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = fmt.Sprintf("invalid resource spec: cpu=%d, memory=%d", resourceSpec.CPU, resourceSpec.Memory)
		return rsp, nil
	}

	snapshotDir := req.GetSnapshotDir()
	if snapshotDir == "" {
		snapshotDir = DefaultSnapshotDir
	}
	specDir := fmt.Sprintf("%dC%dM", resourceSpec.CPU, resourceSpec.Memory)
	snapshotPath := filepath.Join(snapshotDir, "cubebox", rsp.TemplateID, specDir)
	if _, err := pathutil.ValidatePathUnderBase(snapshotDir, snapshotPath); err != nil {
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = fmt.Sprintf("invalid snapshot path: %v", err)
		return rsp, nil
	}
	rsp.SnapshotPath = snapshotPath
	tmpSnapshotPath := snapshotPath + ".tmp"
	if _, err := pathutil.ValidatePathUnderBase(snapshotDir, tmpSnapshotPath); err != nil {
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = fmt.Sprintf("invalid tmp snapshot path: %v", err)
		return rsp, nil
	}

	_ = os.RemoveAll(tmpSnapshotPath) // NOCC:Path Traversal()
	if err := s.executeCubeRuntimeSnapshot(ctx, rsp.SandboxID, spec, tmpSnapshotPath); err != nil {
		_ = os.RemoveAll(tmpSnapshotPath) // NOCC:Path Traversal()
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
		rsp.Ret.RetMsg = fmt.Sprintf("failed to execute cube-runtime snapshot: %v", err)
		return rsp, nil
	}
	// NOCC:Path Traversal()
	if err := os.RemoveAll(snapshotPath); err != nil {
		stepLog.Warnf("failed to remove existing snapshot path: %v", err)
	}
	if err := os.Rename(tmpSnapshotPath, snapshotPath); err != nil {
		_ = os.RemoveAll(tmpSnapshotPath) // NOCC:Path Traversal()
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
		rsp.Ret.RetMsg = fmt.Sprintf("failed to move snapshot: %v", err)
		return rsp, nil
	}
	if err := writeSnapshotFlag(stepLog); err != nil {
		stepLog.Warnf("failed to write snapshot flag: %v", err)
	}
	stepLog.Infof("CommitSandbox completed successfully: snapshotPath=%s", snapshotPath)
	return rsp, nil
}

func (s *service) CleanupTemplate(ctx context.Context, req *cubebox.CleanupTemplateRequest) (*cubebox.CleanupTemplateResponse, error) {
	rsp := &cubebox.CleanupTemplateResponse{
		RequestID:  req.GetRequestID(),
		TemplateID: strings.TrimSpace(req.GetTemplateID()),
		Ret:        &errorcode.Ret{RetCode: errorcode.ErrorCode_Success},
	}
	if rsp.TemplateID == "" {
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = "templateID is required"
		return rsp, nil
	}
	if err := pathutil.ValidateSafeID(rsp.TemplateID); err != nil {
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = fmt.Sprintf("invalid templateID: %v", err)
		return rsp, nil
	}
	if sp := req.GetSnapshotPath(); sp != "" {
		if err := pathutil.ValidateNoTraversal(sp); err != nil {
			rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
			rsp.Ret.RetMsg = fmt.Sprintf("invalid snapshotPath: %v", err)
			return rsp, nil
		}
	}
	if err := storage.CleanupTemplateLocalData(ctx, rsp.TemplateID, req.GetSnapshotPath()); err != nil {
		rerr, _ := ret.FromError(err)
		if rerr == nil || rerr.Code() == 0 {
			rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
			rsp.Ret.RetMsg = err.Error()
			return rsp, nil
		}
		rsp.Ret.RetCode = rerr.Code()
		rsp.Ret.RetMsg = rerr.Message()
	}
	return rsp, nil
}
