---
id: system_design_backend
title: 后端系统设计评价基准
domain: system-design
source_type: expert_benchmark
content_kind: benchmark
tags: [system-design, 后端架构, 可靠性]
aliases: [系统设计评分, 后端架构证据]
difficulty: advanced
question_patterns:
  - 如何评价一个后端系统设计回答是否完整？
  - 系统设计中怎样用量化证据支撑架构选择？
references:
  - title: 可靠性设计原则
    url: https://learn.microsoft.com/zh-cn/azure/well-architected/reliability/principles
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 健康终结点监视模式
    url: https://learn.microsoft.com/zh-cn/azure/architecture/patterns/health-endpoint-monitoring
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 容量规划的体系结构策略
    url: https://learn.microsoft.com/zh-cn/azure/well-architected/performance-efficiency/capacity-planning
    source_kind: official_cn
    publisher: Microsoft Learn
---
# 后端系统设计评价基准

## 核心结论
优秀系统设计从需求和约束开始，以容量模型决定主要结构，明确数据流、一致性、故障边界和演进路径，并为关键选择给出可验证指标。堆叠中间件名称不是设计。

## 机制与边界
先澄清核心用例、峰值、延迟、可用性和数据保留，再画入口、服务、存储、缓存与消息流。说明分区键、事实来源、事务边界和读写路径。逐个分析组件变慢、不可用、重复和数据不一致时的行为，配置超时、限流、隔离、降级和恢复。最后给出从简单方案到目标架构的分阶段演进。

## 常见错误
没有数字就直接分库分表，或把最终一致性当作无需说明失败窗口，都会失分。只画正常路径、不讨论热点、容量和恢复，无法证明方案可运行。为所有风险增加新组件，会让复杂度和运维成本失控。

## 工程权衡
一致性、可用性、延迟、成本和开发复杂度相互制约。设计应明确哪些需求是硬约束，哪些可降级。优先采用团队能运维的简单结构，在指标证明瓶颈后再增加分片、队列或多地域。

## 可观察评分信号
高质量回答会给出 QPS、并发、数据增长、分区与副本估算，展示关键 SLI、健康检查、故障演练、恢复目标和迁移计划，并能解释每个组件不使用时会失去什么。
