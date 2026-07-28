---
id: kafka_delivery
title: Kafka 投递语义与幂等副作用
domain: kafka
source_type: theory
content_kind: mechanism
tags: [kafka, 投递语义, 幂等]
aliases: [至少一次投递, offset 提交]
difficulty: beginner
question_patterns:
  - Kafka 至少一次投递下如何避免重复副作用？
  - offset 提交时机怎样影响消息丢失和重复？
references:
  - title: Apache Kafka 事务
    url: https://learn.microsoft.com/zh-cn/azure/event-hubs/apache-kafka-transactions
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 用于 Apache Kafka 的常见问题
    url: https://learn.microsoft.com/zh-cn/azure/event-hubs/apache-kafka-frequently-asked-questions
    source_kind: official_cn
    publisher: Microsoft Learn
---
# Kafka 投递语义与幂等副作用

## 核心结论
至少一次投递允许消息重复，应用必须让副作用可幂等。offset 在业务处理成功前提交可能丢失处理，成功后提交则可能在提交失败或进程崩溃时重复，因此提交顺序必须与业务一致性策略配套。

## 机制与边界
消费者读取消息后执行业务，再提交 offset，可保证失败后重读，但同一消息可能再次执行。幂等可通过事件 ID 唯一约束、去重表、状态机条件更新或下游幂等接口实现。Kafka 事务可原子组合 Kafka 内部的生产与 offset 提交，但无法自动覆盖数据库、支付或邮件等外部系统；跨系统仍需 outbox、补偿或可重放状态。

## 常见错误
仅依赖生产者幂等不能消除消费者副作用重复。先提交 offset 再写数据库会在写入失败时跳过消息。去重记录与业务写入若不在同一事务，也会留下不一致窗口。把异常全部重试会让永久坏消息阻塞分区。

## 工程权衡
严格去重提高正确性，但会增加存储、索引和清理成本。较大批次提高吞吐，却扩大重复范围。事务处理简化 Kafka 内部语义，但降低兼容性并增加运行约束。应依据副作用价值选择机制。

## 可观察评分信号
回答应画出处理、业务提交和 offset 提交顺序，说明崩溃点与恢复结果，并用重复检测数、幂等冲突、提交失败、消费重试和业务一致性校验验证方案。
