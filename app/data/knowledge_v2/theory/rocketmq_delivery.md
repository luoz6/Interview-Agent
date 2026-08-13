---
id: rocketmq_delivery
title: RocketMQ 投递语义与幂等消费
domain: rocketmq
source_type: theory
content_kind: mechanism
tags: [rocketmq, 投递语义, 幂等]
aliases: [至少一次投递, 消费确认]
technical_terms: [at-least-once, message-id, idempotency]
topic: delivery-semantics
difficulty: beginner
question_patterns:
  - RocketMQ 至少一次投递下如何避免重复副作用？
  - 消费成功或失败结果怎样影响消息重试和重复？
references:
  - title: RocketMQ 普通消息
    url: https://rocketmq.apache.org/zh/docs/featureBehavior/01normalmessage/
    source_kind: official_cn
    publisher: Apache RocketMQ
  - title: RocketMQ 消费者重试策略
    url: https://rocketmq.apache.org/zh/docs/featureBehavior/10consumerretrypolicy/
    source_kind: official_cn
    publisher: Apache RocketMQ
---
# RocketMQ 投递语义与幂等消费

## 核心结论
RocketMQ 的可靠消费仍应按至少一次语义设计：Broker 保存消息并把它投递给消费组，消费者处理成功后返回成功结果，处理失败、超时或进程异常时可能再次收到同一业务消息。因此消息系统保证送达不等于业务副作用只发生一次，数据库写入、库存扣减、支付通知和外部接口都必须具备可验证的幂等边界。

## 机制与边界
消费者取得消息后应先执行业务，再提交成功结果；如果业务尚未提交就确认成功，故障可能让消息不再进入正常重试。业务已经提交但确认结果未到达 Broker 时，消息可能再次投递。幂等可使用业务事件 ID 唯一约束、消费记录与业务写入同事务、状态机条件更新或下游幂等接口。跨数据库与消息系统的一致性仍需事务消息、outbox、补偿或可重放状态，不能只依赖消息 ID。

## 常见错误
把发送成功理解成消费成功，会遗漏消费者和下游故障。只根据每次投递产生的临时标识去重，无法稳定识别同一业务事件。先返回消费成功再提交数据库会形成丢处理窗口。消费记录与业务写入分属两个事务时，崩溃会留下已去重但业务未完成，或业务完成却没有去重记录的不一致状态。

## 工程权衡
严格去重提高正确性，但增加存储、索引、事务竞争和清理成本。批量消费提高吞吐，却扩大失败后的重复范围。事务消息能缩小本地事务与消息发布之间的窗口，但需要可靠的回查和状态治理。应依据副作用价值、可补偿性与峰值吞吐选择机制，并明确异常恢复责任。

## 可观察评分信号
回答应画出拉取、业务事务、消费结果提交和重试顺序，分析每个崩溃点的恢复结果，并给出重复检测数、幂等冲突、消费失败率、重试次数、业务一致性校验和补偿成功率。高级回答还应说明业务键选择、去重记录生命周期、事务消息回查和跨系统恢复边界。
