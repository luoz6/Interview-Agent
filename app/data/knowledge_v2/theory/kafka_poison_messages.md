---
id: kafka_poison_messages
title: Kafka 坏消息与重试隔离
domain: kafka
source_type: theory
content_kind: failure_mode
tags: [kafka, 坏消息, 死信队列]
aliases: [poison message, DLQ]
difficulty: intermediate
question_patterns:
  - 坏消息反复失败导致分区阻塞时应如何处理？
  - 死信消息怎样安全回放并保留审计？
references:
  - title: 服务总线死信队列
    url: https://learn.microsoft.com/zh-cn/azure/service-bus-messaging/service-bus-dead-letter-queues
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 重试模式
    url: https://learn.microsoft.com/zh-cn/azure/architecture/patterns/retry
    source_kind: official_cn
    publisher: Microsoft Learn
---
# Kafka 坏消息与重试隔离

## 核心结论
坏消息是稳定触发解析、校验或业务失败的记录。对它无限重试会阻塞分区并放大下游压力，应先分类错误，限制重试次数，再把永久失败消息与必要上下文写入独立死信 topic。

## 机制与边界
瞬时网络或限流错误采用带退避的有限重试，格式错误、缺失字段和不可满足业务规则通常直接隔离。死信记录保留原始事件标识、来源 topic、分区、offset、错误分类、处理版本和时间，但敏感数据需脱敏。主消费在安全写入死信后推进 offset。修复程序后按审批范围回放，并使用同一幂等键防止重复副作用。

## 常见错误
无限重试会让一个坏消息占住分区。跳过消息却不保留审计会永久丢失业务。把完整异常堆栈和敏感正文写入死信会造成隐私泄漏。未经限速直接回放大量消息，可能再次压垮下游。

## 工程权衡
内联重试顺序清晰，但阻塞正常消息；重试 topic 提高吞吐，却增加顺序与状态管理复杂度。快速隔离提高可用性，但需要成熟的告警、修复和回放流程。

## 可观察评分信号
回答应说明错误分类、重试次数与退避、死信写入原子性、offset 推进和回放审批，并监控失败率、重试量、DLQ 增长、最老死信年龄和回放成功率。
