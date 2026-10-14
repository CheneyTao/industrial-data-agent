# 合成演示数据

运行以下命令生成本目录的 6 张表和 `manifest.json`：

```bash
python scripts/generate_synthetic_data.py
```

生成过程没有随机抽样：相同来源文件、规则版本和生成器版本会得到相同结果。全部输出均为演示衍生数据，不是现场日志、实际工单、真实成本、真实产量或已确认根因。

文件默认不提交到 Git；完整流程 `python scripts/run_all.py` 会先生成数据，再构建 SQLite、RAG 和报告。
