// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package localcache

import (
	"context"
	"errors"
	"time"

	"github.com/gomodule/redigo/redis"
	"github.com/google/uuid"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/node"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/recov"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/types"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/utils"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/wrapredis"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

type RedisNodeInfo struct {
	InsID string `json:"InstanceID" redis:"ins_id"`

	QuotaCpuUsage int64 `json:"QuotaCpuUsage" redis:"quota_cpu_usage"`

	QuotaMemUsage int64 `json:"QuotaMemUsage" redis:"quota_mem_mb_usage"`

	CpuUtil float64 `json:"CpuUtil" redis:"cpu_util"`

	CpuLoadUsage float64 `json:"CpuLoadUsage" redis:"cpu_load_usage"`

	MemUsage int64 `json:"MemUsage" redis:"mem_load_mb_usage"`

	DataDiskUsagePer    float64 `json:"DataDiskUsagePer" redis:"data_disk_usage_per"`
	StorageDiskUsagePer float64 `json:"StorageDiskUsagePer" redis:"storage_disk_usage_per"`
	SysDiskUsagePer     float64 `json:"SysDiskUsagePer" redis:"sys_disk_usage_per"`

	MvmNum int64 `json:"mvm_num" redis:"mvm_num"`

	MetricUpdate string `json:"MetricUpdateAt" redis:"update_at"`

	RealTimeCreateNum int64 `json:"RealTimeCreateNum,omitempty" redis:"realtime_create_num"`

	NICQueues int64 `json:"nic_queues,omitempty" redis:"nic_queues"`
}

func (l *local) loadMetricFromRedis() error {
	elems := l.cache.Items()
	for k := range elems {
		if tmpNode, found, err := l.getNodeMetricFromRedis(context.Background(), k); found && err == nil {
			if err := l.updateNodeMetric(tmpNode); err != nil {

				CubeLog.WithContext(context.Background()).Warnf("updateMetric fail:%v", err)
			}
		} else if err != nil {
			return err
		}
	}
	return nil
}

func (l *local) loopUpdateMetric(ctx context.Context) {
	ticker := time.NewTicker(50 * time.Millisecond)
	defer ticker.Stop()

	checkDeadline := time.Now().Add(config.GetConfig().Common.SyncMetricDataInterval)
	for {
		select {
		case <-ticker.C:
			recov.WithRecover(func() {
				if checkDeadline.After(time.Now()) {

					return
				}
				defer func() {
					checkDeadline = time.Now().Add(config.GetConfig().Common.SyncMetricDataInterval)
				}()
				ctx = context.WithValue(ctx, CubeLog.KeyRequestID, uuid.New().String())
				elems := l.cache.Items()
				for k := range elems {
					if tmpNode, found, err := l.getNodeMetricFromRedis(ctx, k); found && err == nil {
						if err := l.updateNodeMetric(tmpNode); err != nil {

							CubeLog.WithContext(context.Background()).Fatalf("updateMetric fail:%v", err)
						}
					}
				}
			}, func(panicError interface{}) {
				checkDeadline = time.Now().Add(config.GetConfig().Common.SyncMetricDataInterval)
				CubeLog.WithContext(context.Background()).Fatalf("loopUpdateMetric panic:%v", panicError)
			})
		case <-ctx.Done():
			return
		}
	}
}

func (l *local) getNodeMetricFromRedis(ctx context.Context, key string) (*node.Node, bool, error) {
	values, err := redis.Values(wrapredis.GetRedis(wrapredis.RedisRead).Do("HGETALL", key))
	if err != nil {
		CubeLog.WithContext(ctx).Fatalf("getNodeMetricFromRedis %s err:%s", key, err)
		return nil, false, err
	}
	if len(values) == 0 {
		CubeLog.WithContext(ctx).Warnf("redis hgetall empty, key: %s", key)
		return nil, false, nil
	}

	redisNode := &RedisNodeInfo{}
	if err := redis.ScanStruct(values, redisNode); err != nil {
		CubeLog.WithContext(ctx).Errorf("redis scanStruct error, key: %s, err: %s,values:%v", key, err, values)
		return nil, true, err
	}
	n := &node.Node{}
	n.InsID = redisNode.InsID
	n.QuotaCpuUsage = redisNode.QuotaCpuUsage
	n.QuotaMemUsage = redisNode.QuotaMemUsage
	n.CpuUtil = redisNode.CpuUtil
	n.CpuLoadUsage = redisNode.CpuLoadUsage
	n.MemUsage = redisNode.MemUsage
	n.DataDiskUsagePer = redisNode.DataDiskUsagePer
	n.StorageDiskUsagePer = redisNode.StorageDiskUsagePer
	n.SysDiskUsagePer = redisNode.SysDiskUsagePer
	n.MvmNum = redisNode.MvmNum
	n.MetricUpdate.UnmarshalText([]byte(redisNode.MetricUpdate))
	n.RealTimeCreateNum = redisNode.RealTimeCreateNum
	if log.IsDebug() {
		CubeLog.WithContext(ctx).Debugf("getNodeMetricFromRedis:%+v", utils.InterfaceToString(redisNode))
	}
	return n, true, nil
}

func (l *local) getByPassProsyFromRedis(ctx context.Context, key string) (*types.SandboxProxyMap, error) {
	mapvalues, err := redis.StringMap(wrapredis.GetRedis(wrapredis.RedisRead).Do("HGETALL", key))
	if err != nil {
		if errors.Is(err, redis.ErrNil) {
			log.G(ctx).Debugf("no such key in redis:%s", key)
			return nil, nil
		}
		log.G(ctx).Warnf("getByPassProsyFromRedis %s err:%s", key, err)
		return nil, err
	}
	log.G(ctx).Debugf("getByPassProsyFromRedis:%s", key)
	if len(mapvalues) == 0 {
		log.G(ctx).Warnf("redis get empty, key: %s", key)
		return nil, nil
	}

	nodeIdIp := &types.SandboxProxyMap{}
	if ip, ok := mapvalues["HostIP"]; ok {
		nodeIdIp.HostIP = ip
		delete(mapvalues, "HostIP")
	} else {
		log.G(ctx).Warnf("redis get empty, key: %s", key)
		return nil, errors.New("get empty HostIP")
	}
	if createAt, ok := mapvalues["CreatedAt"]; ok {
		nodeIdIp.CreatedAt = createAt
		delete(mapvalues, "CreatedAt")
	}
	if sandboxIP, ok := mapvalues["SandboxIP"]; ok {
		nodeIdIp.SandboxIP = sandboxIP
		delete(mapvalues, "SandboxIP")
	}

	if len(mapvalues) == 0 {
		log.G(ctx).Warnf("key: %s,has no ContainerToHostPorts", key)
		return nodeIdIp, nil
	}
	nodeIdIp.ContainerToHostPorts = mapvalues
	if log.IsDebug() {
		log.G(ctx).Debugf("getByPassProsyFromRedis:%+v", nodeIdIp)
	}
	return nodeIdIp, nil
}

func (l *local) getInsInfoFromRedis(ctx context.Context, key string) (*types.InstanceInfoMap, error) {
	values, err := redis.Values(wrapredis.GetRedis(wrapredis.RedisRead).Do("HGETALL", key))
	if err != nil {
		if errors.Is(err, redis.ErrNil) {
			log.G(ctx).Debugf("no such key in redis:%s", key)
			return nil, nil
		}
		CubeLog.WithContext(ctx).Fatalf("getInsInfoFromRedis %s err:%s", key, err)
		return nil, err
	}
	if len(values) == 0 {
		CubeLog.WithContext(ctx).Warnf("redis hgetall empty, key: %s", key)
		return nil, nil
	}

	redisIns := &types.InstanceInfoMap{}
	if err := redis.ScanStruct(values, redisIns); err != nil {
		CubeLog.WithContext(ctx).Errorf("redis scanStruct error, key: %s, err: %s,values:%v", key, err, values)
		return nil, err
	}
	return redisIns, nil
}

func (l *local) setInstanceInfoMapToRedis(ctx context.Context, key string, info *types.InstanceInfoMap) (err error) {
	start := time.Now()
	defer traceRedis(ctx, "Create", "HSET", key, start, err)
	_, err = wrapredis.GetRedis(wrapredis.RedisWrite).Do("HSET", redis.Args{key}.AddFlat(info)...)
	if err != nil {
		log.G(ctx).Errorf("redis set error, key: %s, err: %s", key, err)
		return err
	}
	if log.IsDebug() {
		log.G(ctx).Debugf("setInstanceInfoMapToRedis:%s:%s", key, utils.InterfaceToString(info))
	}
	return nil
}

func (l *local) setByPassProsyToRedis(ctx context.Context, key string, byPassProsy *types.SandboxProxyMap) (err error) {
	start := time.Now()
	defer traceRedis(ctx, "Create", "HSET", key, start, err)

	fieldValues := []interface{}{
		"HostIP", byPassProsy.HostIP,
		"CreatedAt", byPassProsy.CreatedAt,
	}
	if byPassProsy.SandboxIP != "" {
		fieldValues = append(fieldValues, "SandboxIP", byPassProsy.SandboxIP)
	}
	for k, v := range byPassProsy.ContainerToHostPorts {
		fieldValues = append(fieldValues, k, v)
	}
	_, err = wrapredis.GetRedis(wrapredis.RedisWrite).Do("HSET", redis.Args{key}.AddFlat(fieldValues)...)
	if err != nil {
		log.G(ctx).Errorf("redis set error, key: %s, err: %s", key, err)
		return err
	}
	if log.IsDebug() {
		log.G(ctx).Debugf("setByPassProsyToRedis:%s,%s", key, fieldValues)
	}
	return nil
}

func (l *local) setDescribeTaskToRedis(ctx context.Context, key string, taskInfo *types.DescribeTaskMap) (err error) {
	start := time.Now()
	conn := wrapredis.GetRedis(wrapredis.RedisWrite)
	defer traceRedis(ctx, "Create", "HSET", key, start, err)
	defer func() {
		if err == nil {
			_, err := conn.Do("EXPIRE", key, config.GetConfig().Common.DescribeTaskExpireTime)
			if err != nil {
				log.G(ctx).Errorf("redis EXPIRE error, key: %s, err: %s", key, err)
			}
		}
	}()
	_, err = conn.Do("HSET", redis.Args{key}.AddFlat(taskInfo)...)
	if err != nil {
		log.G(ctx).Errorf("redis set error, key: %s, err: %s", key, err)
		return err
	}
	if log.IsDebug() {
		log.G(ctx).Debugf("setDescribeTaskToRedis:%s:%s", key, utils.InterfaceToString(taskInfo))
	}
	return nil
}

func (l *local) getDescribeTaskFromRedis(ctx context.Context, key string) (*types.DescribeTaskMap, error) {
	values, err := redis.Values(wrapredis.GetRedis(wrapredis.RedisRead).Do("HGETALL", key))
	if err != nil {
		if errors.Is(err, redis.ErrNil) {
			log.G(ctx).Debugf("no such key in redis:%s", key)
			return nil, nil
		}
		CubeLog.WithContext(ctx).Fatalf("getDescribeTaskFromRedis %s err:%s", key, err)
		return nil, err
	}
	if len(values) == 0 {
		CubeLog.WithContext(ctx).Warnf("redis hgetall empty, key: %s", key)
		return nil, nil
	}

	taskInfo := &types.DescribeTaskMap{}
	if err := redis.ScanStruct(values, taskInfo); err != nil {
		CubeLog.WithContext(ctx).Errorf("redis scanStruct error, key: %s, err: %s,values:%v", key, err, values)
		return nil, err
	}
	return taskInfo, nil
}

func (l *local) deleteKeyFromRedis(ctx context.Context, key string) (err error) {
	start := time.Now()
	defer traceRedis(ctx, "Delete", "DEL", key, start, err)
	_, err = wrapredis.GetRedis(wrapredis.RedisWrite).Do("DEL", key)
	if err != nil {
		log.G(ctx).Errorf("redis del error, key: %s, err: %s", key, err)
		return err
	}

	return nil
}

func traceRedis(ctx context.Context, action, redisOp, key string, start time.Time, err error) {
	cost := time.Since(start)
	baseRt := CubeLog.GetTraceInfo(ctx).DeepCopy()
	baseRt.Callee = constants.Redis
	baseRt.Action = action
	baseRt.CalleeAction = redisOp
	baseRt.InstanceID = key
	baseRt.Cost = cost
	baseRt.RetCode = int64(errorcode.ErrorCode_Success)
	if err != nil {
		baseRt.RetCode = int64(errorcode.ErrorCode_DBError)
	}
	CubeLog.Trace(baseRt)
}
