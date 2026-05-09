INSERT INTO `t_cube_host_type` (
  `instance_type`,
  `cpu_type`,
  `gpu_info`
) VALUES (
  'cubebox',
  'INTEL',
  ''
)
ON DUPLICATE KEY UPDATE
  `cpu_type` = VALUES(`cpu_type`),
  `gpu_info` = VALUES(`gpu_info`);

INSERT INTO `t_cube_host_info` (
  `ins_id`,
  `ip`,
  `region`,
  `zone`,
  `uuid`,
  `instance_type`,
  `cube_cluster_label`,
  `oss_cluster_label`,
  `host_status`,
  `live_status`,
  `quota_cpu`,
  `quota_mem_mb`,
  `cpu_total`,
  `mem_mb_total`,
  `data_disk_gb`,
  `sys_disk_gb`,
  `create_concurrent_num`,
  `max_mvm_num`
) VALUES (
  'local-ins-1',
  '__CUBE_SANDBOX_NODE_IP__',
  'local',
  'local-a',
  'local-ins-1',
  'cubebox',
  'default',
  '',
  'RUNNING',
  'LIVE',
  8000,
  16384,
  8000,
  16384,
  100,
  50,
  32,
  128
)
ON DUPLICATE KEY UPDATE
  `ip` = VALUES(`ip`),
  `region` = VALUES(`region`),
  `zone` = VALUES(`zone`),
  `uuid` = VALUES(`uuid`),
  `instance_type` = VALUES(`instance_type`),
  `cube_cluster_label` = VALUES(`cube_cluster_label`),
  `oss_cluster_label` = VALUES(`oss_cluster_label`),
  `host_status` = VALUES(`host_status`),
  `live_status` = VALUES(`live_status`),
  `quota_cpu` = VALUES(`quota_cpu`),
  `quota_mem_mb` = VALUES(`quota_mem_mb`),
  `cpu_total` = VALUES(`cpu_total`),
  `mem_mb_total` = VALUES(`mem_mb_total`),
  `data_disk_gb` = VALUES(`data_disk_gb`),
  `sys_disk_gb` = VALUES(`sys_disk_gb`),
  `create_concurrent_num` = VALUES(`create_concurrent_num`),
  `max_mvm_num` = VALUES(`max_mvm_num`);

INSERT INTO `t_cube_sub_host_info` (
  `ins_id`,
  `host_ip`,
  `device_class`,
  `device_id`,
  `instance_family`,
  `dedicated_cluster_id`,
  `virtual_node_quota`
) VALUES (
  'local-ins-1',
  '__CUBE_SANDBOX_NODE_IP__',
  'cpu',
  0,
  'cubebox',
  'single-node',
  '[128]'
)
ON DUPLICATE KEY UPDATE
  `host_ip` = VALUES(`host_ip`),
  `device_class` = VALUES(`device_class`),
  `device_id` = VALUES(`device_id`),
  `instance_family` = VALUES(`instance_family`),
  `dedicated_cluster_id` = VALUES(`dedicated_cluster_id`),
  `virtual_node_quota` = VALUES(`virtual_node_quota`);
