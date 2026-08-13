---
id: rocketmq_operations
title: RocketMQ 消费运行指标
domain: rocketmq
source_type: engineering_guide
content_kind: engineering_practice
tags: [rocketmq, 监控, 消费治理]
aliases: [消费积压, 消费延迟]
technical_terms: [consumer-lag, inflight, drain-time]
topic: rocketmq-operations
difficulty: intermediate
question_patterns:
  - RocketMQ 消费者需要监控哪些运行指标？
  - 如何判断积压来自突发流量、消费故障还是下游容量不足？
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
# RocketMQ 消费运行指标

## 核心结论
消费健康不能只看当前积压量，还要结合积压增长速度、最老消息等待时间、处理吞吐、消费失败与重试、死信增长和下游饱和度。积压是结果指标，定位原因需要同时观察生产速率、Topic 队列分布、消费组实例、Broker 状态和业务依赖。

## 机制与边界
按 Topic、消费组和消息队列记录生产量、消费量、积压量与变化率，并用当前净处理速度估算清空时间。消费者侧关注单条与批次耗时、处理 p95、成功率、重试次数、在途消息和实例存活。Broker 侧观察磁盘、网络、请求延迟、主从复制和路由可用性。告警应区分短暂峰值、持续增长、最老消息超时和死信快速增加。

## 常见错误
只看总积压会掩盖单队列热点。消费者数超过消息队列数并不会继续增加并行度。遇到积压立即扩容可能把数据库或外部接口压垮。忽略重试和死信会让表面消费吞吐失真，而只看消费者 CPU 又会漏掉 Broker、网络和下游瓶颈。

## 工程权衡
更细指标便于定位，但增加标签基数与监控成本。大批次提升吞吐，却增加单批延迟和失败重试范围。扩容前应确认消息队列数、下游容量、连接预算和实例启动时间，并采用逐级放量。保留较长监控历史有利于容量规划，但需要控制消息标识和业务标签中的敏感信息。

## 可观察评分信号
回答应给出生产与消费速率、积压量、最老消息年龄、积压清空时间、处理 p95、失败与重试率、死信增长和下游饱和度，并说明告警阈值、扩容步骤和恢复完成条件。高级回答还应区分 Topic、消费组和单队列热点，并给出容量演练方法。
