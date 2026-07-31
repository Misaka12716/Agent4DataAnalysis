# db/workbench_schema.py — 2.2.10 分析项目与 run 追溯

TABLE_WORKBENCH_PROJECTS = "workbench_projects"
TABLE_WORKBENCH_RUNS = "workbench_runs"
TABLE_WORKBENCH_STEPS = "workbench_run_steps"
TABLE_WORKBENCH_EXPORTS = "workbench_exports"

DDL_WORKBENCH_PROJECTS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_WORKBENCH_PROJECTS} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    session_id VARCHAR(128) NOT NULL,
    project_name VARCHAR(256) NOT NULL,
    description TEXT,
    data_file VARCHAR(512),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_session (user_id, session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

DDL_WORKBENCH_RUNS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_WORKBENCH_RUNS} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL UNIQUE,
    project_id INT,
    session_id VARCHAR(128) NOT NULL,
    user_id INT NOT NULL,
    task_text TEXT NOT NULL,
    route VARCHAR(32) DEFAULT 'workbench',
    status VARCHAR(32) DEFAULT 'pending',
    step_count INT DEFAULT 0,
    manifest_path VARCHAR(512),
    summary TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP NULL,
    INDEX idx_session (session_id),
    INDEX idx_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

DDL_WORKBENCH_STEPS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_WORKBENCH_STEPS} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL,
    step_index INT NOT NULL,
    solver_id VARCHAR(128),
    step_name VARCHAR(256),
    status VARCHAR(32) DEFAULT 'pending',
    artifact_path VARCHAR(512),
    chart_paths JSON,
    message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_run_step (run_id, step_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


DDL_WORKBENCH_EXPORTS = f"""
CREATE TABLE IF NOT EXISTS {TABLE_WORKBENCH_EXPORTS} (
    id INT AUTO_INCREMENT PRIMARY KEY,
    export_id VARCHAR(64) NOT NULL UNIQUE,
    session_id VARCHAR(128) NOT NULL,
    user_id INT NOT NULL,
    run_id VARCHAR(64),
    kind VARCHAR(64) NOT NULL,
    artifact_path VARCHAR(512),
    note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_export_session (session_id),
    INDEX idx_export_run (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def ensure_workbench_tables(mysql_handler) -> None:
    for ddl in (DDL_WORKBENCH_PROJECTS, DDL_WORKBENCH_RUNS, DDL_WORKBENCH_STEPS, DDL_WORKBENCH_EXPORTS):
        mysql_handler.execute(ddl)
