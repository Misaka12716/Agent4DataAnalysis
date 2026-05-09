// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package sandbox

import (
	"testing"

	"github.com/stretchr/testify/assert"
	cubebox "github.com/tencentcloud/CubeSandbox/CubeMaster/api/services/cubebox/v1"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
)

func Test_checkAndGetHostDirVolumeSource(t *testing.T) {
	type args struct {
		src *types.HostDirVolumeSources
		out *cubebox.Volume
	}
	tests := []struct {
		name      string
		args      args
		wantErr   bool
		wantPanic bool
	}{
		{
			name: "nil_src",
			args: args{
				src: nil,
				out: &cubebox.Volume{VolumeSource: &cubebox.VolumeSource{}},
			},
			wantErr: false,
		},
		{
			name: "empty_volume_sources",
			args: args{
				src: &types.HostDirVolumeSources{},
				out: &cubebox.Volume{VolumeSource: &cubebox.VolumeSource{}},
			},
			wantErr: false,
		},
		{
			name: "missing_name",
			args: args{
				src: &types.HostDirVolumeSources{
					VolumeSources: []*types.HostDirSource{
						{Name: "", HostPath: "/data/foo"},
					},
				},
				out: &cubebox.Volume{VolumeSource: &cubebox.VolumeSource{}},
			},
			wantErr: true,
		},
		{
			name: "missing_host_path",
			args: args{
				src: &types.HostDirVolumeSources{
					VolumeSources: []*types.HostDirSource{
						{Name: "vol1", HostPath: ""},
					},
				},
				out: &cubebox.Volume{VolumeSource: &cubebox.VolumeSource{}},
			},
			wantErr: true,
		},
		{
			name: "host_path_not_absolute",
			args: args{
				src: &types.HostDirVolumeSources{
					VolumeSources: []*types.HostDirSource{
						{Name: "vol1", HostPath: "relative/path"},
					},
				},
				out: &cubebox.Volume{VolumeSource: &cubebox.VolumeSource{}},
			},
			wantErr: true,
		},
		{
			name: "valid_single_source",
			args: args{
				src: &types.HostDirVolumeSources{
					VolumeSources: []*types.HostDirSource{
						{Name: "vol1", HostPath: "/data/shared"},
					},
				},
				out: &cubebox.Volume{VolumeSource: &cubebox.VolumeSource{}},
			},
			wantErr: false,
		},
		{
			name: "out_volumeSource_nil_panics",
			args: args{
				src: &types.HostDirVolumeSources{},
				out: &cubebox.Volume{},
			},
			wantPanic: true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if tt.wantPanic {
				assert.Panics(t, func() {
					_ = checkAndGetHostDirVolumeSource(tt.args.src, tt.args.out)
				})
				return
			}
			err := checkAndGetHostDirVolumeSource(tt.args.src, tt.args.out)
			if (err != nil) != tt.wantErr {
				t.Errorf("checkAndGetHostDirVolumeSource() error = %v, wantErr %v", err, tt.wantErr)
			}
			if !tt.wantErr && tt.args.src != nil {
				assert.NotNil(t, tt.args.out.VolumeSource.HostDirVolumes)
				assert.Equal(t, len(tt.args.src.VolumeSources), len(tt.args.out.VolumeSource.HostDirVolumes.VolumeSources))
			}
		})
	}
}
