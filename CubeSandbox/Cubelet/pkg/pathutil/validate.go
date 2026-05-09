// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package pathutil

import (
	"fmt"
	"path/filepath"
	"strings"
)

func ValidateSafeID(id string) error {
	if id == "" {
		return fmt.Errorf("id cannot be empty")
	}
	if strings.ContainsAny(id, `/\`) || strings.Contains(id, "..") {
		return fmt.Errorf("invalid id %q: contains path separators or traversal sequences", id)
	}
	return nil
}

func ValidatePathUnderBase(basePath, inputPath string) (string, error) {
	if inputPath == "" {
		return "", fmt.Errorf("path cannot be empty")
	}
	cleaned := filepath.Clean(inputPath)
	if !filepath.IsAbs(cleaned) {
		return "", fmt.Errorf("path %q is not absolute", inputPath)
	}
	baseAbs, err := filepath.Abs(basePath)
	if err != nil {
		return "", fmt.Errorf("invalid base path %q: %w", basePath, err)
	}
	inputAbs, err := filepath.Abs(cleaned)
	if err != nil {
		return "", fmt.Errorf("invalid path %q: %w", inputPath, err)
	}
	if inputAbs != baseAbs && !strings.HasPrefix(inputAbs, baseAbs+string(filepath.Separator)) {
		return "", fmt.Errorf("path %q is not under base %q", inputPath, basePath)
	}
	return inputAbs, nil
}

func ValidateNoTraversal(p string) error {
	if p == "" {
		return nil
	}
	cleaned := filepath.Clean(p)
	if strings.Contains(cleaned, "..") {
		return fmt.Errorf("path %q contains traversal sequence", p)
	}
	return nil
}
