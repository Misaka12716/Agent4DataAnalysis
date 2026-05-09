// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package storage

import (
	"fmt"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/utils"
)

func TestNewExt4BaseRaw(t *testing.T) {
	testDir := t.TempDir()

	filePath := filepath.Join(testDir, "base.raw")
	if err := newExt4BaseRaw(filePath, defaultDiskUUID, 512000); err != nil {
		t.Fatal(err)
	}
}

func TestNewExt4RawByCopy(t *testing.T) {
	testDir := t.TempDir()

	fmt.Println(testDir)

	baseFile := filepath.Join(testDir, "base.raw")
	if err := newExt4BaseRaw(baseFile, defaultDiskUUID, 512000); err != nil {
		t.Fatal(err)
	}

	var err error

	targetFile := filepath.Join(testDir, "target.raw")
	err = newExt4RawByCopy(baseFile, targetFile, 0)
	assert.NoErrorf(t, err, "copy with size 0")

	targetFile = filepath.Join(testDir, "target2.raw")
	err = newExt4RawByCopy(baseFile, targetFile, 1024000)
	assert.NoErrorf(t, err, "copy with size 1024000")

	targetFile = filepath.Join(testDir, "target3.raw")
	err = newExt4RawByCopy(baseFile, targetFile, 128000)
	assert.NoErrorf(t, err, "copy with size 128000")
}

func TestNewExt4RawByReflinkCopy(t *testing.T) {
	utils.SkipCI(t)

	testDir := t.TempDir()

	fmt.Println(testDir)

	baseFile := filepath.Join(testDir, "base.raw")
	if err := newExt4BaseRaw(baseFile, defaultDiskUUID, 512000); err != nil {
		t.Fatal(err)
	}

	var err error

	targetFile := filepath.Join(testDir, "target.raw")
	err = newExt4RawByReflinkCopy(baseFile, targetFile, 0)
	if err != nil {

		t.Skipf("reflink copy not supported on this filesystem: %v", err)
		return
	}
	assert.NoErrorf(t, err, "copy with size 0")

	targetFile = filepath.Join(testDir, "target2.raw")
	err = newExt4RawByReflinkCopy(baseFile, targetFile, 1024000)
	assert.NoErrorf(t, err, "copy with size 1024000")

	targetFile = filepath.Join(testDir, "target3.raw")
	err = newExt4RawByReflinkCopy(baseFile, targetFile, 128000)
	assert.Errorf(t, err, "copy with size 128000")
}
