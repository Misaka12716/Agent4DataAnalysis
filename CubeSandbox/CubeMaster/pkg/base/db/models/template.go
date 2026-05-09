// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package models

import (
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"gorm.io/gorm"
)

type TemplateDefinition struct {
	gorm.Model
	TemplateID   string `json:"template_id" gorm:"column:template_id"`
	InstanceType string `json:"instance_type" gorm:"column:instance_type"`
	Version      string `json:"version" gorm:"column:version"`
	Status       string `json:"status" gorm:"column:status"`
	RequestJSON  string `json:"request_json" gorm:"column:request_json"`
	LastError    string `json:"last_error" gorm:"column:last_error"`
}

func (TemplateDefinition) TableName() string {
	return constants.TemplateDefinitionTableName
}

type TemplateReplica struct {
	gorm.Model
	TemplateID      string `json:"template_id" gorm:"column:template_id"`
	NodeID          string `json:"node_id" gorm:"column:node_id"`
	NodeIP          string `json:"node_ip" gorm:"column:node_ip"`
	InstanceType    string `json:"instance_type" gorm:"column:instance_type"`
	Spec            string `json:"spec" gorm:"column:spec"`
	SnapshotPath    string `json:"snapshot_path" gorm:"column:snapshot_path"`
	Status          string `json:"status" gorm:"column:status"`
	Phase           string `json:"phase" gorm:"column:phase"`
	ArtifactID      string `json:"artifact_id" gorm:"column:artifact_id"`
	LastJobID       string `json:"last_job_id" gorm:"column:last_job_id"`
	LastErrorPhase  string `json:"last_error_phase" gorm:"column:last_error_phase"`
	CleanupRequired bool   `json:"cleanup_required" gorm:"column:cleanup_required"`
	ErrorMessage    string `json:"error_message" gorm:"column:error_message"`
}

func (TemplateReplica) TableName() string {
	return constants.TemplateReplicaTableName
}
