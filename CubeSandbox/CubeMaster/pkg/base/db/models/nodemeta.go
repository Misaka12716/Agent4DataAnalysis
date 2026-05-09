// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package models

import (
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"gorm.io/gorm"
)

type NodeRegistration struct {
	gorm.Model
	NodeID              string `gorm:"column:node_id"`
	HostIP              string `gorm:"column:host_ip"`
	GRPCPort            int    `gorm:"column:grpc_port"`
	LabelsJSON          string `gorm:"column:labels_json"`
	CapacityJSON        string `gorm:"column:capacity_json"`
	AllocatableJSON     string `gorm:"column:allocatable_json"`
	InstanceType        string `gorm:"column:instance_type"`
	ClusterLabel        string `gorm:"column:cluster_label"`
	QuotaCPU            int64  `gorm:"column:quota_cpu"`
	QuotaMemMB          int64  `gorm:"column:quota_mem_mb"`
	CreateConcurrentNum int64  `gorm:"column:create_concurrent_num"`
	MaxMvmNum           int64  `gorm:"column:max_mvm_num"`
}

func (NodeRegistration) TableName() string {
	return constants.NodeMetaRegistrationTable
}

type NodeStatus struct {
	gorm.Model
	NodeID             string `gorm:"column:node_id"`
	ConditionsJSON     string `gorm:"column:conditions_json"`
	ImagesJSON         string `gorm:"column:images_json"`
	LocalTemplatesJSON string `gorm:"column:local_templates_json"`
	HeartbeatUnix      int64  `gorm:"column:heartbeat_unix"`
	Healthy            bool   `gorm:"column:healthy"`
}

func (NodeStatus) TableName() string {
	return constants.NodeMetaStatusTable
}
