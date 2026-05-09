// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package sandbox

import "github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"

func cloneStringMap(in map[string]string) map[string]string {
	if len(in) == 0 {
		return nil
	}
	out := make(map[string]string, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}

func buildTemplateAnnotations(templateID string) map[string]string {
	if templateID == "" {
		return nil
	}
	return map[string]string{
		constants.CubeAnnotationAppSnapshotTemplateID: templateID,
	}
}

func templateIDFromLabels(labels map[string]string) string {
	if len(labels) == 0 {
		return ""
	}
	return labels[constants.CubeAnnotationAppSnapshotTemplateID]
}
