---
id: capacity_planning
title: 服务容量规划方法
domain: system-design
source_type: engineering_guide
content_kind: engineering_practice
tags: [system-design, reliability, 容量规划]
aliases: [容量估算, 峰值流量规划]
difficulty: intermediate
question_patterns:
  - 如何根据峰值流量和并发估算服务容量？
  - 容量模型怎样通过压测和生产数据校准？
references:
  - title: 容量规划的体系结构策略
    url: https://learn.microsoft.com/zh-cn/azure/well-architected/performance-efficiency/capacity-planning
    source_kind: official_cn
    publisher: Microsoft Learn
---
# 服务容量规划方法

## 核心结论
容量规划要把业务峰值转换为请求、并发、计算、存储和下游资源需求，再用压测与生产观测校准。单看平均流量会低估突发、增长、故障切换和发布期间的余量。

## 机制与边界
先确定峰值 QPS、请求类型比例和增长周期，用并发约等于到达率乘平均服务时间估算在途请求。计算每实例稳定吞吐，并扣除 CPU、内存、连接池和垃圾回收的安全余量。存储模型包含每日增长、索引、副本、日志和保留期。数据库、缓存、队列和第三方接口分别计算上限。最后用阶梯压测找到饱和点并与生产指标校准。

## 常见错误
以单机极限吞吐直接乘实例数会忽略共享数据库和热点。只看平均延迟会掩盖尾延迟与排队。压测数据分布、缓存命中和请求比例与生产不同，结果不能直接外推。

## 工程权衡
更高余量提高可靠性但增加成本；自动扩容降低长期空闲，却受启动时间和下游容量限制。预留实例适合突发，限流与降级用于超出设计峰值。容量计划需要定期随业务和代码变化更新。

## 可观察评分信号
回答应给出输入假设、计算过程、压测曲线、饱和指标和安全余量，并跟踪 QPS、并发、p95 延迟、资源利用率、队列深度、存储增长和扩容耗时。
