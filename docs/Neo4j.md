# Neo4j 使用指南


## 一、启动与停止服务
通过以下命令控制 Neo4j 服务的启动和停止：
```bash
# 启动 Neo4j
sudo systemctl start neo4j

# 查看 Neo4j 状态
sudo systemctl status neo4j

# 停止 Neo4j
sudo systemctl stop neo4j
```


## 二、可视化查看说明
由于服务器通常无法直接打开可视化网页，建议将 Neo4j 数据备份为 dump 文件后，在本地环境进行可视化查看。


## 三、服务器端备份操作
在服务器上执行以下步骤，将 Neo4j 数据备份为 dump 文件：

1. **确保备份目标目录存在**  
   ```bash
   sudo mkdir -p /data/agent_platform/neo4j-backup/
   ```

2. **设置目录权限（避免权限问题）**  
   将目录所有者改为 `neo4j` 用户，确保后续备份操作有权限执行：  
   ```bash
   sudo chown -R neo4j:neo4j /data/agent_platform/neo4j-backup
   ```

3. **执行备份**  
   以 `neo4j` 用户身份执行备份命令（`--overwrite-destination=true` 表示覆盖已有文件）：  
   ```bash
   sudo -u neo4j neo4j-admin database dump neo4j --to-path /data/agent_platform/neo4j-backup --overwrite-destination=true
   ```


## 四、本地加载备份文件
将服务器上的 dump 文件复制到本地后，通过以下命令加载到本地 Neo4j 中：  
```bash
neo4j-admin database load --from-path="E:\Desktop" --overwrite-destination=true neo4j
```

> 注意事项：
> - 引号内的路径需替换为本地 dump 文件所在的**目录**（仅目录，不包含文件名）。
> - 命令末尾的 `neo4j` 为目标数据库名称，初始数据库默认名为 `neo4j`，可根据实际需求修改。


mysql -u root -p
88888888