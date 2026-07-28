---
id: kafka_operations
title: Kafka 消费运行指标
domain: kafka
source_type: engineering_guide
content_kind: engineering_practice
tags: [kafka, 监控, 消费治理]
aliases: [consumer lag, 消费积压]
difficulty: intermediate
question_patterns:
  - Kafka 消费者需要监控哪些运行指标？
  - 如何判断积压是突发流量还是处理能力不足？
references:
  - title: Kafka 群集监视和网络配置
    url: https://learn.microsoft.com/zh-cn/azure/aks/kafka-configure
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 监视 Azure 事件中心
    url: https://learn.microsoft.com/zh-cn/azure/event-hubs/monitor-event-hubs
    source_kind: official_cn
    publisher: Microsoft Learn
---
# Kafka 消费运行指标

## 核心结论
消费健康不能只看当前消费积压，还要结合增长速度、最老消息年龄、处理吞吐、失败率和再平衡。消费积压是结果指标，定位原因需要同时观察生产、分区分布、消费者处理和下游依赖。

## 机制与边界
记录每个消费组和分区的当前 offset、末端 offset 与积压量，计算单位时间变化和按当前净处理速度估算的清空时间。处理侧关注单条与批次耗时、成功率、重试量、poll 间隔和心跳。基础设施侧观察 broker 磁盘、网络、请求延迟、分区领导者和副本状态。告警应区分短暂峰值、持续增长和最老消息超时。

## 常见错误
只看总 lag 会掩盖单分区热点。消费者数量超过分区数不会增加并行度。遇到积压就扩容可能把数据库压垮；忽略失败重试和坏消息，会让表面消费速率失真。

## 工程权衡
更细指标便于定位，但增加标签基数与成本。较长批次提升吞吐，却提高处理延迟和再平衡恢复成本。扩容前应确认分区、下游容量和连接预算，并设置逐级放量。

## 可观察评分信号
回答应给出消费积压、最老消息年龄、积压清空时间、消费吞吐、处理 p95、失败与重试率、再平衡次数和下游饱和度，并说明告警阈值、扩容步骤和恢复完成条件。
