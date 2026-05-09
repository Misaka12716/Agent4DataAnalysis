// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package pathutil

import (
	"testing"
)

func TestValidateSafeID(t *testing.T) {
	tests := []struct {
		name    string
		id      string
		wantErr bool
	}{
		{
			name:    "valid_id",
			id:      "valid-id-123",
			wantErr: false,
		},
		{
			name:    "valid_id_with_underscores",
			id:      "valid_id_456",
			wantErr: false,
		},
		{
			name:    "empty_id",
			id:      "",
			wantErr: true,
		},
		{
			name:    "id_with_forward_slash",
			id:      "invalid/id",
			wantErr: true,
		},
		{
			name:    "id_with_backslash",
			id:      "invalid\\id",
			wantErr: true,
		},
		{
			name:    "id_with_double_dot",
			id:      "invalid..id",
			wantErr: true,
		},
		{
			name:    "id_with_traversal_prefix",
			id:      "../etc/passwd",
			wantErr: true,
		},
		{
			name:    "id_with_traversal_suffix",
			id:      "id/../../../etc",
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateSafeID(tt.id)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateSafeID(%q) error = %v, wantErr %v", tt.id, err, tt.wantErr)
			}
		})
	}
}

func TestValidateNoTraversal(t *testing.T) {
	tests := []struct {
		name    string
		path    string
		wantErr bool
	}{
		{
			name:    "valid_absolute_path",
			path:    "/usr/local/services/cubetoolbox/cube-snapshot",
			wantErr: false,
		},
		{
			name:    "valid_relative_path",
			path:    "relative/path/to/file",
			wantErr: false,
		},
		{
			name:    "empty_path",
			path:    "",
			wantErr: false,
		},
		{
			name:    "path_with_dot",
			path:    "/usr/local/./services",
			wantErr: false,
		},
		{
			name:    "path_with_traversal_resolves_clean",
			path:    "/usr/local/../../../etc/passwd",
			wantErr: false,
		},
		{
			name:    "path_with_double_dot_middle_resolves",
			path:    "/usr/local/../services",
			wantErr: false,
		},
		{
			name:    "path_with_traversal_relative",
			path:    "../../etc/passwd",
			wantErr: true,
		},
		{
			name:    "complex_path_still_has_traversal",
			path:    "/a/b/c/./d/../../e",
			wantErr: false,
		},
		{
			name:    "path_with_orphaned_traversal",
			path:    "a/b/../../..",
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := ValidateNoTraversal(tt.path)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateNoTraversal(%q) error = %v, wantErr %v", tt.path, err, tt.wantErr)
			}
		})
	}
}

func TestValidatePathUnderBase(t *testing.T) {
	tests := []struct {
		name       string
		basePath   string
		inputPath  string
		wantErr    bool
		wantResult bool
	}{
		{
			name:       "valid_path_under_base",
			basePath:   "/usr/local/services",
			inputPath:  "/usr/local/services/cubetoolbox",
			wantErr:    false,
			wantResult: true,
		},
		{
			name:       "path_equals_base",
			basePath:   "/usr/local/services",
			inputPath:  "/usr/local/services",
			wantErr:    false,
			wantResult: true,
		},
		{
			name:       "path_not_under_base",
			basePath:   "/usr/local/services",
			inputPath:  "/etc/passwd",
			wantErr:    true,
			wantResult: false,
		},
		{
			name:       "traversal_escape_attempt",
			basePath:   "/usr/local/services",
			inputPath:  "/usr/local/services/../../../etc/passwd",
			wantErr:    true,
			wantResult: false,
		},
		{
			name:       "empty_input_path",
			basePath:   "/usr/local/services",
			inputPath:  "",
			wantErr:    true,
			wantResult: false,
		},
		{
			name:       "relative_input_path",
			basePath:   "/usr/local/services",
			inputPath:  "relative/path",
			wantErr:    true,
			wantResult: false,
		},
		{
			name:       "nested_path_under_base",
			basePath:   "/usr/local/services",
			inputPath:  "/usr/local/services/a/b/c/d",
			wantErr:    false,
			wantResult: true,
		},
		{
			name:       "similar_prefix_not_under",
			basePath:   "/usr/local/services",
			inputPath:  "/usr/local/services2/something",
			wantErr:    true,
			wantResult: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := ValidatePathUnderBase(tt.basePath, tt.inputPath)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidatePathUnderBase(%q, %q) error = %v, wantErr %v", tt.basePath, tt.inputPath, err, tt.wantErr)
				return
			}
			if !tt.wantErr && (result == "") != !tt.wantResult {
				t.Errorf("ValidatePathUnderBase(%q, %q) got result=%q, want result to be non-empty=%v", tt.basePath, tt.inputPath, result, tt.wantResult)
			}
		})
	}
}
