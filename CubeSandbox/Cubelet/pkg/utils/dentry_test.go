// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package utils

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestSupportPrepare(t *testing.T) {
	flag := IsMountLoop("/proc")
	assert.Equal(t, flag, true)

	flag = IsMountLoop("/xxx/yyy/zzz")
	assert.Equal(t, flag, false)
}
