# 客服 v2 additions 根目录历史回传件归档

状态：`HISTORICAL_INTAKE_RECOVERED_PROVENANCE_STILL_BLOCKED`

本目录保存 2026-08-26 从项目根目录发现的两份、各 60 条的已填写历史盲标回传件及其原始 sidecar manifest。归档前，两份表都已在 `shop` conda 环境中以候选集 `candidate-v2-additions.jsonl` 执行完整校验并通过；归档副本与根目录原文件逐字节一致。

这些文件不是本轮待填写表，也不得重新发给 reviewer 覆盖。它们是已经进入历史 v2 合并链的来源证据。原 manifest 的 `sheetPath` 保留当时位于项目根目录的相对路径，故没有为了新目录而改写；新位置的真实性由本包 `evidence-manifest.json` 和 `SHA256SUMS` 绑定。

## 归档意义与边界

- reviewer-a 回传件 SHA-256 为 `3b24ca2e3cded7c3d7e1330ccfc2580a14c850bac3541af62450b2443f78829a`，与其 sealed manifest 声明的 `sourceOpenSheetSha256` 一致。
- reviewer-b 回传件 SHA-256 为 `9affe71c43465157b144897cc3ac77a736edad0a1d6089253a9d3394fcc3635b`，同样与 sealed manifest 一致。
- 因此，原 provenance audit 中“reviewer-a 已填写 open source bytes 不可得”这一项现在有了补充证据。
- 但旧工具把 `openSheetSha256AtExport` 错写为填写后文件 hash 的语义问题仍然存在；两位 reviewer 的独立性/不可见性声明也仍然缺失。
- 本补充归档不会、也不能原地改写既有 immutable provenance package，不能把当前 v2 晋升为 release/final evidence。

权威机器结论见 `recovery-audit.json`。所有文件均应保持只读；后续修复必须生成新的 successor evidence package。
