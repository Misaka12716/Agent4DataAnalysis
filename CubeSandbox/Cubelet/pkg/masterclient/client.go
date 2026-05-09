// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package masterclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	cubeletnodemeta "github.com/tencentcloud/CubeSandbox/Cubelet/pkg/cubelet/nodemeta"
	corev1 "k8s.io/api/core/v1"
)

type ResourceSnapshot struct {
	MilliCPU int64 `json:"milli_cpu,omitempty"`
	MemoryMB int64 `json:"memory_mb,omitempty"`
}

type RegisterNodeRequest struct {
	RequestID           string            `json:"requestID,omitempty"`
	NodeID              string            `json:"node_id,omitempty"`
	HostIP              string            `json:"host_ip,omitempty"`
	GRPCPort            int               `json:"grpc_port,omitempty"`
	Labels              map[string]string `json:"labels,omitempty"`
	Capacity            ResourceSnapshot  `json:"capacity,omitempty"`
	Allocatable         ResourceSnapshot  `json:"allocatable,omitempty"`
	InstanceType        string            `json:"instance_type,omitempty"`
	ClusterLabel        string            `json:"cluster_label,omitempty"`
	QuotaCPU            int64             `json:"quota_cpu,omitempty"`
	QuotaMemMB          int64             `json:"quota_mem_mb,omitempty"`
	CreateConcurrentNum int64             `json:"create_concurrent_num,omitempty"`
	MaxMvmNum           int64             `json:"max_mvm_num,omitempty"`
}

type UpdateNodeStatusRequest struct {
	RequestID      string                           `json:"requestID,omitempty"`
	Conditions     []corev1.NodeCondition           `json:"conditions,omitempty"`
	Images         []cubeletnodemeta.ContainerImage `json:"images,omitempty"`
	LocalTemplates []cubeletnodemeta.LocalTemplate  `json:"local_templates,omitempty"`
	HeartbeatTime  time.Time                        `json:"heartbeat_time,omitempty"`
}

type Client struct {
	baseURL    string
	httpClient *http.Client
}

type responseEnvelope struct {
	Ret *struct {
		RetCode int    `json:"ret_code"`
		RetMsg  string `json:"ret_msg"`
	} `json:"ret,omitempty"`
}

func New(endpoint string, timeout time.Duration) *Client {
	return &Client{
		baseURL: strings.TrimRight(endpoint, "/"),
		httpClient: &http.Client{
			Timeout: timeout,
		},
	}
}

func (c *Client) Readyz(ctx context.Context) error {
	return c.get(ctx, "/internal/meta/readyz")
}

func (c *Client) RegisterNode(ctx context.Context, req *RegisterNodeRequest) error {
	return c.post(ctx, "/internal/meta/nodes/register", req)
}

func (c *Client) UpdateNodeStatus(ctx context.Context, nodeID string, req *UpdateNodeStatusRequest) error {
	return c.post(ctx, "/internal/meta/nodes/"+nodeID+"/status", req)
}

func (c *Client) get(ctx context.Context, path string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return err
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("request %s failed: %s", path, resp.Status)
	}
	return decodeRet(resp.Body)
}

func (c *Client) post(ctx context.Context, path string, payload interface{}) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("request %s failed: %s", path, resp.Status)
	}
	return decodeRet(resp.Body)
}

func decodeRet(body io.Reader) error {
	var envelope responseEnvelope
	if err := json.NewDecoder(body).Decode(&envelope); err != nil {
		return err
	}
	if envelope.Ret != nil && envelope.Ret.RetCode != 200 {
		return fmt.Errorf("request failed: %s", envelope.Ret.RetMsg)
	}
	return nil
}
