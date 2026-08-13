# Stage 44B1 中文来源矩阵

Status: APPROVED

操作方批准日期：2026-07-22。

审查日期：2026-07-22。每个链接均已直接请求并检查页面主体语言、页面标题和与单元核心论断的匹配关系。`official_cn` 表示发布方维护的中文官方文档；不存在适用官方中文资料时，使用两个不同发布方、不同主机名的 `secondary_cn` 来源交叉验证。矩阵未收录根目录、搜索结果页、问答贴、无作者转载、安装教程、营销页以及检查中发现的 404、403 或正文不匹配页面。

## fastapi_backend

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| FastAPI 项目测试证据 | 测试 | https://fastapi.tiangolo.com/zh/tutorial/testing/ | official_cn | FastAPI | PASS：中文官方正文 | PASS：覆盖 TestClient、测试边界和可验证接口行为 |
| FastAPI 部署与稳定性 | 部署概念 | https://fastapi.tiangolo.com/zh/deployment/concepts/ | official_cn | FastAPI | PASS：中文官方正文 | PASS：覆盖进程、启动、内存、复制和部署边界 |

## fastapi_blocking_io

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| 异步接口与阻塞调用 | 并发与 async/await | https://fastapi.tiangolo.com/zh/async/ | official_cn | FastAPI | PASS：中文官方正文 | PASS：区分异步等待、同步阻塞和线程池适用边界 |

## fastapi_dependency_lifecycle

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| yield 依赖清理顺序 | 使用 yield 的依赖项 | https://fastapi.tiangolo.com/zh/tutorial/dependencies/dependencies-with-yield/ | official_cn | FastAPI | PASS：中文官方正文 | PASS：覆盖进入、响应、异常和退出代码执行顺序 |
| 应用级资源生命周期 | 生命周期事件 | https://fastapi.tiangolo.com/zh/advanced/events/ | official_cn | FastAPI | PASS：中文官方正文 | PASS：覆盖 lifespan 启动与关闭资源边界 |

## fastapi_production

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| 多进程服务模型 | 服务器工作进程 | https://fastapi.tiangolo.com/zh/deployment/server-workers/ | official_cn | FastAPI | PASS：中文官方正文 | PASS：覆盖 Uvicorn 多工作进程及容器部署边界 |
| 启动关闭与资源管理 | 生命周期事件 | https://fastapi.tiangolo.com/zh/advanced/events/ | official_cn | FastAPI | PASS：中文官方正文 | PASS：覆盖优雅启动、关闭和共享资源清理 |
| 生产部署约束 | 部署概念 | https://fastapi.tiangolo.com/zh/deployment/concepts/ | official_cn | FastAPI | PASS：中文官方正文 | PASS：覆盖自动启动、复制、内存和部署策略 |

## fastapi_request_lifecycle

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| ASGI 请求与响应包装 | 中间件 | https://fastapi.tiangolo.com/zh/tutorial/middleware/ | official_cn | FastAPI | PASS：中文官方正文 | PASS：覆盖请求进入、下游调用、响应返回和中间件顺序 |
| 请求体读取与校验 | 请求体 | https://fastapi.tiangolo.com/zh/tutorial/body/ | official_cn | FastAPI | PASS：中文官方正文 | PASS：覆盖模型解析、校验、错误响应和处理函数输入 |

## cache_breakdown

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| 热点键与客户端保护 | 开发的最佳做法 | https://learn.microsoft.com/zh-cn/azure/azure-cache-for-redis/cache-best-practices-development | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖重试、超时、连接复用和缓存故障下的后端保护 |
| 缓存未命中后的回源 | Cache-Aside 模式 | https://learn.microsoft.com/zh-cn/azure/architecture/patterns/cache-aside | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖按需装载、过期、回源和一致性边界；互斥重建是应用层扩展 |

## redis_backend

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| Redis 工程与容量基线 | 开发的最佳做法 | https://learn.microsoft.com/zh-cn/azure/azure-cache-for-redis/cache-best-practices-development | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖连接、超时、重试、负载和故障处理 |
| Redis 可观测证据 | 监视用于 Redis 的 Azure 缓存 | https://learn.microsoft.com/zh-cn/azure/azure-cache-for-redis/cache-how-to-monitor | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖延迟、负载、内存、连接和告警指标 |

## redis_consistency

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| Cache-Aside 一致性边界 | Cache-Aside 模式 | https://learn.microsoft.com/zh-cn/azure/architecture/patterns/cache-aside | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖缓存装载、更新、失效、过期和数据不一致窗口 |

## redis_distributed_lock

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| Redis 锁安全算法 | Redis 分布式锁 | https://redis.ac.cn/docs/latest/develop/use/patterns/distributed-locks/ | secondary_cn | Redis 中文文档镜像 | PASS：中文正文 | PASS：覆盖唯一随机值、有效期、Lua 比较删除和安全性边界 |
| Redis 锁正确实现 | Redis 分布式锁的正确实现方式 | https://www.cnblogs.com/linjiqin/p/8003838.html | secondary_cn | 博客园作者 Ruthless | PASS：中文正文 | PASS：独立覆盖 SET 条件、唯一值、Lua 原子释放和过期时间 |

## redis_operations

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| Redis 运行监控 | 监视用于 Redis 的 Azure 缓存 | https://learn.microsoft.com/zh-cn/azure/azure-cache-for-redis/cache-how-to-monitor | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖服务器负载、连接、命中、内存和延迟观测 |
| Redis 内存治理 | 内存管理的最佳做法 | https://learn.microsoft.com/zh-cn/azure/azure-cache-for-redis/cache-best-practices-memory-management | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖淘汰、内存预留、碎片与容量治理 |

## mysql_backend

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| MySQL 索引与事务基线 | MySQL 的索引 | https://cloud.tencent.com/developer/article/2238573 | secondary_cn | 腾讯云开发者社区作者 | PASS：中文正文 | PASS：覆盖索引结构、联合索引、回表与索引取舍 |
| MySQL 变更与可用性 | InnoDB 和在线 DDL | https://mysql.net.cn/doc/refman/8.0/en/innodb-online-ddl.html | secondary_cn | MySQL 中文手册镜像 | PASS：中文正文 | PASS：覆盖在线 DDL 算法、锁和并发影响 |

## mysql_deadlocks

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| InnoDB 死锁机制 | InnoDB 中的死锁 | https://mysql.net.cn/doc/refman/8.0/en/innodb-deadlocks.html | secondary_cn | MySQL 中文手册镜像 | PASS：中文正文 | PASS：覆盖死锁检测、受害事务回滚和应用重试要求 |
| 死锁案例与锁顺序 | mysql 死锁问题分析 | https://www.cnblogs.com/LBSer/p/5183300.html | secondary_cn | 博客园作者 zhanlijun | PASS：中文正文 | PASS：独立覆盖等待关系、加锁顺序和回滚分析 |

## mysql_indexing

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| 多列索引最左前缀 | 多列索引 | https://mysql.net.cn/doc/refman/8.0/en/multiple-column-indexes.html | secondary_cn | MySQL 中文手册镜像 | PASS：中文正文 | PASS：覆盖最左前缀、多列查询和索引选择 |
| 联合索引与回表 | MySQL 的索引 | https://cloud.tencent.com/developer/article/2238573 | secondary_cn | 腾讯云开发者社区作者 | PASS：中文正文 | PASS：独立覆盖联合索引、覆盖索引和回表行为 |

## mysql_isolation

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| InnoDB 隔离与锁 | 事务隔离级别 | https://mysql.net.cn/doc/refman/8.0/en/innodb-transaction-isolation-levels.html | secondary_cn | MySQL 中文手册镜像 | PASS：中文正文 | PASS：覆盖 RC/RR、快照读、锁定读和间隙锁差异 |
| 事务隔离交叉验证 | MySQL 的事务 | https://cloud.tencent.com/developer/article/2238570 | secondary_cn | 腾讯云开发者社区作者 | PASS：中文正文 | PASS：独立覆盖事务特性、隔离级别与并发现象 |

## mysql_online_migration

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| InnoDB 在线 DDL 能力 | InnoDB 和在线 DDL | https://mysql.net.cn/doc/refman/8.0/en/innodb-online-ddl.html | secondary_cn | MySQL 中文手册镜像 | PASS：中文正文 | PASS：覆盖 ALGORITHM、LOCK、并发、失败和资源边界 |
| 在线 DDL 算法边界 | InnoDB and Online DDL 读书笔记 | https://www.cnblogs.com/xuliuzai/p/18111648 | secondary_cn | 博客园作者东山絮柳仔 | PASS：中文正文 | PASS：独立覆盖 INSTANT/INPLACE、锁和操作支持范围；业务回填与回滚需另行设计 |

## rocketmq_backend

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| RocketMQ 普通消息与消费基线 | RocketMQ 普通消息 | https://rocketmq.apache.org/zh/docs/featureBehavior/01normalmessage/ | official_cn | Apache RocketMQ | PASS：中文官方正文 | PASS：覆盖 Topic、消息投递与消费基本边界 |
| RocketMQ 重试与死信基线 | RocketMQ 消费者重试策略 | https://rocketmq.apache.org/zh/docs/featureBehavior/10consumerretrypolicy/ | official_cn | Apache RocketMQ | PASS：中文官方正文 | PASS：覆盖消费失败、重试次数和死信治理 |

## rocketmq_delivery

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| RocketMQ 至少一次消费边界 | RocketMQ 普通消息 | https://rocketmq.apache.org/zh/docs/featureBehavior/01normalmessage/ | official_cn | Apache RocketMQ | PASS：中文官方正文 | PASS：覆盖消息投递、消费结果与重复投递边界 |
| RocketMQ 重试与幂等边界 | RocketMQ 消费者重试策略 | https://rocketmq.apache.org/zh/docs/featureBehavior/10consumerretrypolicy/ | official_cn | Apache RocketMQ | PASS：中文官方正文 | PASS：覆盖消费失败重试；业务副作用仍需幂等保护 |

## rocketmq_operations

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| RocketMQ 消费运行状态 | RocketMQ 普通消息 | https://rocketmq.apache.org/zh/docs/featureBehavior/01normalmessage/ | official_cn | Apache RocketMQ | PASS：中文官方正文 | PASS：支撑生产/消费速率、积压和消费组运行边界 |
| RocketMQ 失败消费治理 | RocketMQ 消费者重试策略 | https://rocketmq.apache.org/zh/docs/featureBehavior/10consumerretrypolicy/ | official_cn | Apache RocketMQ | PASS：中文官方正文 | PASS：支撑重试量、死信量和失败恢复观测 |

## rocketmq_retry_dead_letter

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| RocketMQ 有限重试 | RocketMQ 消费者重试策略 | https://rocketmq.apache.org/zh/docs/featureBehavior/10consumerretrypolicy/ | official_cn | Apache RocketMQ | PASS：中文官方正文 | PASS：覆盖重试策略、最大重试次数与消费组边界 |
| RocketMQ 死信消息 | RocketMQ 消费者重试策略 | https://rocketmq.apache.org/zh/docs/featureBehavior/10consumerretrypolicy/ | official_cn | Apache RocketMQ | PASS：中文官方正文 | PASS：覆盖超过重试上限后进入死信队列；回放仍需审批、限速和幂等 |

## rocketmq_load_balancing

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| RocketMQ 集群消费与队列分配 | RocketMQ 普通消息 | https://rocketmq.apache.org/zh/docs/featureBehavior/01normalmessage/ | official_cn | Apache RocketMQ | PASS：中文官方正文 | PASS：支撑 Topic、消息队列和消费组的集群消费边界 |
| RocketMQ 故障后的重复处理 | RocketMQ 消费者重试策略 | https://rocketmq.apache.org/zh/docs/featureBehavior/10consumerretrypolicy/ | official_cn | Apache RocketMQ | PASS：中文官方正文 | PASS：支撑重新分配期间在途消息重试和业务幂等要求 |

## capacity_planning

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| 峰值、增长与余量 | 容量规划的体系结构策略 | https://learn.microsoft.com/zh-cn/azure/well-architected/performance-efficiency/capacity-planning | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖基线、峰值、增长、瓶颈、负载测试和容量模型 |

## cascading_failures

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| 重试放大边界 | 重试模式 | https://learn.microsoft.com/zh-cn/azure/architecture/patterns/retry | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖重试风暴风险、退避和失败分类 |
| 熔断与恢复探测 | 断路器模式 | https://learn.microsoft.com/zh-cn/azure/architecture/patterns/circuit-breaker | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖快速失败、半开探测和恢复边界 |
| 故障域隔离 | 舱壁模式 | https://learn.microsoft.com/zh-cn/azure/architecture/patterns/bulkhead | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖资源池隔离和级联故障限制 |

## queue_backpressure

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| 队列调平与负载保护 | 基于队列的负载调平模式 | https://learn.microsoft.com/zh-cn/azure/architecture/patterns/queue-based-load-leveling | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖峰值缓冲、服务速率、队列积压和容量限制 |
| 消费者并发扩缩 | 竞争消费者模式 | https://learn.microsoft.com/zh-cn/azure/architecture/patterns/competing-consumers | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖并行消费、动态扩缩和顺序/竞争边界 |

## service_scaling

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| 自动扩缩容与瓶颈 | 自动缩放指南 | https://learn.microsoft.com/zh-cn/azure/architecture/best-practices/auto-scaling | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖横向/纵向扩展、指标、冷却和下游限制 |
| 状态与外部依赖 | 可靠性设计原则 | https://learn.microsoft.com/zh-cn/azure/well-architected/reliability/principles | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖冗余、故障域、恢复和依赖边界 |

## system_design_backend

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面语言检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| 后端可靠性评价框架 | 可靠性设计原则 | https://learn.microsoft.com/zh-cn/azure/well-architected/reliability/principles | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖需求、故障假设、恢复、冗余和持续验证 |
| 健康检查与可验证证据 | 健康终结点监视模式 | https://learn.microsoft.com/zh-cn/azure/architecture/patterns/health-endpoint-monitoring | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖健康检查、外部监控、告警和可用性证据 |
| 容量与演进 | 容量规划的体系结构策略 | https://learn.microsoft.com/zh-cn/azure/well-architected/performance-efficiency/capacity-planning | official_cn | Microsoft Learn | PASS：中文官方正文 | PASS：覆盖容量假设、压测、增长和瓶颈验证 |

### 审批结论

Stage 44B1 的原始 25 个单元及其来源保持历史批准状态。2026-07-30
Memory P1 扩展在用户授权执行计划后完成技术来源复核：以下五个 Microsoft Learn
官方中文页面均经直接 HTTP 请求确认返回 200，路径和主题与新增单元一致。
生产加载与对外合规声明仍需要独立操作审批。

## Memory P1 PostgreSQL 扩展

| 中文主题 | 来源标题 | HTTPS URL | 类型 | 发布方 | 页面可用性检查 | 论断一致性检查 |
| --- | --- | --- | --- | --- | --- | --- |
| 高可用与故障切换 | Azure Database for PostgreSQL 灵活服务器中的高可用性 | https://learn.microsoft.com/zh-cn/azure/postgresql/high-availability/concepts-high-availability | official_cn | Microsoft Learn | PASS：2026-07-30 直接请求返回 200 | PASS：覆盖冗余、故障转移和恢复边界 |
| 自动备份与时间点恢复 | Azure Database for PostgreSQL 灵活服务器中的备份和还原 | https://learn.microsoft.com/zh-cn/azure/postgresql/backup-restore/concepts-backup-restore | official_cn | Microsoft Learn | PASS：2026-07-30 直接请求返回 200 | PASS：覆盖备份、保留窗口、时间点恢复和恢复边界 |
| 只读副本与复制延迟 | Azure Database for PostgreSQL 灵活服务器中的只读副本 | https://learn.microsoft.com/zh-cn/azure/postgresql/read-replica/concepts-read-replicas | official_cn | Microsoft Learn | PASS：2026-07-30 直接请求返回 200 | PASS：覆盖副本、异步复制和读扩展边界 |
| 服务限制与连接预算 | Azure Database for PostgreSQL 灵活服务器中的限制 | https://learn.microsoft.com/zh-cn/azure/postgresql/configure-maintain/concepts-limits | official_cn | Microsoft Learn | PASS：2026-07-30 直接请求返回 200 | PASS：覆盖实例限制、连接与容量边界 |
| 指标、日志和告警 | 监视 Azure Database for PostgreSQL | https://learn.microsoft.com/zh-cn/azure/postgresql/monitor/concepts-monitoring | official_cn | Microsoft Learn | PASS：2026-07-30 直接请求返回 200 | PASS：覆盖监控指标、日志和诊断边界 |

新增六个版本化扩展单元位于
`app/data/knowledge_v2/extensions/memory_p1/`。历史
`stage44b1-zh-v2` 构建显式排除 extensions，继续保持 25 个单元；
当前 `memory-p1-zh-v4` 构建包含 31 个单元，其中消息域的 5 个单元已
从 Kafka 替换为 RocketMQ。任何后续来源变化都必须
重新执行 schema、manifest、retrieval 和来源矩阵审查。
