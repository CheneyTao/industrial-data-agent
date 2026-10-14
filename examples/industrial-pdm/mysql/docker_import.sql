-- Executed automatically inside the MySQL container on first initialization.
USE `industrial_pdm`;
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;
TRUNCATE TABLE `synthetic_parts_usage`;
TRUNCATE TABLE `synthetic_work_orders`;
TRUNCATE TABLE `synthetic_maintenance_notes`;
TRUNCATE TABLE `synthetic_operator_logs`;
TRUNCATE TABLE `synthetic_fault_reports`;
TRUNCATE TABLE `synthetic_daily_operations`;
TRUNCATE TABLE `pdm_failures`;
TRUNCATE TABLE `pdm_maint`;
TRUNCATE TABLE `pdm_errors`;
TRUNCATE TABLE `pdm_telemetry`;
TRUNCATE TABLE `pdm_machines`;
SET FOREIGN_KEY_CHECKS = 1;

LOAD DATA INFILE '/data/original/PdM_machines.csv'
INTO TABLE `pdm_machines`
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(`machineID`, @model, @age)
SET `model`=TRIM(TRAILING '\r' FROM @model),
    `age`=CAST(TRIM(TRAILING '\r' FROM @age) AS UNSIGNED);

LOAD DATA INFILE '/data/original/PdM_telemetry.csv'
INTO TABLE `pdm_telemetry`
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(@datetime, `machineID`, `volt`, `rotate`, `pressure`, `vibration`)
SET `datetime`=STR_TO_DATE(TRIM(@datetime),'%Y-%m-%d %H:%i:%s');

LOAD DATA INFILE '/data/original/PdM_errors.csv'
INTO TABLE `pdm_errors`
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(@datetime, `machineID`, @errorID)
SET `datetime`=STR_TO_DATE(TRIM(@datetime),'%Y-%m-%d %H:%i:%s'),
    `errorID`=TRIM(TRAILING '\r' FROM @errorID);

LOAD DATA INFILE '/data/original/PdM_maint.csv'
INTO TABLE `pdm_maint`
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(@datetime, `machineID`, @comp)
SET `datetime`=STR_TO_DATE(TRIM(@datetime),'%Y-%m-%d %H:%i:%s'),
    `comp`=TRIM(TRAILING '\r' FROM @comp);

LOAD DATA INFILE '/data/original/PdM_failures.csv'
INTO TABLE `pdm_failures`
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\r\n'
IGNORE 1 LINES
(@datetime, `machineID`, @failure)
SET `datetime`=STR_TO_DATE(TRIM(@datetime),'%Y-%m-%d %H:%i:%s'),
    `failure`=TRIM(TRAILING '\r' FROM @failure);

LOAD DATA INFILE '/data/synthetic/synthetic_fault_reports.csv'
INTO TABLE `synthetic_fault_reports`
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(`fault_id`,@datetime,`machineID`,`component`,`machine_model`,`machine_age`,`dominant_metric`,`dominant_z`,
 `nearby_error_count_72h`,@nearby_error_codes,`recent_same_component_maint`,`root_cause`,`failure_mechanism`,
 `severity`,`resolution`,`fault_report`,`synthetic_text_flag`,`aligned_to_source_failure`,`source_failure_key`,
 `generator_version`,@telemetry_window_start,@telemetry_window_end,`telemetry_points_72h`,
 `recent_same_component_maint_count_30d`,@recent_maint_dt,`source_maintenance_exact_match`,
 `volt_mean`,`volt_min`,`volt_max`,`volt_last`,`volt_trend_per_hour`,`volt_last_deviation_pct`,`volt_last_z`,`volt_max_abs_z`,
 `rotate_mean`,`rotate_min`,`rotate_max`,`rotate_last`,`rotate_trend_per_hour`,`rotate_last_deviation_pct`,`rotate_last_z`,`rotate_max_abs_z`,
 `pressure_mean`,`pressure_min`,`pressure_max`,`pressure_last`,`pressure_trend_per_hour`,`pressure_last_deviation_pct`,`pressure_last_z`,`pressure_max_abs_z`,
 `vibration_mean`,`vibration_min`,`vibration_max`,`vibration_last`,`vibration_trend_per_hour`,`vibration_last_deviation_pct`,`vibration_last_z`,`vibration_max_abs_z`)
SET `datetime`=STR_TO_DATE(TRIM(@datetime),'%Y-%m-%d %H:%i:%s'),
    `nearby_error_codes_72h`=NULLIF(TRIM(@nearby_error_codes),''),
    `telemetry_window_start`=STR_TO_DATE(TRIM(@telemetry_window_start),'%Y-%m-%d %H:%i:%s'),
    `telemetry_window_end`=STR_TO_DATE(TRIM(@telemetry_window_end),'%Y-%m-%d %H:%i:%s'),
    `recent_same_component_maint_last_datetime`=IF(TRIM(@recent_maint_dt)='',NULL,STR_TO_DATE(TRIM(@recent_maint_dt),'%Y-%m-%d %H:%i:%s'));

LOAD DATA INFILE '/data/synthetic/synthetic_operator_logs.csv'
INTO TABLE `synthetic_operator_logs`
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(`log_id`,@datetime,`machineID`,`component`,`log_type`,`severity`,@error_code,`dominant_metric`,`operator_note`,
 @linked_fault_id,`synthetic_text_flag`,`aligned_to_source_failure`,`source_event_type`,`source_event_key`,`generator_version`)
SET `datetime`=STR_TO_DATE(TRIM(@datetime),'%Y-%m-%d %H:%i:%s'),
    `error_code`=NULLIF(TRIM(@error_code),''),
    `linked_fault_id`=NULLIF(TRIM(@linked_fault_id),'');

LOAD DATA INFILE '/data/synthetic/synthetic_maintenance_notes.csv'
INTO TABLE `synthetic_maintenance_notes`
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(`maint_note_id`,@datetime,`machineID`,`component`,`maintenance_type`,`root_cause`,`action_taken`,`technician_note`,
 @linked_fault_id,`synthetic_text_flag`,`aligned_to_source_failure`,`source_maintenance_exact_match`,`source_event_key`,`generator_version`)
SET `datetime`=STR_TO_DATE(TRIM(@datetime),'%Y-%m-%d %H:%i:%s'),
    `linked_fault_id`=NULLIF(TRIM(@linked_fault_id),'');

LOAD DATA INFILE '/data/synthetic/synthetic_work_orders.csv'
INTO TABLE `synthetic_work_orders`
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(`work_order_id`,@opened_at,@closed_at,`machineID`,`component`,`work_order_type`,`priority`,`status`,`assigned_team`,
 `duration_hours`,`downtime_hours`,`labor_hours`,`labor_cost`,`parts_cost`,`total_cost`,@linked_fault_id,
 `linked_maint_note_id`,`source_maintenance_event_flag`,`synthetic_record_flag`,`generator_version`)
SET `opened_at`=STR_TO_DATE(TRIM(@opened_at),'%Y-%m-%d %H:%i:%s'),
    `closed_at`=STR_TO_DATE(TRIM(@closed_at),'%Y-%m-%d %H:%i:%s'),
    `linked_fault_id`=NULLIF(TRIM(@linked_fault_id),'');

LOAD DATA INFILE '/data/synthetic/synthetic_parts_usage.csv'
INTO TABLE `synthetic_parts_usage`
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(`usage_id`,`work_order_id`,`part_code`,`part_name`,`quantity`,`unit_cost`,`total_cost`,`synthetic_record_flag`,`generator_version`);

LOAD DATA INFILE '/data/synthetic/synthetic_daily_operations.csv'
INTO TABLE `synthetic_daily_operations`
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(@operation_date,`machineID`,`model`,`age`,`scheduled_hours`,`runtime_hours`,`downtime_hours`,`production_units`,
 `good_units`,`scrap_units`,`availability_pct`,`performance_pct`,`quality_pct`,`oee_pct`,`energy_kwh`,
 `source_telemetry_points`,`source_error_count`,`source_failure_count`,`source_maintenance_count`,
 `synthetic_record_flag`,`generator_version`)
SET `operation_date`=STR_TO_DATE(TRIM(@operation_date),'%Y-%m-%d');

INSERT INTO `data_sources` (`source_id`,`source_name`,`source_type`,`database_name`,`description`,`fact_boundary`,`enabled`)
VALUES (
  'industrial_pdm','Industrial PdM DataAgent Demo','mysql','industrial_pdm',
  '设备遥测、错误、维护、故障及明确标注的合成工单、运营和文本数据。',
  'pdm_* 是未修改的公开样例来源层；synthetic_* 是可复现演示衍生层，不得当作现场事实。',1
)
ON DUPLICATE KEY UPDATE `source_name`=VALUES(`source_name`),`description`=VALUES(`description`),
  `fact_boundary`=VALUES(`fact_boundary`),`enabled`=1;

SET FOREIGN_KEY_CHECKS = 1;
