# 来源数据

此目录本地保存 Microsoft Azure Predictive Maintenance 公开样例的 5 个 CSV 快照。`original` 表示项目未修改文件字节，不表示这些记录已被核验为真实工厂现场数据。

公开索引页：<https://www.kaggle.com/datasets/arnabbiswas1/microsoft-azure-predictive-maintenance>

项目运行前需要以下文件：

- `PdM_machines.csv`
- `PdM_telemetry.csv`
- `PdM_errors.csv`
- `PdM_maint.csv`
- `PdM_failures.csv`

预期 SHA-256 写在 `scripts/generate_synthetic_data.py` 中。生成脚本默认拒绝校验和不一致的来源文件。由于公开页面将许可证标为 Unknown，提交或再分发这些 CSV 前应自行确认授权；工程默认通过 `.gitignore` 排除它们。
