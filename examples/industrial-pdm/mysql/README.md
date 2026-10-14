# MySQL 数据源

推荐从项目根目录执行 `docker compose up -d`。容器首次创建数据卷时会按顺序执行：

1. 一次性 `import-data` 服务从只读挂载的 `original/`、`synthetic/` 复制 CSV 到专用导入卷；源文件不会交给 MySQL 改写权限。
2. `schema.sql`：创建来源层、合成演示层、分析审计层和视图。
3. `docker_import.sql`：从专用导入卷装载全部 11 张业务数据表。
4. 健康检查不只检查端口，还会核对 11 张表的期望行数；导入中途失败时容器不会被误报为健康。

导入后执行：

```bash
mysql -h 127.0.0.1 -P 3307 -u industrial -p industrial_pdm < mysql/validate.sql
```

项目不再提供包含本机绝对路径的宿主机导入 SQL。这样同一份工程可在 Windows、Linux、CI 和其他开发机上复现。若重新生成了 `synthetic/`，请创建新的 MySQL 数据卷或在受控环境中重新执行 schema/import；不要在生产数据库直接运行这些演示初始化脚本。

已有 2.0 数据卷升级到 2.1 时，可执行 `mysql/migrations/002_model_aliases.sql`。该迁移只新增型号语义维表并重建三个展示视图，不改写 `pdm_machines.model` 的来源值。
