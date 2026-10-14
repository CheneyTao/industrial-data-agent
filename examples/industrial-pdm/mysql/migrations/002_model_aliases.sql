USE `industrial_pdm`;
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `dim_machine_models` (
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
  ('model4','D 型机组','Series D unit',4,1,'中性演示别名；原始来源仅提供 model4。')
ON DUPLICATE KEY UPDATE
  `display_name_zh`=VALUES(`display_name_zh`),
  `display_name_en`=VALUES(`display_name_en`),
  `sort_order`=VALUES(`sort_order`),
  `is_demo_alias`=VALUES(`is_demo_alias`),
  `note`=VALUES(`note`);

CREATE OR REPLACE VIEW `v_machine_daily_kpi` AS
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

CREATE OR REPLACE VIEW `v_machine_reliability` AS
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

CREATE OR REPLACE VIEW `v_monthly_operations_kpi` AS
SELECT DATE_FORMAT(o.`operation_date`,'%Y-%m') AS `operation_month`,
       o.`model` AS `model_code`,d.`display_name_zh` AS `model_name`,o.`model`,
       SUM(o.`production_units`) AS `production_units`,SUM(o.`good_units`) AS `good_units`,
       SUM(o.`scrap_units`) AS `scrap_units`,ROUND(AVG(o.`oee_pct`),3) AS `avg_oee_pct`,
       ROUND(SUM(o.`downtime_hours`),3) AS `downtime_hours`,ROUND(SUM(o.`energy_kwh`),3) AS `energy_kwh`
FROM `synthetic_daily_operations` o
JOIN `dim_machine_models` d ON d.`source_model_code`=o.`model`
GROUP BY DATE_FORMAT(o.`operation_date`,'%Y-%m'),o.`model`,d.`display_name_zh`;
