# AI Shop 本地只读开放到达率容量基准

> Run `capacity-open-arrival-readonly-v1-20260825`；`notProductionSlo=true`；不进入质量门禁。

> Claim `LOCAL_OPEN_ARRIVAL_READ_ONLY_OBSERVATION`。

| 目标 QPS | 实际发起 QPS | 完成吞吐 QPS | 计划/发起/完成 | 丢弃 | 超时 | 429 | 晚启动 |
|---:|---:|---:|---|---:|---:|---:|---:|
| 2.0 | 2.0 | 1.210963 | 24/24/24 | 0 | 0 | 0 | 0 |

| 指标 | P50 | P95 | P99 | Max |
|---|---:|---:|---:|---:|
| generator delay (ms) | 0.479 | 1.515 | 1.675 | 1.716 |
| queue delay (ms) | 0.051 | 0.084 | 0.126 | 0.138 |
| end-to-end latency (ms) | 636.949 | 9431.686 | 13223.514 | 14318.766 |

## 边界

这是本机固定节拍、固定只读 case 混合下的开放到达率观察值。它不能外推为生产 SLO、峰值容量或真实业务流量分布。
