---
id: queue_backpressure
title: 队列背压与准入控制
domain: system-design
source_type: theory
content_kind: mechanism
tags: [system-design, reliability, 背压]
aliases: [队列调平, 流量准入]
difficulty: intermediate
question_patterns:
  - 消费速度低于生产速度时如何实施背压？
  - 队列积压达到上限后系统应该怎样降级？
references:
  - title: 基于队列的负载调平模式
    url: https://learn.microsoft.com/zh-cn/azure/architecture/patterns/queue-based-load-leveling
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 竞争消费者模式
    url: https://learn.microsoft.com/zh-cn/azure/architecture/patterns/competing-consumers
    source_kind: official_cn
    publisher: Microsoft Learn
---
# 队列背压与准入控制

## 核心结论
当到达率长期高于服务率，队列只能延迟过载，不能创造处理能力。背压需要把容量信号传回生产端，通过限流、拒绝、降级或降低采样，保证积压有界并保护核心任务。

## 机制与边界
队列调平吸收短暂峰值，消费者按下游安全速率处理。系统同时限制队列长度、最老消息年龄和每租户配额；接近阈值时逐级减少非关键工作，超过硬上限时明确拒绝。竞争消费者可扩展处理能力，但受分区、数据库连接和热点限制。任务必须有截止时间、幂等键和失败隔离，过期任务应丢弃或转入审计流程。

## 常见错误
只监控消息数量会忽略消息大小和年龄。无限队列会把内存故障变成延迟故障。盲目增加消费者可能压垮数据库，生产者自动重试又会继续加压。没有优先级与租户隔离时，大客户会挤占全部容量。

## 工程权衡
严格拒绝保持系统稳定，却牺牲部分请求；排队提高峰值吸收能力，但增加响应时间和存储成本。公平调度保护租户，可能降低总吞吐。应根据任务价值和时效性确定丢弃、延迟或降级策略。

## 可观察评分信号
回答应说明到达率、服务率、队列上限和准入规则，并监控队列深度、最老消息年龄、清空时间、拒绝率、消费者利用率、下游饱和度和过期任务数。
