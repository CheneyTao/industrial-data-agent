-- Industrial PdM demo schema for MySQL 8.0+
-- pdm_*: untouched public sample source layer
-- synthetic_*: deterministic demo-derived layer, never field truth

CREATE DATABASE IF NOT EXISTS `industrial_pdm`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE `industrial_pdm`;
SET NAMES utf8mb4;

SET FOREIGN_KEY_CHECKS = 0;
DROP VIEW IF EXISTS `v_work_order_kpi`;
DROP VIEW IF EXISTS `v_monthly_operations_kpi`;
DROP VIEW IF EXISTS `v_machine_reliability`;
DROP VIEW IF EXISTS `v_failure_rca_evidence`;
DROP VIEW IF EXISTS `v_event_timeline`;
DROP VIEW IF EXISTS `v_machine_daily_kpi`;
DROP TABLE IF EXISTS `analysis_reports`;
DROP TABLE IF EXISTS `root_cause_candidates`;
DROP TABLE IF EXISTS `detected_anomalies`;
DROP TABLE IF EXISTS `analysis_step_logs`;
DROP TABLE IF EXISTS `analysis_runs`;
DROP TABLE IF EXISTS `data_sources`;
DROP TABLE IF EXISTS `synthetic_parts_usage`;
DROP TABLE IF EXISTS `synthetic_work_orders`;
DROP TABLE IF EXISTS `synthetic_maintenance_notes`;
DROP TABLE IF EXISTS `synthetic_operator_logs`;
DROP TABLE IF EXISTS `synthetic_fault_reports`;
DROP TABLE IF EXISTS `synthetic_daily_operations`;
DROP TABLE IF EXISTS `pdm_failures`;
DROP TABLE IF EXISTS `pdm_maint`;
DROP TABLE IF EXISTS `pdm_errors`;
DROP TABLE IF EXISTS `pdm_telemetry`;
DROP TABLE IF EXISTS `pdm_machines`;
DROP TABLE IF EXISTS `dim_machine_models`;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE `pdm_machines` (
  `machineID` INT NOT NULL COMMENT '设备标识',
  `model` VARCHAR(32) NOT NULL COMMENT '设备型号',
  `age` INT NOT NULL COMMENT '机龄（年）',
  PRIMARY KEY (`machineID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='公开样例来源层：设备主数据，未由本项目改写';

CREATE TABLE `dim_machine_models` (
  `source_model_code` VARCHAR(32) NOT NULL COMMENT '来源文件中的原始型号编码',
  `display_name_zh` VARCHAR(64) NOT NULL COMMENT '面向用户的中性中文演示别名',
  `display_name_en` VARCHAR(64) NOT NULL COMMENT '面向用户的中性英文演示别名',
  `sort_order` INT NOT NULL,
  `is_demo_alias` TINYINT(1) NOT NULL DEFAULT 1,
  `note` VARCHAR(255) NOT NULL,
  PRIMARY KEY (`source_model_code`),
  UNIQUE KEY `uq_dim_machine_model_zh` (`display_name_zh`),
  CONSTRAINT `ck_dim_machine_model_demo_alias` CHECK (`is_demo_alias` = 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='语义展示维表：不改写来源 model1-model4，仅提供中性演示别名';

INSERT INTO `dim_machine_models`
  (`source_model_code`,`display_name_zh`,`display_name_en`,`sort_order`,`is_demo_alias`,`note`)
VALUES
  ('model1','A 型机组','Series A unit',1,1,'中性演示别名；原始来源仅提供 model1。'),
  ('model2','B 型机组','Series B unit',2,1,'中性演示别名；原始来源仅提供 model2。'),
  ('model3','C 型机组','Series C unit',3,1,'中性演示别名；原始来源仅提供 model3。'),
  ('model4','D 型机组','Series D unit',4,1,'中性演示别名；原始来源仅提供 model4。');

CREATE TABLE `pdm_telemetry` (
  `datetime` DATETIME NOT NULL COMMENT '小时级观测时间',
  `machineID` INT NOT NULL,
  `volt` DOUBLE NOT NULL COMMENT '电压',
  `rotate` DOUBLE NOT NULL COMMENT '转速',
  `pressure` DOUBLE NOT NULL COMMENT '压力',
  `vibration` DOUBLE NOT NULL COMMENT '振动',
  PRIMARY KEY (`machineID`, `datetime`),
  KEY `idx_telemetry_datetime` (`datetime`),
  CONSTRAINT `fk_telemetry_machine` FOREIGN KEY (`machineID`) REFERENCES `pdm_machines` (`machineID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='公开样例来源层：小时级遥测，观测点不等同于已核验运行小时';

CREATE TABLE `pdm_errors` (
  `datetime` DATETIME NOT NULL,
  `machineID` INT NOT NULL,
  `errorID` VARCHAR(32) NOT NULL COMMENT '来源错误代码',
  PRIMARY KEY (`machineID`, `datetime`, `errorID`),
  KEY `idx_errors_machine_datetime` (`machineID`, `datetime`),
  CONSTRAINT `fk_errors_machine` FOREIGN KEY (`machineID`) REFERENCES `pdm_machines` (`machineID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='公开样例来源层：非停机错误事件';

CREATE TABLE `pdm_maint` (
  `datetime` DATETIME NOT NULL,
  `machineID` INT NOT NULL,
  `comp` VARCHAR(32) NOT NULL COMMENT '维护或更换部件',
  PRIMARY KEY (`machineID`, `datetime`, `comp`),
  KEY `idx_maint_machine_datetime` (`machineID`, `datetime`),
  KEY `idx_maint_machine_component` (`machineID`, `comp`),
  CONSTRAINT `fk_maint_machine` FOREIGN KEY (`machineID`) REFERENCES `pdm_machines` (`machineID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='公开样例来源层：维护和部件更换事件';

CREATE TABLE `pdm_failures` (
  `datetime` DATETIME NOT NULL,
  `machineID` INT NOT NULL,
  `failure` VARCHAR(32) NOT NULL COMMENT '发生故障的部件',
  PRIMARY KEY (`machineID`, `datetime`, `failure`),
  KEY `idx_failures_machine_datetime` (`machineID`, `datetime`),
  KEY `idx_failures_machine_component` (`machineID`, `failure`),
  CONSTRAINT `fk_failures_machine` FOREIGN KEY (`machineID`) REFERENCES `pdm_machines` (`machineID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='公开样例来源层：部件故障事件';

CREATE TABLE `synthetic_fault_reports` (
  `fault_id` VARCHAR(24) NOT NULL,
  `datetime` DATETIME NOT NULL,
  `machineID` INT NOT NULL,
  `component` VARCHAR(32) NOT NULL,
  `machine_model` VARCHAR(32) NOT NULL,
  `machine_age` INT NOT NULL,
  `dominant_metric` VARCHAR(32) NOT NULL,
  `dominant_z` DOUBLE NOT NULL,
  `nearby_error_count_72h` INT NOT NULL,
  `nearby_error_codes_72h` VARCHAR(255) NULL,
  `recent_same_component_maint` VARCHAR(512) NOT NULL,
  `root_cause` VARCHAR(128) NOT NULL COMMENT '合成规则候选，不是现场结论',
  `failure_mechanism` TEXT NOT NULL,
  `severity` VARCHAR(16) NOT NULL,
  `resolution` TEXT NOT NULL,
  `fault_report` LONGTEXT NOT NULL COMMENT '确定性生成的合成报告',
  `synthetic_text_flag` TINYINT(1) NOT NULL,
  `aligned_to_source_failure` TINYINT(1) NOT NULL,
  `source_failure_key` VARCHAR(128) NOT NULL,
  `generator_version` VARCHAR(32) NOT NULL,
  `telemetry_window_start` DATETIME NOT NULL,
  `telemetry_window_end` DATETIME NOT NULL,
  `telemetry_points_72h` INT NOT NULL,
  `recent_same_component_maint_count_30d` INT NOT NULL,
  `recent_same_component_maint_last_datetime` DATETIME NULL,
  `source_maintenance_exact_match` TINYINT(1) NOT NULL,
  `volt_mean` DOUBLE NOT NULL,
  `volt_min` DOUBLE NOT NULL,
  `volt_max` DOUBLE NOT NULL,
  `volt_last` DOUBLE NOT NULL,
  `volt_trend_per_hour` DOUBLE NOT NULL,
  `volt_last_deviation_pct` DOUBLE NOT NULL,
  `volt_last_z` DOUBLE NOT NULL,
  `volt_max_abs_z` DOUBLE NOT NULL,
  `rotate_mean` DOUBLE NOT NULL,
  `rotate_min` DOUBLE NOT NULL,
  `rotate_max` DOUBLE NOT NULL,
  `rotate_last` DOUBLE NOT NULL,
  `rotate_trend_per_hour` DOUBLE NOT NULL,
  `rotate_last_deviation_pct` DOUBLE NOT NULL,
  `rotate_last_z` DOUBLE NOT NULL,
  `rotate_max_abs_z` DOUBLE NOT NULL,
  `pressure_mean` DOUBLE NOT NULL,
  `pressure_min` DOUBLE NOT NULL,
  `pressure_max` DOUBLE NOT NULL,
  `pressure_last` DOUBLE NOT NULL,
  `pressure_trend_per_hour` DOUBLE NOT NULL,
  `pressure_last_deviation_pct` DOUBLE NOT NULL,
  `pressure_last_z` DOUBLE NOT NULL,
  `pressure_max_abs_z` DOUBLE NOT NULL,
  `vibration_mean` DOUBLE NOT NULL,
  `vibration_min` DOUBLE NOT NULL,
  `vibration_max` DOUBLE NOT NULL,
  `vibration_last` DOUBLE NOT NULL,
  `vibration_trend_per_hour` DOUBLE NOT NULL,
  `vibration_last_deviation_pct` DOUBLE NOT NULL,
  `vibration_last_z` DOUBLE NOT NULL,
  `vibration_max_abs_z` DOUBLE NOT NULL,
  PRIMARY KEY (`fault_id`),
  UNIQUE KEY `uq_synthetic_fault_source_event` (`machineID`, `datetime`, `component`),
  KEY `idx_synthetic_fault_machine_datetime` (`machineID`, `datetime`),
  CONSTRAINT `fk_synthetic_fault_source` FOREIGN KEY (`machineID`, `datetime`, `component`)
    REFERENCES `pdm_failures` (`machineID`, `datetime`, `failure`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='合成演示层：与来源故障一一对齐的特征、规则候选和文字报告';

CREATE TABLE `synthetic_operator_logs` (
  `log_id` VARCHAR(24) NOT NULL,
  `datetime` DATETIME NOT NULL,
  `machineID` INT NOT NULL,
  `component` VARCHAR(32) NOT NULL,
  `log_type` VARCHAR(64) NOT NULL,
  `severity` VARCHAR(16) NOT NULL,
  `error_code` VARCHAR(128) NULL,
  `dominant_metric` VARCHAR(32) NOT NULL,
  `operator_note` LONGTEXT NOT NULL COMMENT '合成文字，不是现场人员原始日志',
  `linked_fault_id` VARCHAR(24) NULL,
  `synthetic_text_flag` TINYINT(1) NOT NULL,
  `aligned_to_source_failure` TINYINT(1) NOT NULL,
  `source_event_type` VARCHAR(64) NOT NULL,
  `source_event_key` VARCHAR(160) NOT NULL,
  `generator_version` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`log_id`),
  KEY `idx_synthetic_operator_machine_datetime` (`machineID`, `datetime`),
  KEY `idx_synthetic_operator_fault` (`linked_fault_id`),
  CONSTRAINT `fk_synthetic_operator_machine` FOREIGN KEY (`machineID`) REFERENCES `pdm_machines` (`machineID`),
  CONSTRAINT `fk_synthetic_operator_fault` FOREIGN KEY (`linked_fault_id`) REFERENCES `synthetic_fault_reports` (`fault_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='合成演示层：来源遥测和错误事件的自然语言化，不是现场日志';

CREATE TABLE `synthetic_maintenance_notes` (
  `maint_note_id` VARCHAR(24) NOT NULL,
  `datetime` DATETIME NOT NULL,
  `machineID` INT NOT NULL,
  `component` VARCHAR(32) NOT NULL,
  `maintenance_type` VARCHAR(80) NOT NULL,
  `root_cause` VARCHAR(128) NOT NULL,
  `action_taken` TEXT NOT NULL,
  `technician_note` LONGTEXT NOT NULL COMMENT '合成建议，不代表实际执行',
  `linked_fault_id` VARCHAR(24) NULL,
  `synthetic_text_flag` TINYINT(1) NOT NULL,
  `aligned_to_source_failure` TINYINT(1) NOT NULL,
  `source_maintenance_exact_match` TINYINT(1) NOT NULL,
  `source_event_key` VARCHAR(160) NOT NULL,
  `generator_version` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`maint_note_id`),
  KEY `idx_synthetic_notes_machine_datetime` (`machineID`, `datetime`),
  KEY `idx_synthetic_notes_fault` (`linked_fault_id`),
  CONSTRAINT `fk_synthetic_notes_machine` FOREIGN KEY (`machineID`) REFERENCES `pdm_machines` (`machineID`),
  CONSTRAINT `fk_synthetic_notes_fault` FOREIGN KEY (`linked_fault_id`) REFERENCES `synthetic_fault_reports` (`fault_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='合成演示层：维护文字、建议动作和规则原因';

CREATE TABLE `synthetic_work_orders` (
  `work_order_id` VARCHAR(24) NOT NULL,
  `opened_at` DATETIME NOT NULL,
  `closed_at` DATETIME NOT NULL,
  `machineID` INT NOT NULL,
  `component` VARCHAR(32) NOT NULL,
  `work_order_type` VARCHAR(32) NOT NULL,
  `priority` VARCHAR(16) NOT NULL,
  `status` VARCHAR(16) NOT NULL,
  `assigned_team` VARCHAR(64) NOT NULL,
  `duration_hours` DECIMAL(12,3) NOT NULL,
  `downtime_hours` DECIMAL(12,3) NOT NULL,
  `labor_hours` DECIMAL(12,3) NOT NULL,
  `labor_cost` DECIMAL(14,2) NOT NULL,
  `parts_cost` DECIMAL(14,2) NOT NULL,
  `total_cost` DECIMAL(14,2) NOT NULL,
  `linked_fault_id` VARCHAR(24) NULL,
  `linked_maint_note_id` VARCHAR(24) NOT NULL,
  `source_maintenance_event_flag` TINYINT(1) NOT NULL,
  `synthetic_record_flag` TINYINT(1) NOT NULL,
  `generator_version` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`work_order_id`),
  UNIQUE KEY `uq_synthetic_work_order_note` (`linked_maint_note_id`),
  KEY `idx_synthetic_work_order_machine_opened` (`machineID`, `opened_at`),
  KEY `idx_synthetic_work_order_fault` (`linked_fault_id`),
  CONSTRAINT `fk_synthetic_work_order_machine` FOREIGN KEY (`machineID`) REFERENCES `pdm_machines` (`machineID`),
  CONSTRAINT `fk_synthetic_work_order_fault` FOREIGN KEY (`linked_fault_id`) REFERENCES `synthetic_fault_reports` (`fault_id`),
  CONSTRAINT `fk_synthetic_work_order_note` FOREIGN KEY (`linked_maint_note_id`) REFERENCES `synthetic_maintenance_notes` (`maint_note_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='合成演示层：工单、工时、停机和成本，非真实生产或财务记录';

CREATE TABLE `synthetic_parts_usage` (
  `usage_id` VARCHAR(32) NOT NULL,
  `work_order_id` VARCHAR(24) NOT NULL,
  `part_code` VARCHAR(64) NOT NULL,
  `part_name` VARCHAR(128) NOT NULL,
  `quantity` INT NOT NULL,
  `unit_cost` DECIMAL(14,2) NOT NULL,
  `total_cost` DECIMAL(14,2) NOT NULL,
  `synthetic_record_flag` TINYINT(1) NOT NULL,
  `generator_version` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`usage_id`),
  KEY `idx_synthetic_parts_work_order` (`work_order_id`),
  CONSTRAINT `fk_synthetic_parts_work_order` FOREIGN KEY (`work_order_id`) REFERENCES `synthetic_work_orders` (`work_order_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='合成演示层：工单备件使用和成本';

CREATE TABLE `synthetic_daily_operations` (
  `operation_date` DATE NOT NULL,
  `machineID` INT NOT NULL,
  `model` VARCHAR(32) NOT NULL,
  `age` INT NOT NULL,
  `scheduled_hours` DECIMAL(8,3) NOT NULL,
  `runtime_hours` DECIMAL(8,3) NOT NULL,
  `downtime_hours` DECIMAL(8,3) NOT NULL,
  `production_units` INT NOT NULL,
  `good_units` INT NOT NULL,
  `scrap_units` INT NOT NULL,
  `availability_pct` DECIMAL(8,3) NOT NULL,
  `performance_pct` DECIMAL(8,3) NOT NULL,
  `quality_pct` DECIMAL(8,3) NOT NULL,
  `oee_pct` DECIMAL(8,3) NOT NULL,
  `energy_kwh` DECIMAL(14,3) NOT NULL,
  `source_telemetry_points` INT NOT NULL,
  `source_error_count` INT NOT NULL,
  `source_failure_count` INT NOT NULL,
  `source_maintenance_count` INT NOT NULL,
  `synthetic_record_flag` TINYINT(1) NOT NULL,
  `generator_version` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`machineID`, `operation_date`),
  KEY `idx_synthetic_daily_model_date` (`model`, `operation_date`),
  CONSTRAINT `fk_synthetic_daily_machine` FOREIGN KEY (`machineID`) REFERENCES `pdm_machines` (`machineID`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='合成演示层：设备日产量、停机、能耗和OEE，非真实经营数据';

CREATE TABLE `data_sources` (
  `source_id` VARCHAR(64) NOT NULL,
  `source_name` VARCHAR(255) NOT NULL,
  `source_type` VARCHAR(32) NOT NULL,
  `database_name` VARCHAR(128) NOT NULL,
  `description` TEXT NOT NULL,
  `fact_boundary` TEXT NOT NULL,
  `enabled` TINYINT(1) NOT NULL DEFAULT 1,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`source_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `analysis_runs` (
  `run_id` CHAR(36) NOT NULL,
  `source_id` VARCHAR(64) NOT NULL,
  `requested_query` TEXT NOT NULL,
  `scope_json` JSON NOT NULL,
  `status` VARCHAR(32) NOT NULL,
  `started_at` DATETIME(6) NOT NULL,
  `finished_at` DATETIME(6) NULL,
  `duration_ms` BIGINT NULL,
  `error_message` TEXT NULL,
  PRIMARY KEY (`run_id`),
  KEY `idx_analysis_runs_source_started` (`source_id`, `started_at`),
  CONSTRAINT `fk_analysis_runs_source` FOREIGN KEY (`source_id`) REFERENCES `data_sources` (`source_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `analysis_step_logs` (
  `step_log_id` BIGINT NOT NULL AUTO_INCREMENT,
  `run_id` CHAR(36) NOT NULL,
  `step_name` VARCHAR(128) NOT NULL,
  `tool_name` VARCHAR(128) NULL,
  `status` VARCHAR(32) NOT NULL,
  `started_at` DATETIME(6) NOT NULL,
  `finished_at` DATETIME(6) NULL,
  `duration_ms` BIGINT NULL,
  `input_summary_json` JSON NULL,
  `output_summary_json` JSON NULL,
  `error_message` TEXT NULL,
  PRIMARY KEY (`step_log_id`),
  KEY `idx_step_logs_run_step` (`run_id`, `step_name`),
  CONSTRAINT `fk_step_log_run` FOREIGN KEY (`run_id`) REFERENCES `analysis_runs` (`run_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `detected_anomalies` (
  `anomaly_id` CHAR(36) NOT NULL,
  `run_id` CHAR(36) NOT NULL,
  `machineID` INT NOT NULL,
  `event_time` DATETIME NOT NULL,
  `metric` VARCHAR(32) NOT NULL,
  `actual_value` DOUBLE NOT NULL,
  `baseline_value` DOUBLE NULL,
  `robust_z` DOUBLE NOT NULL,
  `direction` VARCHAR(16) NOT NULL,
  `severity` VARCHAR(16) NOT NULL,
  `method` VARCHAR(64) NOT NULL,
  `evidence_json` JSON NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`anomaly_id`),
  KEY `idx_anomaly_machine_time` (`machineID`, `event_time`),
  CONSTRAINT `fk_anomaly_run` FOREIGN KEY (`run_id`) REFERENCES `analysis_runs` (`run_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `root_cause_candidates` (
  `candidate_id` CHAR(36) NOT NULL,
  `run_id` CHAR(36) NOT NULL,
  `fault_id` VARCHAR(24) NULL,
  `anomaly_id` CHAR(36) NULL,
  `candidate_rank` INT NOT NULL,
  `candidate_name` VARCHAR(128) NOT NULL,
  `confidence_score` DECIMAL(8,3) NOT NULL COMMENT '规则证据分，不是因果概率',
  `evidence_json` JSON NOT NULL,
  `limitations_text` TEXT NOT NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`candidate_id`),
  KEY `idx_rca_run_score` (`run_id`, `confidence_score`),
  CONSTRAINT `fk_rca_run` FOREIGN KEY (`run_id`) REFERENCES `analysis_runs` (`run_id`) ON DELETE CASCADE,
  CONSTRAINT `fk_rca_fault` FOREIGN KEY (`fault_id`) REFERENCES `synthetic_fault_reports` (`fault_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `analysis_reports` (
  `report_id` CHAR(36) NOT NULL,
  `run_id` CHAR(36) NOT NULL,
  `report_type` VARCHAR(64) NOT NULL,
  `title` VARCHAR(255) NOT NULL,
  `executive_summary` LONGTEXT NOT NULL,
  `report_json` JSON NOT NULL,
  `html_path` VARCHAR(1024) NULL,
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`report_id`),
  CONSTRAINT `fk_report_run` FOREIGN KEY (`run_id`) REFERENCES `analysis_runs` (`run_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO `data_sources` (
  `source_id`, `source_name`, `source_type`, `database_name`, `description`, `fact_boundary`
) VALUES (
  'industrial_pdm',
  'Industrial PdM DataAgent Demo',
  'mysql',
  'industrial_pdm',
  '设备遥测、错误、维护、故障及明确标注的合成工单、运营和文本数据。',
  'pdm_* 是未修改的公开样例来源层；synthetic_* 是可复现演示衍生层，不得当作现场事实。'
) ON DUPLICATE KEY UPDATE
  `source_name`=VALUES(`source_name`), `description`=VALUES(`description`),
  `fact_boundary`=VALUES(`fact_boundary`), `enabled`=1;

CREATE VIEW `v_machine_daily_kpi` AS
SELECT DATE(t.`datetime`) AS `metric_date`, t.`machineID`,
       m.`model` AS `model_code`, d.`display_name_zh` AS `model_name`, m.`model`, m.`age`,
       COUNT(*) AS `telemetry_points`,
       AVG(t.`volt`) AS `avg_volt`, AVG(t.`rotate`) AS `avg_rotate`,
       AVG(t.`pressure`) AS `avg_pressure`, AVG(t.`vibration`) AS `avg_vibration`,
       MAX(COALESCE(e.`error_count`,0)) AS `error_count`,
       MAX(COALESCE(f.`failure_count`,0)) AS `failure_count`
FROM `pdm_telemetry` t
JOIN `pdm_machines` m ON m.`machineID`=t.`machineID`
JOIN `dim_machine_models` d ON d.`source_model_code`=m.`model`
LEFT JOIN (
  SELECT `machineID`, DATE(`datetime`) AS `metric_date`, COUNT(*) AS `error_count`
  FROM `pdm_errors` GROUP BY `machineID`, DATE(`datetime`)
) e ON e.`machineID`=t.`machineID` AND e.`metric_date`=DATE(t.`datetime`)
LEFT JOIN (
  SELECT `machineID`, DATE(`datetime`) AS `metric_date`, COUNT(*) AS `failure_count`
  FROM `pdm_failures` GROUP BY `machineID`, DATE(`datetime`)
) f ON f.`machineID`=t.`machineID` AND f.`metric_date`=DATE(t.`datetime`)
GROUP BY DATE(t.`datetime`), t.`machineID`, m.`model`, d.`display_name_zh`, m.`age`;

CREATE VIEW `v_event_timeline` AS
SELECT e.`machineID`, e.`datetime` AS `event_time`, 'error' AS `event_type`, e.`errorID` AS `event_code`,
       NULL AS `fault_id`, 'source' AS `data_origin`, 0 AS `synthetic_text_flag`
FROM `pdm_errors` e
UNION ALL
SELECT m.`machineID`, m.`datetime`, 'maintenance', m.`comp`, NULL, 'source', 0 FROM `pdm_maint` m
UNION ALL
SELECT f.`machineID`, f.`datetime`, 'failure', f.`failure`, r.`fault_id`, 'source', 0
FROM `pdm_failures` f LEFT JOIN `synthetic_fault_reports` r
  ON r.`machineID`=f.`machineID` AND r.`datetime`=f.`datetime` AND r.`component`=f.`failure`
UNION ALL
SELECT o.`machineID`, o.`datetime`, 'synthetic_operator_log', o.`log_type`, o.`linked_fault_id`,
       'synthetic_derived', o.`synthetic_text_flag`
FROM `synthetic_operator_logs` o;

CREATE VIEW `v_failure_rca_evidence` AS
SELECT r.*,
       (SELECT COUNT(*) FROM `synthetic_operator_logs` o WHERE o.`linked_fault_id`=r.`fault_id`) AS `linked_operator_log_count`,
       (SELECT COUNT(*) FROM `synthetic_maintenance_notes` n WHERE n.`linked_fault_id`=r.`fault_id`) AS `linked_maintenance_note_count`
FROM `synthetic_fault_reports` r;

CREATE VIEW `v_machine_reliability` AS
SELECT m.`machineID`,m.`model` AS `model_code`,d.`display_name_zh` AS `model_name`,m.`model`,m.`age`,
       COALESCE(t.`observed_hours`,0) AS `observed_hours`,
       COALESCE(e.`error_count`,0) AS `error_count`,
       COALESCE(mt.`maintenance_count`,0) AS `maintenance_count`,
       COALESCE(f.`failure_count`,0) AS `failure_count`,
       CASE WHEN COALESCE(t.`observed_hours`,0)=0 THEN 0
            ELSE COALESCE(f.`failure_count`,0)*1000.0/t.`observed_hours` END AS `failures_per_1000_observed_hours`
FROM `pdm_machines` m
JOIN `dim_machine_models` d ON d.`source_model_code`=m.`model`
LEFT JOIN (SELECT `machineID`,COUNT(*) AS `observed_hours` FROM `pdm_telemetry` GROUP BY `machineID`) t ON t.`machineID`=m.`machineID`
LEFT JOIN (SELECT `machineID`,COUNT(*) AS `error_count` FROM `pdm_errors` GROUP BY `machineID`) e ON e.`machineID`=m.`machineID`
LEFT JOIN (SELECT `machineID`,COUNT(*) AS `maintenance_count` FROM `pdm_maint` GROUP BY `machineID`) mt ON mt.`machineID`=m.`machineID`
LEFT JOIN (SELECT `machineID`,COUNT(*) AS `failure_count` FROM `pdm_failures` GROUP BY `machineID`) f ON f.`machineID`=m.`machineID`;

CREATE VIEW `v_monthly_operations_kpi` AS
SELECT DATE_FORMAT(o.`operation_date`,'%Y-%m') AS `operation_month`,
       o.`model` AS `model_code`,d.`display_name_zh` AS `model_name`,o.`model`,
       SUM(o.`production_units`) AS `production_units`,SUM(o.`good_units`) AS `good_units`,
       SUM(o.`scrap_units`) AS `scrap_units`,ROUND(AVG(o.`oee_pct`),3) AS `avg_oee_pct`,
       ROUND(SUM(o.`downtime_hours`),3) AS `downtime_hours`,ROUND(SUM(o.`energy_kwh`),3) AS `energy_kwh`
FROM `synthetic_daily_operations` o
JOIN `dim_machine_models` d ON d.`source_model_code`=o.`model`
GROUP BY DATE_FORMAT(o.`operation_date`,'%Y-%m'),o.`model`,d.`display_name_zh`;

CREATE VIEW `v_work_order_kpi` AS
SELECT `component`,`work_order_type`,COUNT(*) AS `work_order_count`,
       ROUND(SUM(`downtime_hours`),3) AS `downtime_hours`,
       ROUND(SUM(`labor_cost`),2) AS `labor_cost`,ROUND(SUM(`parts_cost`),2) AS `parts_cost`,
       ROUND(SUM(`total_cost`),2) AS `total_cost`
FROM `synthetic_work_orders`
GROUP BY `component`,`work_order_type`;
