// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package templatecenter

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	cubeboxv1 "github.com/tencentcloud/CubeSandbox/CubeMaster/api/services/cubebox/v1"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/db/models"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/node"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/cubelet"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/localcache"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox"
	sandboxtypes "github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"gorm.io/gorm"
)

var ErrTemplateInUse = errors.New("template is still in use")
var ErrTemplateCleanupLocatorMissing = errors.New("template cleanup locator is missing")

var (
	cleanupTemplateOnCubelet = cubelet.CleanupTemplate
	deleteImageOnCubelet     = cubelet.DeleteImage
	getCubeletAddrForDelete  = cubelet.GetCubeletAddr
	listTemplateSandboxes    = sandbox.ListSandbox
	runReplicaCleanup        = cleanupTemplateReplicasWithLocators
	runArtifactCleanup       = cleanupTemplateArtifact
	runMetadataCleanup       = cleanupTemplateMetadata
	runTemplateJobCleanup    = cleanupTemplateJobs
)

type templateCleanupLocator struct {
	NodeID       string
	NodeIP       string
	SnapshotPath string
}

type templateCleanupTargets struct {
	Definition   *models.TemplateDefinition
	Replicas     []models.TemplateReplica
	Jobs         []models.TemplateImageJob
	Locators     []templateCleanupLocator
	ArtifactIDs  map[string]struct{}
	InstanceType string
}

func DeleteTemplate(ctx context.Context, templateID, instanceType string) error {
	if !isReady() {
		return ErrTemplateStoreNotInitialized
	}
	return withTemplateWriteLock(templateID, func() error {
		targets, err := discoverTemplateCleanupTargets(ctx, templateID, instanceType)
		if err != nil {
			return err
		}
		return deleteTemplateWithTargets(ctx, templateID, targets)
	})
}

func deleteTemplateWithTargets(ctx context.Context, templateID string, targets *templateCleanupTargets) error {
	if !targets.hasCleanupState() {
		return ErrTemplateNotFound
	}
	if targets.hasActiveJob() {
		return fmt.Errorf("%w: template %s deletion is blocked while a build job is still active", ErrTemplateAttemptInProgress, templateID)
	}
	if targets.hasActiveDefinitionBuild() {
		return fmt.Errorf("%w: template %s deletion is blocked while definition creation is still active", ErrTemplateAttemptInProgress, templateID)
	}
	if targets.requiresCleanupLocator() {
		return fmt.Errorf("%w: template %s has historical cleanup state but no node locator", ErrTemplateCleanupLocatorMissing, templateID)
	}
	if targets.shouldCheckInUse() {
		inUse, err := isTemplateInUse(ctx, templateID, targets.InstanceType)
		if err != nil {
			return err
		}
		if inUse {
			return ErrTemplateInUse
		}
	}
	if err := runReplicaCleanup(ctx, templateID, targets.Locators); err != nil {
		return err
	}
	if err := runArtifactCleanup(ctx, templateID, targets); err != nil {
		return err
	}
	if err := runMetadataCleanup(ctx, templateID); err != nil {
		invalidateTemplateCaches(templateID)
		return err
	}
	invalidateTemplateCaches(templateID)
	if err := runTemplateJobCleanup(ctx, templateID); err != nil {
		return err
	}
	return nil
}

func discoverTemplateCleanupTargets(ctx context.Context, templateID, instanceType string) (*templateCleanupTargets, error) {
	targets := &templateCleanupTargets{
		ArtifactIDs: make(map[string]struct{}),
	}

	def, err := GetDefinition(ctx, templateID)
	switch {
	case err == nil:
		targets.Definition = def
		if instanceType == "" {
			instanceType = def.InstanceType
		}
	case errors.Is(err, ErrTemplateNotFound):
	default:
		return nil, err
	}

	replicas, err := ListReplicas(ctx, templateID)
	if err != nil {
		return nil, err
	}
	targets.Replicas = replicas
	for _, replica := range replicas {
		if replica.ArtifactID != "" {
			targets.ArtifactIDs[replica.ArtifactID] = struct{}{}
		}
		targets.addLocator(templateCleanupLocator{
			NodeID:       replica.NodeID,
			NodeIP:       replica.NodeIP,
			SnapshotPath: replica.SnapshotPath,
		})
	}

	jobs, err := listTemplateImageJobsByTemplateID(ctx, templateID)
	if err != nil {
		return nil, err
	}
	targets.Jobs = jobs
	for _, job := range jobs {
		if instanceType == "" && job.InstanceType != "" {
			instanceType = job.InstanceType
		}
		if job.ArtifactID != "" {
			targets.ArtifactIDs[job.ArtifactID] = struct{}{}
		}
		targets.addLocator(templateCleanupLocator{
			NodeID:       job.NodeID,
			NodeIP:       job.NodeIP,
			SnapshotPath: job.SnapshotPath,
		})
	}

	if instanceType == "" {
		instanceType = cubeboxv1.InstanceType_cubebox.String()
	}
	targets.InstanceType = instanceType
	return targets, nil
}

func (t *templateCleanupTargets) addLocator(locator templateCleanupLocator) {
	if strings.TrimSpace(locator.NodeID) == "" && strings.TrimSpace(locator.NodeIP) == "" && strings.TrimSpace(locator.SnapshotPath) == "" {
		return
	}
	key := strings.Join([]string{
		strings.TrimSpace(locator.NodeID),
		strings.TrimSpace(locator.NodeIP),
		strings.TrimSpace(locator.SnapshotPath),
	}, "|")
	for _, existing := range t.Locators {
		existingKey := strings.Join([]string{
			strings.TrimSpace(existing.NodeID),
			strings.TrimSpace(existing.NodeIP),
			strings.TrimSpace(existing.SnapshotPath),
		}, "|")
		if existingKey == key {
			return
		}
	}
	t.Locators = append(t.Locators, locator)
}

func (t *templateCleanupTargets) hasCleanupState() bool {
	return t != nil && (t.Definition != nil || len(t.Replicas) > 0 || len(t.Jobs) > 0)
}

func (t *templateCleanupTargets) hasActiveJob() bool {
	if t == nil {
		return false
	}
	for _, job := range t.Jobs {
		if strings.EqualFold(job.Status, JobStatusPending) || strings.EqualFold(job.Status, JobStatusRunning) {
			return true
		}
	}
	return false
}

func (t *templateCleanupTargets) hasActiveDefinitionBuild() bool {
	if t == nil || t.Definition == nil {
		return false
	}
	return strings.EqualFold(t.Definition.Status, StatusPending)
}

func (t *templateCleanupTargets) requiresCleanupLocator() bool {
	if t == nil {
		return false
	}
	if len(t.Locators) > 0 || t.Definition != nil || len(t.Replicas) > 0 || len(t.ArtifactIDs) > 0 {
		return false
	}
	// Only block deletion if there is a job that carries a snapshot path but
	// has no resolvable node locator. Jobs with empty NodeID/NodeIP/SnapshotPath
	// are orphaned records with nothing to clean up on any cubelet, so they
	// can be safely removed without a locator.
	for _, job := range t.Jobs {
		if strings.TrimSpace(job.SnapshotPath) != "" {
			return true
		}
	}
	return false
}

func (t *templateCleanupTargets) shouldCheckInUse() bool {
	if t == nil || t.Definition == nil {
		return false
	}
	return !strings.EqualFold(t.Definition.Status, StatusFailed)
}

func cleanupTemplateMetadata(ctx context.Context, templateID string) error {
	var cleanupErr error
	if err := store.db.WithContext(ctx).Unscoped().Table(constants.TemplateReplicaTableName).
		Where("template_id = ?", templateID).Delete(&models.TemplateReplica{}).Error; err != nil {
		cleanupErr = errors.Join(cleanupErr, err)
	}
	if err := store.db.WithContext(ctx).Unscoped().Table(constants.TemplateDefinitionTableName).
		Where("template_id = ?", templateID).Delete(&models.TemplateDefinition{}).Error; err != nil {
		cleanupErr = errors.Join(cleanupErr, err)
	}
	return cleanupErr
}

func cleanupTemplateJobs(ctx context.Context, templateID string) error {
	return store.db.WithContext(ctx).Unscoped().Table(constants.TemplateImageJobTableName).
		Where("template_id = ?", templateID).Delete(&models.TemplateImageJob{}).Error
}

func cleanupTemplateArtifact(ctx context.Context, templateID string, targets *templateCleanupTargets) error {
	if targets == nil || len(targets.ArtifactIDs) == 0 {
		return nil
	}
	var cleanupErr error
	for artifactID := range targets.ArtifactIDs {
		artifactTargets := resolveArtifactCleanupNodes(targets, artifactID)
		if len(artifactTargets) == 0 {
			artifactTargets = healthyTemplateNodes(targets.InstanceType)
		}
		distributionErr := cleanupArtifactOnNodes(ctx, artifactID, artifactTargets)
		artifact, lookupErr := getRootfsArtifactByID(ctx, artifactID)
		if lookupErr != nil && !errors.Is(lookupErr, gorm.ErrRecordNotFound) {
			cleanupErr = errors.Join(cleanupErr, lookupErr)
		}
		var artifactCleanupErr error
		if lookupErr == nil {
			if artifact.Ext4Path != "" {
				if err := cleanupLocalRootfsArtifact(artifact.ArtifactID, artifact.Ext4Path); err != nil {
					artifactCleanupErr = errors.Join(artifactCleanupErr, err)
				}
			}
			if distributionErr == nil && artifactCleanupErr == nil {
				if err := store.db.WithContext(ctx).Unscoped().Table(constants.RootfsArtifactTableName).
					Where("artifact_id = ?", artifactID).Delete(&models.RootfsArtifact{}).Error; err != nil {
					artifactCleanupErr = errors.Join(artifactCleanupErr, err)
				}
			}
		}
		cleanupErr = errors.Join(cleanupErr, distributionErr, artifactCleanupErr)
	}
	return cleanupErr
}

func resolveArtifactCleanupNodes(targets *templateCleanupTargets, artifactID string) []*node.Node {
	if targets == nil || strings.TrimSpace(artifactID) == "" {
		return nil
	}
	out := make([]*node.Node, 0)
	seen := make(map[string]struct{})
	appendNode := func(nodeID, nodeIP string) {
		nodeID = strings.TrimSpace(nodeID)
		nodeIP = strings.TrimSpace(nodeIP)
		if nodeIP == "" && nodeID != "" {
			if cachedNode, ok := localcache.GetNode(nodeID); ok && cachedNode != nil {
				nodeIP = strings.TrimSpace(cachedNode.HostIP())
				if nodeID == "" {
					nodeID = strings.TrimSpace(cachedNode.ID())
				}
			}
		}
		if nodeIP == "" {
			return
		}
		key := nodeID + "|" + nodeIP
		if _, ok := seen[key]; ok {
			return
		}
		out = append(out, &node.Node{
			InsID: nodeID,
			IP:    nodeIP,
		})
		seen[key] = struct{}{}
	}
	for _, replica := range targets.Replicas {
		if strings.TrimSpace(replica.ArtifactID) != artifactID {
			continue
		}
		appendNode(replica.NodeID, replica.NodeIP)
	}
	for _, job := range targets.Jobs {
		if strings.TrimSpace(job.ArtifactID) != artifactID {
			continue
		}
		appendNode(job.NodeID, job.NodeIP)
	}
	return out
}

func cleanupDistributedArtifact(ctx context.Context, artifactID, instanceType string) error {
	return cleanupArtifactOnNodes(ctx, artifactID, healthyTemplateNodes(instanceType))
}

func cleanupTemplateReplicas(ctx context.Context, templateID string) error {
	targets, err := discoverTemplateCleanupTargets(ctx, templateID, "")
	if err != nil {
		return err
	}
	return cleanupTemplateReplicasWithLocators(ctx, templateID, targets.Locators)
}

func cleanupTemplateReplicasWithLocators(ctx context.Context, templateID string, locators []templateCleanupLocator) error {
	var cleanupErr error
	for _, locator := range locators {
		hostIP := locator.NodeIP
		if hostIP == "" && locator.NodeID != "" {
			if n, ok := localcache.GetNode(locator.NodeID); ok && n != nil {
				hostIP = n.HostIP()
			}
		}
		if hostIP == "" {
			cleanupErr = errors.Join(cleanupErr, fmt.Errorf("cleanup template %s: missing node address for locator node=%s snapshot=%s", templateID, locator.NodeID, locator.SnapshotPath))
			continue
		}
		rsp, err := cleanupTemplateOnCubelet(ctx, getCubeletAddrForDelete(hostIP), &cubeboxv1.CleanupTemplateRequest{
			RequestID:    uuid.NewString(),
			TemplateID:   templateID,
			SnapshotPath: locator.SnapshotPath,
		})
		if err != nil {
			if isIgnorableTemplateCleanupError(err) {
				continue
			}
			cleanupErr = errors.Join(cleanupErr, fmt.Errorf("cleanup template %s on node %s: %w", templateID, locator.NodeID, err))
			continue
		}
		if rsp.GetRet() != nil && int(rsp.GetRet().GetRetCode()) != int(errorcode.ErrorCode_Success) {
			if isIgnorableTemplateCleanupMessage(rsp.GetRet().GetRetMsg()) {
				continue
			}
			cleanupErr = errors.Join(cleanupErr, fmt.Errorf("cleanup template %s on node %s failed: %s", templateID, locator.NodeID, rsp.GetRet().GetRetMsg()))
		}
	}
	return cleanupErr
}

func isIgnorableTemplateCleanupError(err error) bool {
	if err == nil {
		return false
	}
	return isIgnorableTemplateCleanupMessage(err.Error())
}

func isIgnorableTemplateCleanupMessage(msg string) bool {
	msg = strings.ToLower(strings.TrimSpace(msg))
	if msg == "" {
		return false
	}
	if strings.Contains(msg, "no such file") {
		return true
	}
	hasMissing := strings.Contains(msg, "not found") || strings.Contains(msg, "not exist") || strings.Contains(msg, "does not exist")
	hasTemplatePath := strings.Contains(msg, "snapshot") || strings.Contains(msg, "template") || strings.Contains(msg, "path") || strings.Contains(msg, "directory") || strings.Contains(msg, "file")
	return hasMissing && hasTemplatePath
}

func isIgnorableArtifactDeleteError(err error) bool {
	if err == nil {
		return false
	}
	return isIgnorableArtifactDeleteMessage(err.Error())
}

func isIgnorableArtifactDeleteMessage(msg string) bool {
	msg = strings.ToLower(strings.TrimSpace(msg))
	if msg == "" {
		return false
	}
	if strings.Contains(msg, "no such image") {
		return true
	}
	hasMissing := strings.Contains(msg, "not found") || strings.Contains(msg, "not exist") || strings.Contains(msg, "does not exist")
	hasImage := strings.Contains(msg, "image")
	return hasMissing && hasImage
}

func isTemplateInUse(ctx context.Context, templateID, instanceType string) (bool, error) {
	nodeCount := localcache.GetHealthyNodesByInstanceType(-1, instanceType).Len()
	if nodeCount == 0 {
		return false, nil
	}
	rsp := listTemplateSandboxes(ctx, &sandboxtypes.ListCubeSandboxReq{
		RequestID:    uuid.New().String(),
		InstanceType: instanceType,
		StartIdx:     1,
		Size:         nodeCount,
	})
	if rsp == nil || rsp.Ret == nil {
		return false, errors.New("list sandbox returned empty response")
	}
	if rsp.Ret.RetCode != int(errorcode.ErrorCode_Success) {
		return false, fmt.Errorf("list sandbox failed: %s", rsp.Ret.RetMsg)
	}
	for _, item := range rsp.Data {
		if item == nil {
			continue
		}
		if item.TemplateID == templateID {
			return true, nil
		}
		if item.Labels[constants.CubeAnnotationAppSnapshotTemplateID] == templateID {
			return true, nil
		}
	}
	return false, nil
}
