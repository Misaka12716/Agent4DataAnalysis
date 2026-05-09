CREATE TABLE IF NOT EXISTS `t_cube_host_info` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `ins_id` varchar(64) NOT NULL DEFAULT '',
  `ip` varchar(64) NOT NULL DEFAULT '',
  `region` varchar(64) NOT NULL DEFAULT 'local',
  `zone` varchar(64) NOT NULL DEFAULT 'local-a',
  `uuid` varchar(64) NOT NULL DEFAULT '',
  `instance_type` varchar(64) NOT NULL DEFAULT 'cubebox',
  `cube_cluster_label` varchar(64) NOT NULL DEFAULT 'default',
  `oss_cluster_label` varchar(64) NOT NULL DEFAULT '',
  `host_status` varchar(32) NOT NULL DEFAULT 'RUNNING',
  `live_status` varchar(32) NOT NULL DEFAULT 'LIVE',
  `quota_cpu` bigint NOT NULL DEFAULT 0,
  `quota_mem_mb` bigint NOT NULL DEFAULT 0,
  `cpu_total` bigint NOT NULL DEFAULT 0,
  `mem_mb_total` bigint NOT NULL DEFAULT 0,
  `data_disk_gb` bigint NOT NULL DEFAULT 0,
  `sys_disk_gb` bigint NOT NULL DEFAULT 0,
  `create_concurrent_num` bigint NOT NULL DEFAULT 0,
  `max_mvm_num` bigint NOT NULL DEFAULT 0,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `deleted_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_host_info_ins_id` (`ins_id`),
  KEY `idx_host_info_ip` (`ip`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `t_cube_host_type` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `instance_type` varchar(128) NOT NULL DEFAULT '',
  `cpu_type` varchar(64) NOT NULL DEFAULT 'INTEL',
  `gpu_info` varchar(1024) NOT NULL DEFAULT '',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `deleted_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_host_type_instance_type` (`instance_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `t_cube_sub_host_info` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `ins_id` varchar(64) NOT NULL DEFAULT '',
  `host_ip` varchar(64) NOT NULL DEFAULT '',
  `device_class` varchar(64) NOT NULL DEFAULT '',
  `device_id` bigint NOT NULL DEFAULT 0,
  `instance_family` varchar(64) NOT NULL DEFAULT '',
  `dedicated_cluster_id` varchar(64) NOT NULL DEFAULT '',
  `virtual_node_quota` varchar(255) NOT NULL DEFAULT '[]',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `deleted_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sub_host_info_ins_id` (`ins_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
