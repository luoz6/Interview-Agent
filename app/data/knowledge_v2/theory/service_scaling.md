---
id: service_scaling
title: 无状态服务扩容边界
domain: system-design
source_type: theory
content_kind: hard_negative
tags: [system-design, 扩容, 无状态服务]
aliases: [横向扩容, 自动扩缩容]
difficulty: beginner
question_patterns:
  - 无状态服务扩容时哪些下游会成为瓶颈？
  - 为什么增加实例不一定提高系统吞吐？
references:
  - title: 自动缩放指南
    url: https://learn.microsoft.com/zh-cn/azure/architecture/best-practices/auto-scaling
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 可靠性设计原则
    url: https://learn.microsoft.com/zh-cn/azure/well-architected/reliability/principles
    source_kind: official_cn
    publisher: Microsoft Learn
---
# 无状态服务扩容边界

## 核心结论
无状态只表示请求处理实例不保存必须留在本机的会话，不代表系统可以无限横向扩容。数据库、缓存、队列、连接池、热点键和外部配额通常会先成为共享瓶颈。

## 机制与边界
会话和任务状态应放入可共享且有一致性边界的存储，实例通过负载均衡接收请求。扩容指标应结合请求量、在途请求、尾延迟和资源饱和，而不是只看 CPU。每个新实例会增加数据库与缓存连接、订阅和预热流量，因此需设全局预算。自动扩容还要考虑冷启动、最小实例、冷却时间和缩容时的在途请求排空。

## 常见错误
把本地内存缓存或临时文件当共享状态会导致请求漂移后丢失数据。仅按 CPU 扩容无法处理 I/O 等待和连接池饱和。快速扩容可能造成缓存击穿与连接风暴，快速缩容可能中断长请求。

## 工程权衡
更多实例提高冗余和吞吐，但增加成本与下游压力。连接代理和缓存能缓解共享瓶颈，却增加新的故障点。预热降低首请求延迟，但延长扩容时间。应为核心依赖单独规划容量和降级。

## 可观察评分信号
回答应列出所有共享状态与配额，给出单实例稳定吞吐、全局连接预算、扩缩容触发和冷却依据，并观察 p95 延迟、下游利用率、冷启动、排空时间和热点分布。
