// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cubebox

import (
	"k8s.io/apimachinery/pkg/api/resource"

	"github.com/tencentcloud/CubeSandbox/Cubelet/api/services/cubebox/v1"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/config"
	"github.com/tencentcloud/CubeSandbox/Cubelet/pkg/constants"
	metrictype "github.com/tencentcloud/CubeSandbox/Cubelet/plugins/cube/internals/metric/types"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

func (l *local) RegisterMetrics(register *metrictype.CollectRegister) error {
	register.AddCollector(metrictype.MetricTypeCLS, func() (any, error) {
		var traces []*CubeLog.RequestTrace
		sbs := l.cubeboxManger.List()
		traces = append(traces, &CubeLog.RequestTrace{
			Action:  "MvmTotal",
			Callee:  constants.CubeboxID.ID(),
			RetCode: int64(len(sbs)),
		})

		traces = append(traces, &CubeLog.RequestTrace{
			Action:  "MvmDead",
			Callee:  constants.CubeboxID.ID(),
			RetCode: int64(deadContainerCount),
		})

		hostConf := config.GetHostConf()

		allocatePercent := -1
		if hostConf.Quota.MvmLimit > 0 {
			allocatePercent = len(sbs) * 100 / hostConf.Quota.MvmLimit
		}

		traces = append(traces, &CubeLog.RequestTrace{
			Action:  "MvmAllocatePercent",
			Callee:  constants.CubeboxID.ID(),
			RetCode: int64(allocatePercent),
		})

		cpuUsage := resource.MustParse("0")
		memUsage := resource.MustParse("0")
		nicQueues := int64(0)
		for _, sb := range sbs {
			if sb.GetStatus() == nil || !isContainerInGoodState(sb.GetStatus().Get().State()) {
				continue
			}

			if sb.ResourceWithOverHead != nil {
				cpuUsage.Add(sb.ResourceWithOverHead.HostCpuQ)
				memUsage.Add(sb.ResourceWithOverHead.HostMemQ)
			}
			nicQueues += sb.Queues
		}

		if cpuQuota := hostConf.Quota.Cpu; cpuQuota > 0 {
			cpuRate := float64(cpuUsage.MilliValue()) / float64(cpuQuota) * 100
			traces = append(traces, &CubeLog.RequestTrace{
				Action:  "CpuUsagePercent",
				Callee:  constants.CgroupID.ID(),
				RetCode: int64(cpuRate),
			})
		}

		memQuota, err := resource.ParseQuantity(hostConf.Quota.Mem)
		if err == nil {
			memRate := float64(memUsage.Value()) / float64(memQuota.Value()) * 100
			traces = append(traces, &CubeLog.RequestTrace{
				Action:  "MemUsagePercent",
				Callee:  constants.CgroupID.ID(),
				RetCode: int64(memRate),
			})
		}

		traces = append(traces, &CubeLog.RequestTrace{
			Action:  "NicQueues",
			Callee:  constants.CubeboxID.ID(),
			RetCode: nicQueues,
		})
		return traces, nil
	})
	register.AddCollector(metrictype.MetricTypeOSS, func() (any, error) {
		return l.collectOSSMetrics(), nil
	})
	return nil
}

func (l *local) collectOSSMetrics() map[string]any {
	cpuUsage := resource.MustParse("0")
	memUsage := resource.MustParse("0")
	sbs := l.cubeboxManger.List()
	runningBox := 0
	nicQueues := int64(0)
	for _, sb := range sbs {
		if sb.GetStatus() == nil || !isContainerInGoodState(sb.GetStatus().Get().State()) {
			continue
		}
		runningBox++

		if sb.ResourceWithOverHead != nil {
			cpuUsage.Add(sb.ResourceWithOverHead.HostCpuQ)
			memUsage.Add(sb.ResourceWithOverHead.HostMemQ)
		}
		nicQueues += sb.Queues
	}

	return map[string]any{
		"quota_cpu_usage":    int(cpuUsage.MilliValue()),
		"quota_mem_mb_usage": memUsage.Value() / 1024 / 1024,
		"mvm_num":            len(sbs),
		"mvm_running_num":    runningBox,
		"nic_queues":         nicQueues,
	}
}

func isContainerInGoodState(state cubebox.ContainerState) bool {
	if state == cubebox.ContainerState_CONTAINER_RUNNING ||
		state == cubebox.ContainerState_CONTAINER_PAUSED ||
		state == cubebox.ContainerState_CONTAINER_CREATED ||
		state == cubebox.ContainerState_CONTAINER_PAUSING {
		return true
	}
	return false
}
