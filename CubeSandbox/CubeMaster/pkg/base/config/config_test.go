// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

// Package config provides the configuration for the cube master
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"k8s.io/apimachinery/pkg/api/resource"
)

func TestInit(t *testing.T) {
	mydir, err := os.Getwd()
	if err != nil {
		panic(err)
	}
	fmt.Printf("mydir=%s\n", mydir)
	if os.Getenv("CUBE_MASTER_CONFIG_PATH") == "" {
		configPath := filepath.Clean(filepath.Join(mydir, "../../../test/conf.yaml"))
		if _, statErr := os.Stat(configPath); os.IsNotExist(statErr) {
			t.Skipf("skip TestInit: config fixture not found: %s", configPath)
		}
		os.Setenv("CUBE_MASTER_CONFIG_PATH", configPath)
	}
	_, err = Init()
	assert.NoError(t, err)
	assert.Equal(t, 2, len(GetConfig().ExtraConf.BlkQosMap))
	assert.Equal(t, 2, len(GetConfig().ExtraConf.FsQosMap))

	assert.NotNil(t, GetConfig().Scheduler)
	assert.NotNil(t, GetConfig().Scheduler.LargeSizeAffinityConf)
	cubeboxConf := GetConfig().Scheduler.LargeSizeAffinityConf["cubebox"]
	assert.NotNil(t, cubeboxConf)
	assert.Equal(t, true, cubeboxConf.Enable)
	expectMem := resource.MustParse("100Gi")
	gotMem, err := resource.ParseQuantity(cubeboxConf.MemoryLowerWaterMark)
	assert.NoError(t, err)
	assert.True(t, expectMem.Equal(gotMem))
	expectCpu := resource.MustParse("100000m")
	gotCpu, err := resource.ParseQuantity(cubeboxConf.CpuLowerWaterMark)
	assert.NoError(t, err)
	assert.True(t, expectCpu.Equal(gotCpu))
}

func TestGetEffectiveNodeMaxMemReservedInMBFallsBackForSmallNodes(t *testing.T) {
	sconf := &SchedulerConf{
		NodeMaxMemReservedInMB: 10 * 1024,
	}

	got := sconf.GetEffectiveNodeMaxMemReservedInMB("cubebox", 9450)
	assert.Equal(t, int64(945), got)
}

func TestGetEffectiveNodeMaxMemReservedInMBKeepsConfiguredValue(t *testing.T) {
	sconf := &SchedulerConf{
		NodeMaxMemReservedInMB: 512,
	}

	got := sconf.GetEffectiveNodeMaxMemReservedInMB("cubebox", 9450)
	assert.Equal(t, int64(512), got)
}
