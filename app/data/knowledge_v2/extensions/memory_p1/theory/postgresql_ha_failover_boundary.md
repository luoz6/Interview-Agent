---
id: postgresql_ha_failover_boundary
title: PostgreSQL 高可用切换的恢复边界
domain: postgresql
source_type: theory
content_kind: hard_negative
tags: [postgresql, reliability, 高可用]
aliases: [主备切换, 故障转移]
difficulty: advanced
question_patterns:
  - PostgreSQL 开启高可用后为什么仍需要应用重连和幂等设计？
  - 主备切换期间怎样处理在途事务和连接风暴？
references:
  - title: Azure Database for PostgreSQL 灵活服务器中的高可用性
    url: https://learn.microsoft.com/zh-cn/azure/postgresql/high-availability/concepts-high-availability
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 监视 Azure Database for PostgreSQL
    url: https://learn.microsoft.com/zh-cn/azure/postgresql/monitor/concepts-monitoring
    source_kind: official_cn
    publisher: Microsoft Learn
---
# PostgreSQL 高可用切换的恢复边界

## 核心结论
启用高可用不等于应用获得无中断、零数据风险的数据库。故障检测、备库提升、地址切换和客户端重连都需要时间；切换点附近的连接会断开，在途事务可能提交、回滚或处于客户端无法确认结果的状态。应用必须把这段不确定性纳入超时、重试和幂等设计。

## 机制与边界
高可用系统通过冗余节点和故障转移缩短恢复时间，但实际恢复取决于检测窗口、复制状态、网络和客户端 DNS/连接刷新。旧连接不会自动迁移到新主库。对于无法确认结果的写请求，盲目重试可能重复扣款、重复创建或破坏顺序；安全重试需要业务幂等键和结果查询。

## 常见错误
只验证备库存在而不演练切换，无法证明应用恢复。把数据库连接超时设置得很长会拖慢故障感知，设置过短又会在瞬时抖动时制造重连风暴。切换完成后一次性恢复全部流量会让新主库在缓存和连接尚未稳定时再次过载。

## 工程权衡
更快检测可以降低恢复时间，但可能增加误切换。同步程度更高可以降低数据差距，却增加写延迟。应用侧应采用有限重试、指数退避和分批恢复，并为关键写入保留幂等与状态核对。高可用演练必须覆盖连接、任务 worker 和后台批处理。

## 可观察评分信号
回答应明确 RTO/RPO、检测与提升流程、连接重建、未知提交结果、幂等键和分批恢复，并用切换时长、错误率、重连速率、复制状态和业务重复率验证。
