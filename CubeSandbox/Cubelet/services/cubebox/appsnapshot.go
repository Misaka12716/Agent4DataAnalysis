// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cubebox

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime/debug"
	"time"

	"github.com/containerd/containerd/v2/pkg/namespaces"
	"github.com/containerd/typeurl/v2"
	"google.golang.org/grpc/metadata"

	"github.com/tencentcloud/CubeSandbox/Cubelet/api/services/cubebox/v1"
	"github.com/tencentcloud/CubeSandbox/Cubelet/api/services/errorcode/v1"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/constants"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/log"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/pathutil"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/recov"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/ret"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

const (
	DefaultSnapshotDir = "/usr/local/services/cubetoolbox/cube-snapshot"

	DefaultCubeRuntimePath = "/usr/local/services/cubetoolbox/cube-shim/bin/cube-runtime"

	SnapshotStatusPath = "/data/cube-shim/snapshot"
)

type CubeboxSnapshotSpec struct {
	Resource json.RawMessage `json:"resource,omitempty"`
	Disk     json.RawMessage `json:"disk,omitempty"`
	Pmem     json.RawMessage `json:"pmem,omitempty"`
	Kernel   string          `json:"kernel,omitempty"`
}

type ResourceSpec struct {
	CPU    int `json:"cpu"`
	Memory int `json:"memory"`
}

func (s *service) AppSnapshot(ctx context.Context, req *cubebox.AppSnapshotRequest) (*cubebox.AppSnapshotResponse, error) {
	rsp := &cubebox.AppSnapshotResponse{
		Ret: &errorcode.Ret{RetCode: errorcode.ErrorCode_Success},
	}

	createReq := req.GetCreateRequest()
	if createReq == nil {
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = "create_request is required"
		return rsp, nil
	}

	rsp.RequestID = createReq.RequestID

	if err := validateAppSnapshotAnnotations(createReq); err != nil {
		rerr, _ := ret.FromError(err)
		rsp.Ret.RetMsg = rerr.Message()
		rsp.Ret.RetCode = rerr.Code()
		return rsp, nil
	}

	templateID := createReq.GetAnnotations()[constants.MasterAnnotationAppSnapshotTemplateID]
	rsp.TemplateID = templateID
	if err := pathutil.ValidateSafeID(templateID); err != nil {
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = fmt.Sprintf("invalid templateID: %v", err)
		return rsp, nil
	}

	rt := &CubeLog.RequestTrace{
		Action:       "AppSnapshot",
		RequestID:    createReq.RequestID,
		Caller:       constants.CubeboxServiceID.ID(),
		Callee:       s.engine.ID(),
		CalleeAction: "AppSnapshot",
		AppID:        getAppID(createReq.Annotations),
		Qualifier:    getUserAgent(ctx),
	}
	ctx = CubeLog.WithRequestTrace(ctx, rt)

	stepLog := log.G(ctx).WithFields(CubeLog.Fields{
		"step":       "appSnapshot",
		"templateID": templateID,
	})

	stepLog.Infof("AppSnapshotRequest: templateID=%s", templateID)

	defer recov.HandleCrash(func(panicError interface{}) {
		stepLog.Fatalf("AppSnapshot panic info:%s, stack:%s", panicError, string(debug.Stack()))
		rsp.Ret.RetMsg = string(debug.Stack())
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
	})

	stepLog.Info("Step 1: Creating cubebox...")
	createRsp, err := s.Create(ctx, createReq)
	if err != nil {
		stepLog.Errorf("Failed to create cubebox: %v", err)
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
		rsp.Ret.RetMsg = fmt.Sprintf("failed to create cubebox: %v", err)
		return rsp, nil
	}

	if createRsp.Ret.RetCode == errorcode.ErrorCode_PreConditionFailed {
		stepLog.Warnf("Create cubebox failed with PreConditionFailed, trying to cleanup and retry...")

		expectedSandboxID := templateID + "_0"
		stepLog.Infof("Attempting to destroy existing sandbox: %s", expectedSandboxID)

		cleanupReq := &cubebox.DestroyCubeSandboxRequest{
			RequestID: createReq.RequestID,
			SandboxID: expectedSandboxID,
		}
		cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 30*time.Second)
		cleanupCtx = inheritIncomingMetadata(cleanupCtx, ctx)
		cleanupRsp, cleanupErr := s.Destroy(cleanupCtx, cleanupReq)
		cleanupCancel()

		if cleanupErr != nil {
			stepLog.Errorf("Cleanup destroy failed: %v", cleanupErr)
			rsp.Ret = createRsp.Ret
			return rsp, nil
		}
		if !ret.IsSuccessCode(cleanupRsp.Ret.RetCode) {
			stepLog.Errorf("Cleanup destroy failed: %s", cleanupRsp.Ret.RetMsg)
			rsp.Ret = createRsp.Ret
			return rsp, nil
		}
		stepLog.Infof("Cleaned up existing sandbox: %s, retrying create...", expectedSandboxID)

		createRsp, err = s.Create(ctx, createReq)
		if err != nil {
			stepLog.Errorf("Failed to create cubebox on retry: %v", err)
			rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
			rsp.Ret.RetMsg = fmt.Sprintf("failed to create cubebox on retry: %v", err)
			return rsp, nil
		}
	}

	if !ret.IsSuccessCode(createRsp.Ret.RetCode) {
		stepLog.Errorf("Create cubebox failed: %s", createRsp.Ret.RetMsg)
		rsp.Ret = createRsp.Ret
		return rsp, nil
	}

	sandboxID := createRsp.SandboxID
	rsp.SandboxID = sandboxID
	stepLog = stepLog.WithFields(CubeLog.Fields{"sandboxID": sandboxID})
	stepLog.Infof("Cubebox created successfully: %s", sandboxID)

	snapshotSuccess := false

	forceDestroyCubebox := func() {
		destroyCtx, destroyCancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer destroyCancel()
		destroyCtx = inheritIncomingMetadata(destroyCtx, ctx)
		destroyCtx = CubeLog.WithRequestTrace(destroyCtx, rt)
		destroyCtx = namespaces.WithNamespace(destroyCtx, namespaces.Default)

		stepLog.Info("Cleanup: Force destroying cubebox...")
		forceDestroyReq := &cubebox.DestroyCubeSandboxRequest{
			RequestID: createReq.RequestID,
			SandboxID: sandboxID,
		}
		if destroyRsp, destroyErr := s.Destroy(destroyCtx, forceDestroyReq); destroyErr != nil {
			stepLog.Errorf("Force destroy failed: %v", destroyErr)
		} else if !ret.IsSuccessCode(destroyRsp.Ret.RetCode) {
			stepLog.Errorf("Force destroy failed: %s", destroyRsp.Ret.RetMsg)
		} else {
			stepLog.Info("Force destroy succeeded")
		}
	}

	defer func() {
		if !snapshotSuccess {
			forceDestroyCubebox()
		}
	}()

	stepLog.Info("Step 2: Getting cubebox spec...")
	spec, err := s.getCubeboxSnapshotSpec(ctx, sandboxID)
	if err != nil {
		stepLog.Errorf("Failed to get cubebox spec: %v", err)
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
		rsp.Ret.RetMsg = fmt.Sprintf("failed to get cubebox spec: %v", err)
		return rsp, nil
	}
	stepLog.Infof("Cubebox spec retrieved: resource=%s, disk=%s, pmem=%s, kernel=%s",
		string(spec.Resource), string(spec.Disk), string(spec.Pmem), spec.Kernel)

	var resourceSpec ResourceSpec
	if err := json.Unmarshal(spec.Resource, &resourceSpec); err != nil {
		stepLog.Errorf("Failed to parse resource spec: %v", err)
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
		rsp.Ret.RetMsg = fmt.Sprintf("failed to parse resource spec: %v", err)
		return rsp, nil
	}
	if resourceSpec.CPU <= 0 || resourceSpec.Memory <= 0 {
		stepLog.Errorf("Invalid resource spec: cpu=%d, memory=%d", resourceSpec.CPU, resourceSpec.Memory)
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = fmt.Sprintf("invalid resource spec: cpu=%d, memory=%d", resourceSpec.CPU, resourceSpec.Memory)
		return rsp, nil
	}

	snapshotDir := req.GetSnapshotDir()
	if snapshotDir == "" {
		snapshotDir = DefaultSnapshotDir
	}
	specDir := fmt.Sprintf("%dC%dM", resourceSpec.CPU, resourceSpec.Memory)
	snapshotPath := filepath.Join(snapshotDir, "cubebox", templateID, specDir)
	if _, err := pathutil.ValidatePathUnderBase(snapshotDir, snapshotPath); err != nil {
		stepLog.Errorf("Invalid snapshot path: %v", err)
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = fmt.Sprintf("invalid snapshot path: %v", err)
		return rsp, nil
	}
	rsp.SnapshotPath = snapshotPath

	tmpSnapshotPath := snapshotPath + ".tmp"
	if _, err := pathutil.ValidatePathUnderBase(snapshotDir, tmpSnapshotPath); err != nil {
		stepLog.Errorf("Invalid tmp snapshot path: %v", err)
		rsp.Ret.RetCode = errorcode.ErrorCode_InvalidParamFormat
		rsp.Ret.RetMsg = fmt.Sprintf("invalid tmp snapshot path: %v", err)
		return rsp, nil
	}
	stepLog.Infof("Step 3: Creating snapshot at temporary path: %s", tmpSnapshotPath)

	// NOCC:Path Traversal()
	if err := os.RemoveAll(tmpSnapshotPath); err != nil {
		stepLog.Warnf("Failed to remove existing temp directory: %v", err)
	}

	stepLog.Info("Step 4: Executing cube-runtime snapshot...")
	if err := s.executeCubeRuntimeSnapshot(ctx, sandboxID, spec, tmpSnapshotPath); err != nil {
		stepLog.Errorf("Failed to execute cube-runtime snapshot: %v", err)

		os.RemoveAll(tmpSnapshotPath) // NOCC:Path Traversal()
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
		rsp.Ret.RetMsg = fmt.Sprintf("failed to execute cube-runtime snapshot: %v", err)
		return rsp, nil
	}
	stepLog.Info("cube-runtime snapshot executed successfully")

	stepLog.Info("Step 5: Moving snapshot to final path...")

	// NOCC:Path Traversal()
	if err := os.RemoveAll(snapshotPath); err != nil {
		stepLog.Warnf("Failed to remove existing snapshot directory: %v", err)
	}

	if err := os.Rename(tmpSnapshotPath, snapshotPath); err != nil {
		stepLog.Errorf("Failed to move snapshot to final path: %v", err)
		os.RemoveAll(tmpSnapshotPath) // NOCC:Path Traversal()
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
		rsp.Ret.RetMsg = fmt.Sprintf("failed to move snapshot: %v", err)
		return rsp, nil
	}

	stepLog.Info("Step 6: Writing snapshot status flag file...")
	if err := writeSnapshotFlag(stepLog); err != nil {
		stepLog.Warnf("Failed to write snapshot flag: %v", err)

	}

	stepLog.Infof("Step 7: Destroying cubebox with appsnapshot.finished annotation (templateID=%s)...", templateID)
	destroyCtx, destroyCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer destroyCancel()
	destroyCtx = inheritIncomingMetadata(destroyCtx, ctx)
	destroyCtx = CubeLog.WithRequestTrace(destroyCtx, rt)
	destroyCtx = namespaces.WithNamespace(destroyCtx, namespaces.Default)

	annotatedDestroyReq := &cubebox.DestroyCubeSandboxRequest{
		RequestID: createReq.RequestID,
		SandboxID: sandboxID,
		Annotations: map[string]string{
			constants.AnnotationAppSnapshotFinished: templateID,
		},
	}

	destroyRsp, destroyErr := s.Destroy(destroyCtx, annotatedDestroyReq)
	if destroyErr != nil {
		stepLog.Errorf("Annotated destroy failed: %v", destroyErr)

		stepLog.Warn("Fallback: trying force destroy...")
		forceDestroyCubebox()
		rsp.Ret.RetCode = errorcode.ErrorCode_Unknown
		rsp.Ret.RetMsg = fmt.Sprintf("failed to destroy cubebox with annotation: %v", destroyErr)
		return rsp, nil
	}
	if !ret.IsSuccessCode(destroyRsp.Ret.RetCode) {
		stepLog.Errorf("Annotated destroy failed: %s", destroyRsp.Ret.RetMsg)

		stepLog.Warn("Fallback: trying force destroy...")
		forceDestroyCubebox()
		rsp.Ret.RetCode = destroyRsp.Ret.RetCode
		rsp.Ret.RetMsg = fmt.Sprintf("failed to destroy cubebox with annotation: %s", destroyRsp.Ret.RetMsg)
		return rsp, nil
	}

	snapshotSuccess = true

	stepLog.Infof("AppSnapshot completed successfully: snapshotPath=%s", snapshotPath)
	rsp.Ret.RetMsg = "success"
	return rsp, nil
}

func inheritIncomingMetadata(dst context.Context, src context.Context) context.Context {
	if md, ok := metadata.FromIncomingContext(src); ok {
		return metadata.NewIncomingContext(dst, md.Copy())
	}
	return dst
}

func writeSnapshotFlag(stepLog *log.CubeWrapperLogEntry) error {

	if _, err := os.Stat(SnapshotStatusPath); err == nil {
		stepLog.Info("Snapshot status flag file already exists, skipping")
		return nil
	}

	if err := os.MkdirAll(filepath.Dir(SnapshotStatusPath), 0755); err != nil {
		return fmt.Errorf("failed to create directory: %w", err)
	}

	file, err := os.Create(SnapshotStatusPath)
	if err != nil {
		return fmt.Errorf("failed to create flag file: %w", err)
	}
	file.Close()

	cmd := exec.Command("chattr", "+i", SnapshotStatusPath)
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("failed to set immutable attribute: %w", err)
	}

	stepLog.Infof("Snapshot status flag file created: %s", SnapshotStatusPath)
	return nil
}

func validateAppSnapshotAnnotations(req *cubebox.RunCubeSandboxRequest) error {
	annotations := req.GetAnnotations()
	if annotations == nil {
		return ret.Err(errorcode.ErrorCode_InvalidParamFormat,
			"annotations are required for app snapshot")
	}

	createFlag, ok := annotations[constants.MasterAnnotationsAppSnapshotCreate]
	if !ok || createFlag != "true" {
		return ret.Err(errorcode.ErrorCode_InvalidParamFormat,
			fmt.Sprintf("annotation %s must be set to \"true\"", constants.MasterAnnotationsAppSnapshotCreate))
	}

	templateID, ok := annotations[constants.MasterAnnotationAppSnapshotTemplateID]
	if !ok || templateID == "" {
		return ret.Err(errorcode.ErrorCode_InvalidParamFormat,
			fmt.Sprintf("annotation %s is required and must not be empty", constants.MasterAnnotationAppSnapshotTemplateID))
	}

	return nil
}

func (s *service) getCubeboxSnapshotSpec(ctx context.Context, sandboxID string) (*CubeboxSnapshotSpec, error) {

	cb, err := s.cubeboxMgr.cubeboxManger.Get(ctx, sandboxID)
	if err != nil {
		return nil, fmt.Errorf("failed to get cubebox from store: %w", err)
	}

	ns := cb.Namespace
	if ns == "" {
		ns = namespaces.Default
	}
	ctx = namespaces.WithNamespace(ctx, ns)

	container, err := s.cubeboxMgr.client.LoadContainer(ctx, sandboxID)
	if err != nil {
		return nil, fmt.Errorf("failed to load container: %w", err)
	}

	info, err := container.Info(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get container info: %w", err)
	}

	if info.Spec == nil {
		return nil, fmt.Errorf("container spec is nil")
	}

	specAny, err := typeurl.UnmarshalAny(info.Spec)
	if err != nil {
		return nil, fmt.Errorf("failed to unmarshal container spec: %w", err)
	}

	type ociSpec struct {
		Annotations map[string]string `json:"annotations,omitempty"`
	}

	specBytes, err := json.Marshal(specAny)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal spec: %w", err)
	}

	var spec ociSpec
	if err := json.Unmarshal(specBytes, &spec); err != nil {
		return nil, fmt.Errorf("failed to unmarshal spec to ociSpec: %w", err)
	}

	annotations := spec.Annotations
	if annotations == nil {
		return nil, fmt.Errorf("spec annotations are nil")
	}

	result := &CubeboxSnapshotSpec{
		Kernel: annotations[constants.AnnotationsVMKernelPath],
	}

	if vmmres, ok := annotations[constants.AnnotationsVMSpecKey]; ok && vmmres != "" {
		result.Resource = json.RawMessage(vmmres)
	}

	if disk, ok := annotations[constants.AnnotationsMountListKey]; ok && disk != "" {
		result.Disk = json.RawMessage(disk)
	}

	if pmem, ok := annotations[constants.AnnotationPmem]; ok && pmem != "" {
		result.Pmem = json.RawMessage(pmem)
	}

	return result, nil
}

func (s *service) executeCubeRuntimeSnapshot(ctx context.Context, sandboxID string, spec *CubeboxSnapshotSpec, snapshotPath string) error {
	stepLog := log.G(ctx).WithFields(CubeLog.Fields{
		"sandboxID":    sandboxID,
		"snapshotPath": snapshotPath,
	})

	args := []string{
		"snapshot",
		"--app-snapshot",
		"--vm-id", sandboxID,
		"--path", snapshotPath,
		"--force",
	}

	if len(spec.Resource) > 0 {
		args = append(args, "--resource", string(spec.Resource))
	}

	if len(spec.Disk) > 0 {
		args = append(args, "--disk", string(spec.Disk))
	}

	if len(spec.Pmem) > 0 {
		args = append(args, "--pmem", string(spec.Pmem))
	}

	if spec.Kernel != "" {
		args = append(args, "--kernel", spec.Kernel)
	}

	stepLog.Infof("Executing: %s %v", DefaultCubeRuntimePath, args)

	cmd := exec.CommandContext(ctx, DefaultCubeRuntimePath, args...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		stepLog.Errorf("cube-runtime snapshot failed: %v, output: %s", err, string(output))
		return fmt.Errorf("cube-runtime snapshot failed: %w, output: %s", err, string(output))
	}

	stepLog.Infof("cube-runtime snapshot output: %s", string(output))
	return nil
}
