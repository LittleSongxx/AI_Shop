# 本轮未附 external unseen 正式样本

当前已有的 125 条 external candidate 已在开发环境生成并对开发者可见，状态永久为 `DISQUALIFIED_DEVELOPER_VISIBLE`。对它们补做人工标注不能恢复 unseen，因此没有把它们放进本轮正式评审包。

若只是验证表单/协议，可以另建明确标为 `PROTOCOL_TRIAL_ONLY` 的副本；正式质量评测必须由独立保管人按随附规范重新生成至少 100 条替代数据，并在运行前对开发者隐藏正文和 expected/qrel。
