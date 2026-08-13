---
id: rocketmq_retry_dead_letter
title: RocketMQ 重试与死信消息治理
domain: rocketmq
source_type: theory
content_kind: failure_mode
tags: [rocketmq, 重试, 死信队列]
aliases: [消费重试, DLQ]
technical_terms: [retry-topic, dead-letter-queue, max-reconsume-times]
topic: retry-dead-letter
difficulty: intermediate
question_patterns:
  - RocketMQ 消息反复消费失败时应如何限制重试并进入死信队列？
  - 死信消息怎样安全回放并保留审计和幂等性？
references:
  - title: RocketMQ 消费者重试策略
    url: https://rocketmq.apache.org/zh/docs/featureBehavior/10consumerretrypolicy/
    source_kind: official_cn
    publisher: Apache RocketMQ
  - title: RocketMQ 普通消息
    url: https://rocketmq.apache.org/zh/docs/featureBehavior/01normalmessage/
    source_kind: official_cn
    publisher: Apache RocketMQ
---
# RocketMQ 重试与死信消息治理

## 核心结论
稳定触发解析、校验或业务规则失败的消息不应无限重试。RocketMQ 会根据消费结果和重试策略安排再次投递，超过最大重试次数后进入消费组对应的死信队列。系统必须把重试、死信、修复和回放设计成一条可审计链路，而不是把死信当作自动消失的错误仓库。

## 机制与边界
瞬时网络、限流或依赖抖动适合带退避的有限重试；格式错误、缺少必填字段或不可满足的业务状态应尽早分类。记录业务事件 ID、原始 Topic、消费组、重试次数、错误类别、处理版本和时间，但正文与异常信息需要脱敏。修复代码或数据后按审批范围、速率和幂等键回放，回放成功前不得删除审计记录。

## 常见错误
所有异常统一立即重试会放大下游压力并制造重试风暴。不同消费者复用同一个消费组却承担不同职责，会让重试和死信归属混乱。只看当前死信数量而不看最老消息年龄，可能掩盖长期无人处理的问题。未经限速直接回放大量死信，容易再次压垮数据库或外部接口。

## 工程权衡
更多重试能吸收短暂故障，但延迟永久错误的发现并占用消费资源。快速进入死信提高主链路可用性，却要求成熟的告警、排障、审批和回放工具。保留更完整上下文便于诊断，但增加隐私和存储风险。治理目标应是可恢复、可追踪和可验证，而不是单纯降低死信数量。

## 可观察评分信号
回答应说明错误分类、重试次数、退避策略、死信进入条件、消费组隔离、回放审批和幂等保护，并监控消费失败率、重试量、死信增长、最老死信年龄、回放速率与成功率。高级回答还应覆盖重试风暴限流、敏感字段脱敏和修复版本追踪。
