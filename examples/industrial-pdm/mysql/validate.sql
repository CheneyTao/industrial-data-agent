USE `industrial_pdm`;

SELECT 'pdm_machines' AS table_name, COUNT(*) AS row_count, 100 AS expected FROM `pdm_machines`
UNION ALL SELECT 'pdm_telemetry', COUNT(*), 876100 FROM `pdm_telemetry`
UNION ALL SELECT 'pdm_errors', COUNT(*), 3919 FROM `pdm_errors`
UNION ALL SELECT 'pdm_maint', COUNT(*), 3286 FROM `pdm_maint`
UNION ALL SELECT 'pdm_failures', COUNT(*), 761 FROM `pdm_failures`
UNION ALL SELECT 'synthetic_fault_reports', COUNT(*), 761 FROM `synthetic_fault_reports`
UNION ALL SELECT 'synthetic_operator_logs', COUNT(*), 6587 FROM `synthetic_operator_logs`
UNION ALL SELECT 'synthetic_maintenance_notes', COUNT(*), 3304 FROM `synthetic_maintenance_notes`
UNION ALL SELECT 'synthetic_work_orders', COUNT(*), 3304 FROM `synthetic_work_orders`
UNION ALL SELECT 'synthetic_parts_usage', COUNT(*), 5417 FROM `synthetic_parts_usage`
UNION ALL SELECT 'synthetic_daily_operations', COUNT(*), 36500 FROM `synthetic_daily_operations`;

SELECT COUNT(*) AS unmatched_source_failure_count
FROM `pdm_failures` f
LEFT JOIN `synthetic_fault_reports` r
  ON r.`machineID`=f.`machineID` AND r.`datetime`=f.`datetime` AND r.`component`=f.`failure`
WHERE r.`fault_id` IS NULL;

SELECT COUNT(*) AS invalid_linked_synthetic_log_count
FROM `synthetic_operator_logs` o
LEFT JOIN `synthetic_fault_reports` r ON r.`fault_id`=o.`linked_fault_id`
WHERE o.`linked_fault_id` IS NOT NULL AND r.`fault_id` IS NULL;

SELECT COUNT(*) AS invalid_precursor_time_count
FROM `synthetic_operator_logs` o
JOIN `synthetic_fault_reports` r ON r.`fault_id`=o.`linked_fault_id`
WHERE o.`datetime`>=r.`datetime`;

SELECT COUNT(*) AS non_synthetic_flag_count
FROM (
  SELECT `synthetic_text_flag` AS flag_value FROM `synthetic_fault_reports`
  UNION ALL SELECT `synthetic_text_flag` FROM `synthetic_operator_logs`
  UNION ALL SELECT `synthetic_text_flag` FROM `synthetic_maintenance_notes`
  UNION ALL SELECT `synthetic_record_flag` FROM `synthetic_work_orders`
  UNION ALL SELECT `synthetic_record_flag` FROM `synthetic_parts_usage`
  UNION ALL SELECT `synthetic_record_flag` FROM `synthetic_daily_operations`
) flags
WHERE flag_value<>1;

SELECT COUNT(*) AS parts_cost_mismatch_count
FROM `synthetic_work_orders` w
JOIN (
  SELECT `work_order_id`,ROUND(SUM(`total_cost`),2) AS parts_sum
  FROM `synthetic_parts_usage` GROUP BY `work_order_id`
) p ON p.`work_order_id`=w.`work_order_id`
WHERE ABS(w.`parts_cost`-p.`parts_sum`)>0.001;

SELECT `source_id`,`source_type`,`database_name`,`enabled`,`fact_boundary`
FROM `data_sources` WHERE `source_id`='industrial_pdm';

SELECT `source_model_code`,`display_name_zh`,`is_demo_alias`,`note`
FROM `dim_machine_models`
ORDER BY `sort_order`;

SELECT COUNT(*) AS unmapped_machine_model_count
FROM (SELECT DISTINCT `model` FROM `pdm_machines`) m
LEFT JOIN `dim_machine_models` d ON d.`source_model_code`=m.`model`
WHERE d.`source_model_code` IS NULL;
