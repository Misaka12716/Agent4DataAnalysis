// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package storage

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"testing"
	"time"

	"github.com/containerd/containerd/v2/pkg/namespaces"
	"github.com/containerd/plugin"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"k8s.io/apimachinery/pkg/util/wait"

	"github.com/tencentcloud/CubeSandbox/Cubelet/api/services/cubebox/v1"
	"github.com/tencentcloud/CubeSandbox/Cubelet/api/services/errorcode/v1"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/constants"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/ret"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/utils"
	"github.com/tencentcloud/CubeSandbox/Cubelet/plugins/workflow"
)

func makeTestConfig(t *testing.T) *Config {
	testDir := t.TempDir()

	return &Config{
		RootPath:                  filepath.Join(testDir, "root"),
		DataPath:                  filepath.Join(testDir, "data"),
		PoolType:                  cp_type,
		DiskSize:                  "10Mi",
		WarningPercent:            200,
		PoolDefaultFormatSizeList: []string{"1Mi"},
		PoolSize:                  4,
		PoolWorkers:               2,
		PoolTriggerIntervalInMs:   1,
	}
}

func TestParam(t *testing.T) {
	cfg := makeTestConfig(t)

	s := &local{}
	s.config = cfg
	s.config.PoolWorkers = -1
	s.config.PoolTriggerIntervalInMs = -1
	s.config.WarningPercent = -1
	s.config.PoolWorkers = -1
	s.config.PoolDefaultFormatSizeList = nil
	assert.NoError(t, s.init(&plugin.InitContext{Context: context.Background()}))

	s.config.DiskSize = "kk"
	assert.Error(t, s.init(&plugin.InitContext{Context: context.Background()}))

	s.config.PoolDefaultFormatSizeList = []string{"kk"}
	assert.Error(t, s.init(&plugin.InitContext{Context: context.Background()}))

}

func TestCreateDestroy(t *testing.T) {
	cfg := makeTestConfig(t)

	s := &local{}
	s.config = cfg
	assert.NoError(t, s.init(&plugin.InitContext{Context: context.Background()}))

	ctx := namespaces.WithNamespace(context.Background(), namespaces.Default)
	req := &cubebox.RunCubeSandboxRequest{
		Volumes: []*cubebox.Volume{
			{
				Name: "test",
				VolumeSource: &cubebox.VolumeSource{
					EmptyDir: &cubebox.EmptyDirVolumeSource{
						SizeLimit: "1Mi",
					},
				},
			},
		},
	}
	opts := &workflow.CreateContext{
		BaseWorkflowInfo: workflow.BaseWorkflowInfo{
			SandboxID: "test",
		},
		ReqInfo: req,
	}

	err := s.Create(ctx, opts)
	assert.NoError(t, err)
	require.NotNil(t, opts.StorageInfo)
	res := opts.StorageInfo.(*StorageInfo)

	require.Len(t, res.Volumes, 1)
	filePath := res.Volumes["test"].FilePath

	exist, _ := utils.DenExist(filePath)
	assert.True(t, exist)
	assert.Equal(t, 1024*1024+diskSizeExtendInBytes, int(res.Volumes["test"].FSQuota))

	dOpts := &workflow.DestroyContext{
		BaseWorkflowInfo: workflow.BaseWorkflowInfo{
			SandboxID: "test",
		},
	}
	assert.NoError(t, s.Destroy(ctx, dOpts))

	exist, _ = utils.DenExist(filePath)
	assert.False(t, exist)

	assert.NoError(t, s.Destroy(ctx, dOpts))

	assert.Error(t, s.Destroy(ctx, nil))
}
func TestCreateDestroyInvalidVolume(t *testing.T) {
	cfg := makeTestConfig(t)

	s := &local{}
	s.config = cfg
	assert.NoError(t, s.init(&plugin.InitContext{Context: context.Background()}))

	ctx := namespaces.WithNamespace(context.Background(), namespaces.Default)

	dOpts := &workflow.DestroyContext{
		BaseWorkflowInfo: workflow.BaseWorkflowInfo{
			SandboxID: ".",
		},
	}
	assert.NoError(t, s.Destroy(ctx, dOpts))
}

type noopPool struct{}

func (noopPool) Get(context.Context, int64) (*devInfo, error)     { return nil, nil }
func (noopPool) GetSync(context.Context, int64) (*devInfo, error) { return nil, nil }
func (noopPool) Close()                                           {}
func (noopPool) InitBaseFile(context.Context) error               { return nil }

func TestCleanupTemplateLocalDataIsIdempotent(t *testing.T) {
	cfg := makeTestConfig(t)

	s := &local{}
	s.config = cfg
	assert.NoError(t, s.init(&plugin.InitContext{Context: context.Background()}))

	previousLocalStorage := localStorage
	localStorage = s
	t.Cleanup(func() {
		localStorage = previousLocalStorage
	})

	templateID := "tpl-cleanup-" + uuid.NewString()
	snapshotPath := filepath.Join(s.config.RootPath, "snapshots", templateID)
	templatePath := filepath.Join(s.cubeboxTemplateFormatPath, templateID)
	pooledTemplatePath := filepath.Join(s.config.RootPath, "base-block-storage", "templates", "1Mi", templateID)

	require.NoError(t, os.MkdirAll(snapshotPath, 0o755))
	require.NoError(t, os.MkdirAll(templatePath, 0o755))
	require.NoError(t, os.MkdirAll(pooledTemplatePath, 0o755))

	s.tmpPoolFormat.Store(templateID, noopPool{})
	s.poolFormat.Store(templateID, noopPool{})
	derivedPoolKey := filepath.Join("1Mi", templateID, "derived")
	s.poolFormat.Store(derivedPoolKey, noopPool{})

	require.NoError(t, CleanupTemplateLocalData(context.Background(), templateID, snapshotPath))

	_, err := os.Stat(snapshotPath)
	assert.True(t, os.IsNotExist(err))
	_, err = os.Stat(templatePath)
	assert.True(t, os.IsNotExist(err))
	_, err = os.Stat(pooledTemplatePath)
	assert.True(t, os.IsNotExist(err))
	if _, ok := s.tmpPoolFormat.Load(templateID); ok {
		t.Fatal("tmp pool entry should be removed")
	}
	if _, ok := s.poolFormat.Load(templateID); ok {
		t.Fatal("template pool entry should be removed")
	}
	if _, ok := s.poolFormat.Load(derivedPoolKey); ok {
		t.Fatal("derived pool entry should be removed")
	}

	require.NoError(t, CleanupTemplateLocalData(context.Background(), templateID, snapshotPath))
}

func TestCreateWithTimeoutCtx(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 1*time.Millisecond)
	defer cancel()
	time.Sleep(2 * time.Millisecond)

	s := &local{}
	s.config = makeTestConfig(t)
	assert.NoError(t, s.init(&plugin.InitContext{Context: context.Background()}))

	req := &cubebox.RunCubeSandboxRequest{
		Volumes: []*cubebox.Volume{
			{
				Name: "test",
				VolumeSource: &cubebox.VolumeSource{
					EmptyDir: &cubebox.EmptyDirVolumeSource{
						SizeLimit: "1Mi",
					},
				},
			},
		},
	}
	opts := &workflow.CreateContext{
		BaseWorkflowInfo: workflow.BaseWorkflowInfo{
			SandboxID: "test",
		},
		ReqInfo: req,
	}
	assert.NoError(t, s.Create(ctx, opts))
	assert.Nil(t, opts.StorageInfo)
}

func TestCreateWithInvalidParam(t *testing.T) {
	ctx := context.Background()

	s := &local{}
	s.config = makeTestConfig(t)
	assert.NoError(t, s.init(&plugin.InitContext{Context: context.Background()}))

	req := &cubebox.RunCubeSandboxRequest{
		Volumes: []*cubebox.Volume{
			{
				Name: "test",
				VolumeSource: &cubebox.VolumeSource{
					EmptyDir: &cubebox.EmptyDirVolumeSource{
						SizeLimit: "1Mi",
					},
				},
			},
		},
	}
	opts := &workflow.CreateContext{
		BaseWorkflowInfo: workflow.BaseWorkflowInfo{
			SandboxID: "test",
		},
		ReqInfo: req,
	}

	err := s.Create(ctx, nil)
	assert.Error(t, err)
	status, ok := ret.FromError(err)
	require.True(t, ok)
	assert.Equal(t, errorcode.ErrorCode_InvalidParamFormat, status.Code())
	assert.Nil(t, opts.StorageInfo)

	err = s.Create(context.Background(), opts)
	assert.Error(t, err)
	status, ok = ret.FromError(err)
	require.True(t, ok)
	assert.Equal(t, errorcode.ErrorCode_InvalidParamFormat, status.Code())
	assert.Nil(t, opts.StorageInfo)

	opts.ReqInfo = nil
	err = s.Create(ctx, opts)
	assert.Error(t, err)
	status, ok = ret.FromError(err)
	require.True(t, ok)
	assert.Equal(t, errorcode.ErrorCode_InvalidParamFormat, status.Code())
	assert.Nil(t, opts.StorageInfo)

}

func TestMain(m *testing.M) {
	if os.Getenv("CI") != "" {
		fmt.Println("Skipping testing in CI environment")
		return
	}
	m.Run()
}

func TestPollImmediateInfiniteWithContext(t *testing.T) {
	timeout := 5 * time.Second
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	done := make(chan bool, 1)
	errChan := make(chan error, 1)
	interval := 10 * time.Millisecond
	expectedCnt := int(timeout/interval) + 1
	gotCnt := 0
	go func() {
		alreadyRun := false
		err := wait.PollImmediateInfiniteWithContext(ctx, interval, func(ctx context.Context) (bool, error) {
			if !alreadyRun {
				done <- false
				alreadyRun = true
			}
			gotCnt++
			return false, nil
		})
		errChan <- err
	}()

	select {
	case <-done:
	case <-time.After(11 * time.Millisecond):
		t.Fatal("PollImmediateInfiniteWithContext run immediately")
	}

	select {
	case <-ctx.Done():
		assert.Equal(t, expectedCnt, gotCnt)
	case err := <-errChan:
		assert.NoError(t, err)
	}
}

func TestSnapCreateCubebox(t *testing.T) {
	cfg := makeTestConfig(t)

	s := &local{}
	s.config = cfg
	assert.NoError(t, s.init(&plugin.InitContext{Context: context.Background()}))

	templateID := "cube-box-template-id-" + uuid.NewString()
	ctx := namespaces.WithNamespace(context.Background(), namespaces.Default)
	req := &cubebox.RunCubeSandboxRequest{
		Volumes: []*cubebox.Volume{
			{
				Name: "test",
				VolumeSource: &cubebox.VolumeSource{
					EmptyDir: &cubebox.EmptyDirVolumeSource{
						SizeLimit: "1Mi",
					},
				},
			},
		},
		Annotations: map[string]string{
			constants.MasterAnnotationsAppSnapshotCreate:    "true",
			constants.MasterAnnotationAppSnapshotTemplateID: templateID,
		},
		InstanceType: cubebox.InstanceType_cubebox.String(),
	}
	opts := &workflow.CreateContext{
		BaseWorkflowInfo: workflow.BaseWorkflowInfo{
			SandboxID: "test",
		},
		ReqInfo: req,
	}

	err := s.Create(ctx, opts)
	assert.NoError(t, err)
	require.NotNil(t, opts.StorageInfo)
	res := opts.StorageInfo.(*StorageInfo)
	require.Len(t, res.Volumes, 1)
	filePath := res.Volumes["test"].FilePath

	exist, _ := utils.DenExist(filePath)
	assert.True(t, exist)
	assert.Equal(t, 1024*1024+diskSizeExtendInBytes, int(res.Volumes["test"].FSQuota))

	dOpts := &workflow.DestroyContext{
		BaseWorkflowInfo: workflow.BaseWorkflowInfo{
			SandboxID: "test",
		},
	}
	assert.NoError(t, s.Destroy(ctx, dOpts))

	exist, _ = utils.DenExist(filePath)

	assert.True(t, exist)
	p, ok := s.poolFormat.Load(templateID)
	assert.True(t, ok)
	file, err := p.(Pool).Get(ctx, 0)
	assert.NoError(t, err)
	assert.NotNil(t, file)
	t.Logf("file: %v", file.FilePath)

	info, err := s.readBackendFileInfo(ctx, templateID)
	assert.NoError(t, err)
	assert.NotNil(t, info)
	assert.Equal(t, templateID, info.TemplateID)

	assert.NoError(t, s.Destroy(ctx, dOpts))

	assert.Error(t, s.Destroy(ctx, nil))
}

func TestCreateCubeboxBySnap(t *testing.T) {

	cfg := makeTestConfig(t)

	s := &local{}
	s.config = cfg
	assert.NoError(t, s.init(&plugin.InitContext{Context: context.Background()}))

	templateID := "cube-box-template-id-" + uuid.NewString()
	ctx := namespaces.WithNamespace(context.Background(), namespaces.Default)
	req := &cubebox.RunCubeSandboxRequest{
		Volumes: []*cubebox.Volume{
			{
				Name: "test",
				VolumeSource: &cubebox.VolumeSource{
					EmptyDir: &cubebox.EmptyDirVolumeSource{
						SizeLimit: "1Mi",
					},
				},
			},
		},
		Annotations: map[string]string{
			constants.MasterAnnotationsAppSnapshotCreate:    "true",
			constants.MasterAnnotationAppSnapshotTemplateID: templateID,
		},
		InstanceType: cubebox.InstanceType_cubebox.String(),
	}
	opts := &workflow.CreateContext{
		BaseWorkflowInfo: workflow.BaseWorkflowInfo{
			SandboxID: "test",
		},
		ReqInfo: req,
	}

	err := s.Create(ctx, opts)
	assert.NoError(t, err)
	require.NotNil(t, opts.StorageInfo)

	for i := 0; i < 10; i++ {
		reqSnap := &cubebox.RunCubeSandboxRequest{
			Volumes: []*cubebox.Volume{
				{
					Name: "test",
					VolumeSource: &cubebox.VolumeSource{
						EmptyDir: &cubebox.EmptyDirVolumeSource{
							SizeLimit: "1Mi",
						},
					},
				},
			},
			Annotations: map[string]string{
				constants.MasterAnnotationAppSnapshotTemplateID: templateID,
			},
			InstanceType: cubebox.InstanceType_cubebox.String(),
		}
		opts = &workflow.CreateContext{
			BaseWorkflowInfo: workflow.BaseWorkflowInfo{
				SandboxID: "test" + strconv.Itoa(i),
			},
			ReqInfo: reqSnap,
		}
		err = s.Create(ctx, opts)
		assert.NoError(t, err)
		require.NotNil(t, opts.StorageInfo)
		res := opts.StorageInfo.(*StorageInfo)
		require.Len(t, res.Volumes, 1)
		filePath := res.Volumes["test"].FilePath
		t.Logf("filePath: %s", filePath)
		exist, _ := utils.DenExist(filePath)
		assert.True(t, exist)
		assert.Equal(t, 1024*1024+diskSizeExtendInBytes, int(res.Volumes["test"].FSQuota))

		dOpts := &workflow.DestroyContext{
			BaseWorkflowInfo: workflow.BaseWorkflowInfo{
				SandboxID: "test" + strconv.Itoa(i),
			},
		}
		assert.NoError(t, s.Destroy(ctx, dOpts))

		exist, _ = utils.DenExist(filePath)
		assert.False(t, exist)

		assert.NoError(t, s.Destroy(ctx, dOpts))

		assert.Error(t, s.Destroy(ctx, nil))
	}
}

func TestInit(t *testing.T) {

	cfg := makeTestConfig(t)

	s := &local{}
	s.config = cfg
	assert.NoError(t, s.init(&plugin.InitContext{Context: context.Background()}))
	assert.NoError(t, s.Init(context.Background(), nil))
}
