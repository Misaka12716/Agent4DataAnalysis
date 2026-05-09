// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package networkagentclient

type EnsureNetworkRequest struct {
	SandboxID       string            `json:"sandboxID,omitempty"`
	IdempotencyKey  string            `json:"idempotencyKey,omitempty"`
	Interfaces      []Interface       `json:"interfaces,omitempty"`
	Routes          []Route           `json:"routes,omitempty"`
	ARPNeighbors    []ARPNeighbor     `json:"arpNeighbors,omitempty"`
	PortMappings    []PortMapping     `json:"portMappings,omitempty"`
	CubeVSContext   *CubeVSContext    `json:"cubevsContext,omitempty"`
	PersistMetadata map[string]string `json:"persistMetadata,omitempty"`
}

type EnsureNetworkResponse struct {
	SandboxID       string            `json:"sandboxID,omitempty"`
	NetworkHandle   string            `json:"networkHandle,omitempty"`
	Interfaces      []Interface       `json:"interfaces,omitempty"`
	Routes          []Route           `json:"routes,omitempty"`
	ARPNeighbors    []ARPNeighbor     `json:"arpNeighbors,omitempty"`
	PortMappings    []PortMapping     `json:"portMappings,omitempty"`
	PersistMetadata map[string]string `json:"persistMetadata,omitempty"`
}

type ReleaseNetworkRequest struct {
	SandboxID       string            `json:"sandboxID,omitempty"`
	NetworkHandle   string            `json:"networkHandle,omitempty"`
	IdempotencyKey  string            `json:"idempotencyKey,omitempty"`
	PersistMetadata map[string]string `json:"persistMetadata,omitempty"`
}

type ReleaseNetworkResponse struct {
	Released        bool              `json:"released,omitempty"`
	PersistMetadata map[string]string `json:"persistMetadata,omitempty"`
}

type ReconcileNetworkRequest struct {
	SandboxID       string            `json:"sandboxID,omitempty"`
	NetworkHandle   string            `json:"networkHandle,omitempty"`
	IdempotencyKey  string            `json:"idempotencyKey,omitempty"`
	Interfaces      []Interface       `json:"interfaces,omitempty"`
	Routes          []Route           `json:"routes,omitempty"`
	ARPNeighbors    []ARPNeighbor     `json:"arpNeighbors,omitempty"`
	PortMappings    []PortMapping     `json:"portMappings,omitempty"`
	CubeVSContext   *CubeVSContext    `json:"cubevsContext,omitempty"`
	PersistMetadata map[string]string `json:"persistMetadata,omitempty"`
}

type ReconcileNetworkResponse struct {
	SandboxID       string            `json:"sandboxID,omitempty"`
	NetworkHandle   string            `json:"networkHandle,omitempty"`
	Converged       bool              `json:"converged,omitempty"`
	Interfaces      []Interface       `json:"interfaces,omitempty"`
	Routes          []Route           `json:"routes,omitempty"`
	ARPNeighbors    []ARPNeighbor     `json:"arpNeighbors,omitempty"`
	PortMappings    []PortMapping     `json:"portMappings,omitempty"`
	PersistMetadata map[string]string `json:"persistMetadata,omitempty"`
}

type GetNetworkRequest struct {
	SandboxID     string `json:"sandboxID,omitempty"`
	NetworkHandle string `json:"networkHandle,omitempty"`
}

type GetNetworkResponse struct {
	SandboxID       string            `json:"sandboxID,omitempty"`
	NetworkHandle   string            `json:"networkHandle,omitempty"`
	Interfaces      []Interface       `json:"interfaces,omitempty"`
	Routes          []Route           `json:"routes,omitempty"`
	ARPNeighbors    []ARPNeighbor     `json:"arpNeighbors,omitempty"`
	PortMappings    []PortMapping     `json:"portMappings,omitempty"`
	PersistMetadata map[string]string `json:"persistMetadata,omitempty"`
}

type ListNetworksRequest struct{}

type ListNetworksResponse struct {
	Networks []NetworkState `json:"networks,omitempty"`
}

type NetworkState struct {
	SandboxID     string        `json:"sandboxID,omitempty"`
	NetworkHandle string        `json:"networkHandle,omitempty"`
	TapName       string        `json:"tapName,omitempty"`
	TapIfIndex    int32         `json:"tapIfIndex,omitempty"`
	SandboxIP     string        `json:"sandboxIP,omitempty"`
	PortMappings  []PortMapping `json:"portMappings,omitempty"`
}

type HealthRequest struct{}

type Interface struct {
	Name    string   `json:"name,omitempty"`
	MAC     string   `json:"mac,omitempty"`
	MTU     int32    `json:"mtu,omitempty"`
	IPs     []string `json:"ips,omitempty"`
	Gateway string   `json:"gateway,omitempty"`
}

type Route struct {
	Destination string `json:"destination,omitempty"`
	Gateway     string `json:"gateway,omitempty"`
	Device      string `json:"device,omitempty"`
}

type ARPNeighbor struct {
	IP     string `json:"ip,omitempty"`
	MAC    string `json:"mac,omitempty"`
	Device string `json:"device,omitempty"`
}

type PortMapping struct {
	Protocol      string `json:"protocol,omitempty"`
	HostIP        string `json:"hostIP,omitempty"`
	HostPort      int32  `json:"hostPort,omitempty"`
	ContainerPort int32  `json:"containerPort,omitempty"`
}

type CubeVSContext struct {
	AllowInternetAccess *bool    `json:"allowInternetAccess,omitempty"`
	AllowOut            []string `json:"allowOut,omitempty"`
	DenyOut             []string `json:"denyOut,omitempty"`
}
