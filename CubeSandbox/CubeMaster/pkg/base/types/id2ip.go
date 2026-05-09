// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package types

type SandboxProxyMap struct {
	HostIP      string `json:"HostIP"`
	SandboxID   string `json:"SandboxID"`
	SandboxIP   string `json:"SandboxIP,omitempty"`
	SandboxPort string `json:"SandboxPort,omitempty"`

	CreatedAt            string            `json:"CreatedAt,omitempty"`
	ContainerToHostPorts map[string]string `json:"ContainerToHostPorts,omitempty"`
}
