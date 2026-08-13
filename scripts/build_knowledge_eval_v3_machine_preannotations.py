from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.knowledge_eval_dataset_v3 import (
    CaseType,
    KnowledgeRetrievalDatasetV3,
    load_knowledge_retrieval_dataset_v3,
)


DEFAULT_MANIFEST = ROOT / "app/data/knowledge_v2/manifest.json"
DEFAULT_AUTHORING_DIR = ROOT / "eval/knowledge-v3/authoring"
DEFAULT_OUTPUT_DIR = ROOT / "eval/knowledge-v3/machine-preannotation"
DATASET_VERSION = "knowledge-eval-v3-machine-preannotation-rmqv4-2026-08-13-v1"
PREANNOTATION_VERSION = "knowledge-eval-v3-machine-preannotation-2026-08-13-v1"
ALL_SOURCE_TYPES = ["theory", "engineering_guide", "expert_benchmark"]


TARGET_ORDER = {
    "fastapi": [
        "fastapi_request_lifecycle",
        "fastapi_dependency_lifecycle",
        "fastapi_blocking_io",
        "fastapi_production",
        "fastapi_backend",
    ],
    "redis": [
        "redis_distributed_lock",
        "redis_consistency",
        "cache_breakdown",
        "redis_operations",
        "redis_backend",
    ],
    "relational-database": [
        "mysql_indexing",
        "postgresql_connection_capacity",
        "mysql_deadlocks",
        "postgresql_replica_lag",
        "mysql_isolation",
        "postgresql_backup_restore_boundary",
        "mysql_online_migration",
        "postgresql_connection_saturation",
        "mysql_backend",
        "postgresql_ha_failover_boundary",
        "postgresql_monitoring_baseline",
    ],
    "rocketmq": [
        "rocketmq_delivery",
        "rocketmq_retry_dead_letter",
        "rocketmq_load_balancing",
        "rocketmq_operations",
        "rocketmq_backend",
    ],
    "system-design": [
        "queue_backpressure",
        "capacity_planning",
        "cascading_failures",
        "service_scaling",
        "system_design_backend",
    ],
    "reliability": [
        "postgresql_connection_saturation",
        "cascading_failures",
        "postgresql_replica_lag",
        "queue_backpressure",
        "postgresql_ha_failover_boundary",
        "capacity_planning",
        "postgresql_backup_restore_boundary",
        "service_scaling",
        "postgresql_monitoring_baseline",
        "postgresql_connection_capacity",
    ],
}


RELATED = {
    "fastapi_request_lifecycle": ["fastapi_dependency_lifecycle"],
    "fastapi_dependency_lifecycle": ["fastapi_request_lifecycle", "fastapi_production"],
    "fastapi_blocking_io": ["fastapi_production"],
    "fastapi_production": ["fastapi_backend", "fastapi_dependency_lifecycle"],
    "fastapi_backend": ["fastapi_production"],
    "redis_distributed_lock": ["redis_consistency"],
    "redis_consistency": ["cache_breakdown"],
    "cache_breakdown": ["redis_consistency", "redis_operations"],
    "redis_operations": ["redis_backend", "cache_breakdown"],
    "redis_backend": ["redis_operations"],
    "mysql_indexing": ["mysql_backend"],
    "mysql_deadlocks": ["mysql_isolation"],
    "mysql_isolation": ["mysql_deadlocks"],
    "mysql_online_migration": ["mysql_backend"],
    "mysql_backend": ["mysql_indexing", "mysql_online_migration"],
    "postgresql_connection_capacity": ["postgresql_connection_saturation"],
    "postgresql_connection_saturation": ["postgresql_connection_capacity", "postgresql_monitoring_baseline"],
    "postgresql_replica_lag": ["postgresql_monitoring_baseline"],
    "postgresql_backup_restore_boundary": ["postgresql_ha_failover_boundary"],
    "postgresql_ha_failover_boundary": ["postgresql_backup_restore_boundary"],
    "postgresql_monitoring_baseline": ["postgresql_connection_saturation", "postgresql_replica_lag"],
    "rocketmq_delivery": ["rocketmq_retry_dead_letter"],
    "rocketmq_retry_dead_letter": ["rocketmq_delivery", "rocketmq_operations"],
    "rocketmq_load_balancing": ["rocketmq_operations"],
    "rocketmq_operations": ["rocketmq_retry_dead_letter", "rocketmq_load_balancing"],
    "rocketmq_backend": ["rocketmq_delivery", "rocketmq_operations"],
    "queue_backpressure": ["capacity_planning", "cascading_failures"],
    "capacity_planning": ["service_scaling"],
    "cascading_failures": ["queue_backpressure"],
    "service_scaling": ["capacity_planning"],
    "system_design_backend": ["capacity_planning", "cascading_failures"],
}


CONFUSERS = {
    "fastapi_request_lifecycle": ["fastapi_dependency_lifecycle", "fastapi_blocking_io"],
    "fastapi_dependency_lifecycle": ["fastapi_request_lifecycle", "fastapi_blocking_io"],
    "fastapi_blocking_io": ["fastapi_request_lifecycle", "fastapi_dependency_lifecycle"],
    "fastapi_production": ["fastapi_backend", "fastapi_blocking_io"],
    "fastapi_backend": ["fastapi_production", "system_design_backend"],
    "redis_distributed_lock": ["redis_consistency", "mysql_isolation"],
    "redis_consistency": ["cache_breakdown", "mysql_isolation"],
    "cache_breakdown": ["redis_consistency", "queue_backpressure"],
    "redis_operations": ["redis_backend", "rocketmq_operations"],
    "redis_backend": ["redis_operations", "mysql_backend"],
    "mysql_indexing": ["mysql_isolation", "postgresql_monitoring_baseline"],
    "mysql_deadlocks": ["mysql_isolation", "redis_distributed_lock"],
    "mysql_isolation": ["mysql_deadlocks", "postgresql_replica_lag"],
    "mysql_online_migration": ["postgresql_backup_restore_boundary", "mysql_backend"],
    "mysql_backend": ["postgresql_monitoring_baseline", "mysql_indexing"],
    "postgresql_connection_capacity": ["postgresql_connection_saturation", "capacity_planning"],
    "postgresql_connection_saturation": ["postgresql_connection_capacity", "cascading_failures"],
    "postgresql_replica_lag": ["postgresql_ha_failover_boundary", "redis_consistency"],
    "postgresql_backup_restore_boundary": ["postgresql_ha_failover_boundary", "mysql_online_migration"],
    "postgresql_ha_failover_boundary": ["postgresql_replica_lag", "postgresql_backup_restore_boundary"],
    "postgresql_monitoring_baseline": ["postgresql_connection_capacity", "redis_operations"],
    "rocketmq_delivery": ["rocketmq_retry_dead_letter", "redis_distributed_lock"],
    "rocketmq_retry_dead_letter": ["rocketmq_delivery", "cascading_failures"],
    "rocketmq_load_balancing": ["rocketmq_operations", "service_scaling"],
    "rocketmq_operations": ["rocketmq_load_balancing", "redis_operations"],
    "rocketmq_backend": ["rocketmq_operations", "system_design_backend"],
    "queue_backpressure": ["cascading_failures", "rocketmq_operations"],
    "capacity_planning": ["service_scaling", "postgresql_connection_capacity"],
    "cascading_failures": ["queue_backpressure", "rocketmq_retry_dead_letter"],
    "service_scaling": ["capacity_planning", "rocketmq_load_balancing"],
    "system_design_backend": ["capacity_planning", "fastapi_backend"],
}


FOCUS = {
    "fastapi_request_lifecycle": "请求从 ASGI 入口到响应和资源清理的执行顺序",
    "fastapi_dependency_lifecycle": "请求级 yield 依赖与应用级 lifespan 资源的所有权",
    "fastapi_blocking_io": "异步接口中阻塞调用造成的事件循环尾延迟",
    "fastapi_production": "工作进程、健康检查、超时和优雅关闭的生产边界",
    "fastapi_backend": "FastAPI 项目是否具备可复现的生产工程证据",
    "redis_distributed_lock": "分布式锁所有者校验、续期和安全释放",
    "redis_consistency": "Cache-Aside 更新顺序和最终一致性窗口",
    "cache_breakdown": "热点键失效后的并发回源保护",
    "redis_operations": "Redis 容量、连接、淘汰和延迟的运行治理",
    "redis_backend": "Redis 项目的数据结构、容量和故障恢复证据",
    "mysql_indexing": "联合索引最左前缀与覆盖索引的适用条件",
    "mysql_deadlocks": "InnoDB 死锁环识别、回滚和有限重试",
    "mysql_isolation": "RC、RR、快照读和当前读的锁边界",
    "mysql_online_migration": "表结构变更的无停机迁移与回滚",
    "mysql_backend": "MySQL 项目的性能与可靠性工程证据",
    "postgresql_connection_capacity": "PostgreSQL 连接池预算与全局连接上限",
    "postgresql_connection_saturation": "连接池饱和、排队和超时放大",
    "postgresql_replica_lag": "只读副本延迟导致的陈旧读取",
    "postgresql_backup_restore_boundary": "备份恢复、PITR 和恢复演练边界",
    "postgresql_ha_failover_boundary": "主备故障转移后的恢复一致性边界",
    "postgresql_monitoring_baseline": "数据库运行指标和可验证监控基线",
    "rocketmq_delivery": "至少一次投递下的幂等消费与确认边界",
    "rocketmq_retry_dead_letter": "有限重试、死信隔离和安全回放",
    "rocketmq_load_balancing": "消费组队列分配、集群消费与重平衡",
    "rocketmq_operations": "消费积压、延迟和清空时间的运行治理",
    "rocketmq_backend": "RocketMQ 项目的投递、消费和运维工程证据",
    "queue_backpressure": "生产速率超过消费速率时的背压和准入控制",
    "capacity_planning": "峰值 QPS、并发、资源余量和容量校准",
    "cascading_failures": "下游变慢、重试风暴和资源耗尽的级联链路",
    "service_scaling": "无状态服务横向扩容时的共享下游瓶颈",
    "system_design_backend": "系统设计中的容量、数据流和故障边界证据",
}


SYMPTOM = {
    "fastapi_request_lifecycle": "中间件、依赖和响应清理顺序难以定位",
    "fastapi_dependency_lifecycle": "请求结束后资源没有按预期释放",
    "fastapi_blocking_io": "CPU 不高但接口 p99 突然升高且其他请求一起等待",
    "fastapi_production": "扩进程后连接数激增并且发布期间请求被中断",
    "fastapi_backend": "项目列出很多组件却拿不出故障与量化改进证据",
    "redis_distributed_lock": "旧持有者误删新持有者的锁并发生并发执行",
    "redis_consistency": "数据库已经更新但缓存仍短暂返回旧值",
    "cache_breakdown": "热点缓存同时过期后数据库瞬间承受大量回源",
    "redis_operations": "缓存命中率正常但内存、连接和尾延迟持续恶化",
    "redis_backend": "项目只报告命中率却无法说明容量和恢复方案",
    "mysql_indexing": "查询有联合索引仍扫描大量记录并发生回表",
    "mysql_deadlocks": "并发事务形成锁等待环并被数据库回滚",
    "mysql_isolation": "同一事务中快照读和加锁读看到不同结果",
    "mysql_online_migration": "大表 DDL 导致锁等待和业务延迟升高",
    "mysql_backend": "数据库优化数字缺少基线、样本和故障条件",
    "postgresql_connection_capacity": "应用实例增加后总连接数逼近数据库上限",
    "postgresql_connection_saturation": "连接池排队拉长并引发入口超时和重试",
    "postgresql_replica_lag": "写入成功后立即从只读节点查询却看不到数据",
    "postgresql_backup_restore_boundary": "有备份文件却无法证明在目标时间内恢复",
    "postgresql_ha_failover_boundary": "主备切换后客户端恢复但读写一致性仍不明确",
    "postgresql_monitoring_baseline": "数据库变慢却没有连接、锁、复制和查询证据",
    "rocketmq_delivery": "消息重复到达并导致业务副作用重复执行",
    "rocketmq_retry_dead_letter": "永久失败消息反复重试并持续压垮下游",
    "rocketmq_load_balancing": "消费组成员变化后队列分配抖动且吞吐下降",
    "rocketmq_operations": "消费积压增长但只看消息总数无法估计恢复时间",
    "rocketmq_backend": "项目只说用了消息队列却没有投递和恢复证据",
    "queue_backpressure": "生产速度长期高于消费速度且队列年龄持续增长",
    "capacity_planning": "平均流量正常但峰值期间资源余量快速耗尽",
    "cascading_failures": "下游变慢触发多层重试并耗尽线程与连接",
    "service_scaling": "服务扩容后吞吐不升反而压垮数据库连接池",
    "system_design_backend": "架构图堆满组件却没有容量和故障假设",
}


ACRONYM = {
    "fastapi": ("ASGI", "请求链路和依赖清理"),
    "redis": ("TTL", "缓存失效与回源风险"),
    "relational-database": ("PITR", "数据库恢复目标"),
    "rocketmq": ("DLQ", "失败消息隔离与回放"),
    "system-design": ("QPS", "容量模型与峰值余量"),
    "reliability": ("RTO/RPO", "故障恢复边界"),
}


ACRONYM_VARIANTS = {
    "fastapi": [("ASGI", "请求链路和依赖清理", "fastapi_request_lifecycle")],
    "redis": [("TTL", "缓存失效与回源风险", "cache_breakdown")],
    "relational-database": [
        ("PITR", "数据库恢复目标", "postgresql_backup_restore_boundary"),
        ("RC/RR", "事务隔离和锁边界", "mysql_isolation"),
        ("DDL", "在线结构迁移风险", "mysql_online_migration"),
    ],
    "rocketmq": [("DLQ", "失败消息隔离与回放", "rocketmq_retry_dead_letter")],
    "system-design": [
        ("QPS", "容量模型与峰值余量", "capacity_planning"),
        ("SLI", "架构选择的可观察证据", "system_design_backend"),
    ],
    "reliability": [("RTO/RPO", "故障恢复边界", "postgresql_backup_restore_boundary")],
}


OUT_OF_DOMAIN_QUERY = {
    "fastapi": "请解释 React 虚拟 DOM 的协调算法和前端组件重渲染优化。",
    "redis": "请说明 Kubernetes CNI 插件如何为 Pod 分配网络地址。",
    "relational-database": "请比较浏览器 CSS Grid 与 Flexbox 的二维布局能力。",
    "rocketmq": "请解释 Kafka ISR 副本集合收缩与高水位推进算法。",
    "system-design": "请说明卷积神经网络反向传播中的梯度计算过程。",
    "reliability": "请解释 Android Activity 的任务栈与生命周期回调。",
}


NO_EVIDENCE_QUERY = {
    "fastapi": "FastAPI 是否内置跨数据中心的全局事务协调器，如何配置共识节点？",
    "redis": "Redis 是否提供原生的关系型外键约束和级联删除执行计划？",
    "relational-database": "MySQL 和 PostgreSQL 是否内置浏览器端 WebRTC 媒体编解码器？",
    "rocketmq": "RocketMQ 是否原生提供 SQL 联表优化器并维护 B-Tree 二级索引？",
    "system-design": "现有语料是否定义了量子纠错码的稳定子测量与解码流程？",
    "reliability": "现有语料是否给出了卫星姿态控制器的卡尔曼滤波参数？",
}


SCENARIO_CONTEXTS = (
    "日常稳定流量",
    "版本发布窗口",
    "突发流量高峰",
    "单可用区故障",
    "下游依赖变慢",
    "实例横向扩容",
    "连接池接近上限",
    "长尾延迟升高",
    "故障恢复演练",
    "容量压测复盘",
    "夜间批处理高峰",
    "跨机房网络抖动",
    "数据迁移期间",
    "冷启动阶段",
    "流量回切阶段",
    "资源配额收紧",
    "异常重试增多",
    "监控告警缺失",
    "灰度实例混跑",
    "服务优雅关闭",
)


ANALYSIS_ANGLES = (
    "机制与执行顺序",
    "所有权与资源释放",
    "失败传播与隔离",
    "容量上限与安全余量",
    "尾延迟与排队信号",
    "恢复条件与验证证据",
    "扩容触发与下游预算",
    "重试边界与幂等保护",
    "可观测指标与告警阈值",
    "发布回滚与兼容边界",
    "数据新鲜度与一致性窗口",
    "热点识别与流量整形",
    "故障演练与审计追踪",
    "成本、复杂度与可靠性取舍",
    "错误分类与有限恢复",
    "跨域消歧与元数据路由",
    "准入拒绝与降级策略",
    "冷启动、预热与排空",
    "基线、样本量与复现实验",
    "边界反例与错误做法",
)


MISCONCEPTION = {
    "fastapi_request_lifecycle": "给路由函数加上 async 声明",
    "fastapi_dependency_lifecycle": "把所有资源都放进全局变量",
    "fastapi_blocking_io": "增加协程数量",
    "fastapi_production": "无限增加 worker 数量",
    "fastapi_backend": "展示自动生成的接口文档",
    "redis_distributed_lock": "设置一个过期时间并直接 DEL",
    "redis_consistency": "先更新缓存再提交数据库事务",
    "cache_breakdown": "给所有键设置相同过期时间",
    "redis_operations": "只看缓存命中率",
    "redis_backend": "把所有数据都放进 Redis",
    "mysql_indexing": "给每一列都建立独立索引",
    "mysql_deadlocks": "无限重试所有失败事务",
    "mysql_isolation": "把隔离级别统一调到最高",
    "mysql_online_migration": "直接在高峰期执行大表 DDL",
    "mysql_backend": "只展示平均查询耗时",
    "postgresql_connection_capacity": "让每个请求新建数据库连接",
    "postgresql_connection_saturation": "继续扩大应用连接池",
    "postgresql_replica_lag": "把所有读取都切到副本",
    "postgresql_backup_restore_boundary": "确认备份任务显示成功",
    "postgresql_ha_failover_boundary": "完成主备切换就视为恢复",
    "postgresql_monitoring_baseline": "只监控 CPU 平均值",
    "rocketmq_delivery": "依赖消息绝不重复",
    "rocketmq_retry_dead_letter": "对所有异常无限立即重试",
    "rocketmq_load_balancing": "不停增加同组消费者",
    "rocketmq_operations": "只看当前积压消息数量",
    "rocketmq_backend": "创建 Topic 就算完成消息治理",
    "queue_backpressure": "使用无限队列吸收所有流量",
    "capacity_planning": "用单机极限吞吐乘实例数",
    "cascading_failures": "让每一层独立进行多次重试",
    "service_scaling": "只根据 CPU 快速扩容",
    "system_design_backend": "堆叠更多中间件名称",
}


def canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _load_slots(authoring_dir: Path) -> list[dict]:
    rows = []
    for name in ("tuning-authoring-template.jsonl", "holdout-authoring-template.jsonl"):
        rows.extend(
            json.loads(line)
            for line in (authoring_dir / name).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return rows


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _select_confuser(
    target_id: str,
    chunks: dict[str, dict],
    *,
    ordinal: int,
    disallowed_ids: set[str],
    require_cross_domain: bool = False,
    require_filter_mismatch: bool = False,
) -> str:
    target = chunks[target_id]
    preferred = CONFUSERS[target_id]
    candidates = preferred[ordinal % len(preferred) :] + preferred[: ordinal % len(preferred)]
    candidates += sorted(chunks)
    for candidate_id in _dedupe(candidates):
        if candidate_id in disallowed_ids or candidate_id == target_id:
            continue
        candidate = chunks[candidate_id]
        if require_cross_domain and candidate["domain"] == target["domain"]:
            continue
        if require_filter_mismatch and (
            candidate["domain"] == target["domain"]
            and candidate["source_type"] == target["source_type"]
        ):
            continue
        return candidate_id
    raise ValueError(f"no valid confuser for {target_id}")


def _query(
    case_type: str,
    group: str,
    target: dict,
    related: dict,
    confuser: dict,
    ordinal: int,
    *,
    acronym: tuple[str, str] | None = None,
) -> str:
    title = target["title"]
    focus = FOCUS[target["chunk_id"]]
    symptom = SYMPTOM[target["chunk_id"]]
    aliases = target.get("aliases") or []
    terms = target.get("technical_terms") or []
    exact = (terms + aliases + [title])[ordinal % len(terms + aliases + [title])]
    alias = (aliases or [title])[ordinal % len(aliases or [title])]
    if case_type == "exact_technical_term":
        return f"在后端面试中，如何准确解释“{exact}”的工作机制、适用边界和验证指标？"
    if case_type == "alias_only":
        return f"面试官提到“{alias}”时具体指什么问题，应怎样设计处理方案并证明它有效？"
    if case_type == "acronym":
        acronym_name, purpose = acronym or ACRONYM[group]
        return f"针对{title}，{acronym_name} 代表什么，如何用它判断{purpose}并验证{focus}？"
    if case_type == "semantic_paraphrase":
        return f"系统出现“{symptom}”，请给出根因链、控制影响的方法以及恢复后的验证证据。"
    if case_type == "chinese_paraphrase":
        return f"不用堆砌组件名，请用中文说明{focus}的因果关系、工程取舍和可观察证据。"
    if case_type == "weak_keyword":
        weak = (target.get("tags") or [target["domain"]])[-1]
        return f"只得到“{weak}”这个模糊线索时，还要追问哪些条件才能定位到{title}而不是相邻问题？"
    if case_type == "multi_topic":
        return f"如果同时出现{symptom}，并且还要处理{related['title']}，怎样划分责任边界并制定联动治理方案？"
    if case_type == "ambiguous":
        return f"“{alias}”容易与{confuser['title']}混淆；在当前后端场景中应依据哪些条件消歧并找到正确证据？"
    if case_type == "hard_negative":
        return f"有人认为只要{MISCONCEPTION[target['chunk_id']]}就能解决{focus}，这个说法为什么不成立，正确边界是什么？"
    if case_type == "out_of_domain":
        return f"与当前{title}槽位相对照，{OUT_OF_DOMAIN_QUERY[group]}"
    if case_type == "no_evidence":
        return f"在核对{title}相关语料时，{NO_EVIDENCE_QUERY[group]}"
    if case_type == "cross_domain_confusion":
        return f"{title}与{confuser['title']}都可能表现为延迟或失败，如何根据机制、所属边界和观测信号区分二者？"
    if case_type == "metadata_routing_error":
        return f"查询中只描述了“{symptom}”，如果检索被错误路由到{confuser['domain']}领域，应如何识别并回到{target['domain']}的正确证据？"
    if case_type == "filter_boundary":
        return f"只允许检索{target['domain']}领域的{target['source_type']}资料时，哪些证据能直接回答{focus}，哪些相似资料必须排除？"
    raise ValueError(f"unsupported case type: {case_type}")


def build_machine_dataset(manifest: dict, slots: list[dict]) -> tuple[dict, dict]:
    chunks = {item["chunk_id"]: item for item in manifest["chunks"]}
    group_ordinals: defaultdict[str, int] = defaultdict(int)
    type_group_ordinals: defaultdict[tuple[str, str], int] = defaultdict(int)
    cases = []
    provenance_cases = []
    for slot in slots:
        group = slot["planned_evaluation_group"]
        case_type = slot["planned_case_type"]
        ordinal = group_ordinals[group]
        group_ordinals[group] += 1
        type_group_ordinal = type_group_ordinals[(group, case_type)]
        type_group_ordinals[(group, case_type)] += 1
        acronym_variant = None
        if case_type == "acronym":
            acronym_name, acronym_purpose, target_id = ACRONYM_VARIANTS[group][
                type_group_ordinal % len(ACRONYM_VARIANTS[group])
            ]
            acronym_variant = (acronym_name, acronym_purpose)
        else:
            target_id = TARGET_ORDER[group][ordinal % len(TARGET_ORDER[group])]
        target = chunks[target_id]
        related_ids = RELATED[target_id]
        related_id = related_ids[ordinal % len(related_ids)]
        related = chunks[related_id]

        expected_no_evidence = case_type in {"out_of_domain", "no_evidence"}
        primary_ids: list[str] = [] if expected_no_evidence else [target_id]
        accepted_ids: list[str] = []
        if case_type == "multi_topic":
            primary_ids.append(related_id)
        elif case_type not in {
            "hard_negative",
            "cross_domain_confusion",
            "metadata_routing_error",
            "filter_boundary",
            "out_of_domain",
            "no_evidence",
        }:
            accepted_ids = [related_id]

        confuser_id = _select_confuser(
            target_id,
            chunks,
            ordinal=ordinal,
            disallowed_ids=set(primary_ids) | set(accepted_ids),
            require_cross_domain=case_type
            in {"hard_negative", "cross_domain_confusion", "metadata_routing_error"},
            require_filter_mismatch=case_type == "filter_boundary",
        )
        confuser = chunks[confuser_id]

        excluded_ids = [confuser_id]
        if expected_no_evidence:
            excluded_ids = _dedupe([target_id, related_id, confuser_id])
        excluded_ids = [
            item
            for item in _dedupe(excluded_ids)
            if item not in set(primary_ids) | set(accepted_ids)
        ]
        allowed_domains = (
            list(slot["allowed_domain_options"])
            if expected_no_evidence
            else _dedupe([chunks[item]["domain"] for item in primary_ids])
        )
        if not set(allowed_domains) <= set(slot["allowed_domain_options"]):
            raise ValueError(f"slot domain mismatch: {slot['slot_id']}")
        source_types = (
            [target["source_type"]]
            if case_type == "filter_boundary" and not expected_no_evidence
            else list(ALL_SOURCE_TYPES)
        )
        canonical_tags = _dedupe(
            [
                target["domain"],
                *[tag for tag in target.get("tags", []) if tag != target["domain"]][:1],
            ]
        )
        base_query = _query(
            case_type,
            group,
            target,
            related,
            confuser,
            ordinal,
            acronym=acronym_variant,
        )
        query = (
            f"{base_query.rstrip('？')}"
            f"（分析重点：{ANALYSIS_ANGLES[ordinal]}；"
            f"工程情境：{SCENARIO_CONTEXTS[ordinal]}；评测领域：{group}）？"
        )
        case = {
            "case_id": slot["case_id"],
            "case_family": slot["case_family"],
            "case_type": case_type,
            "split": slot["split"],
            "evaluation_group": group,
            "query_text": query,
            "canonical_tags": canonical_tags,
            "source_types": source_types,
            "allowed_domains": allowed_domains,
            "primary_relevant_chunk_ids": primary_ids,
            "accepted_related_chunk_ids": accepted_ids,
            "excluded_chunk_ids": excluded_ids,
            "annotator_identity_sha256s": [],
            "annotation_record_sha256s": [],
            "label_consensus_record_sha256": None,
            "expected_no_evidence": expected_no_evidence,
            "top_k": 5,
        }
        cases.append(case)
        provenance_cases.append(
            {
                "case_id": slot["case_id"],
                "slot_id": slot["slot_id"],
                "annotation_origin": "single_model_machine_preannotation",
                "target_chunk_id": None if expected_no_evidence else target_id,
                "related_chunk_id": None if expected_no_evidence else related_id,
                "confuser_chunk_ids": excluded_ids,
                "semantic_family_key": canonical_sha256(
                    {
                        "case_type": case_type,
                        "evaluation_group": group,
                        "target_chunk_ids": primary_ids,
                        "analysis_angle": ANALYSIS_ANGLES[ordinal],
                        "no_evidence_query": (
                            query if expected_no_evidence else None
                        ),
                    }
                ),
                "human_annotators": 0,
                "human_adjudication_complete": False,
                "eligible_as_independent_eval_evidence": False,
            }
        )

    dataset = {
        "version": DATASET_VERSION,
        "corpus_manifest_sha256": manifest["corpus_manifest_sha256"],
        "governance": None,
        "cases": cases,
    }
    provenance_without_hash = {
        "schema_version": "knowledge-eval-v3-machine-preannotation-provenance-v1",
        "preannotation_version": PREANNOTATION_VERSION,
        "dataset_version": DATASET_VERSION,
        "dataset_canonical_sha256": canonical_sha256(dataset),
        "corpus_version": manifest["corpus_version"],
        "corpus_manifest_sha256": manifest["corpus_manifest_sha256"],
        "annotation_origin": "single_model_machine_preannotation",
        "case_count": len(cases),
        "human_annotator_count": 0,
        "human_agreement_measured": False,
        "human_adjudication_complete": False,
        "runnable_with_release_gates": False,
        "eligible_as_independent_eval_evidence": False,
        "requires_human_review": True,
        "cases": provenance_cases,
    }
    provenance = {
        **provenance_without_hash,
        "provenance_sha256": canonical_sha256(provenance_without_hash),
    }
    return dataset, provenance


def validate_machine_dataset(dataset_path: Path, provenance_path: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset = load_knowledge_retrieval_dataset_v3(
        dataset_path,
        manifest=manifest,
        require_release_shape=False,
    )
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    supplied_provenance_hash = provenance.pop("provenance_sha256")
    if canonical_sha256(provenance) != supplied_provenance_hash:
        raise ValueError("machine preannotation provenance SHA-256 mismatch")
    if provenance["dataset_canonical_sha256"] != canonical_sha256(dataset_payload):
        raise ValueError("machine preannotation dataset SHA-256 mismatch")
    if dataset.governance is not None:
        raise ValueError("machine preannotation dataset cannot claim human governance")
    if len(dataset.cases) != 100:
        raise ValueError("machine preannotation dataset requires exactly 100 cases")
    split_counts = Counter(case.split for case in dataset.cases)
    if split_counts != {"tuning": 75, "holdout": 25}:
        raise ValueError("machine preannotation split must be 75 tuning and 25 holdout")
    type_counts = Counter(case.case_type for case in dataset.cases)
    if set(type_counts) != set(CaseType.__args__) or min(type_counts.values()) < 3:
        raise ValueError("machine preannotation must cover every V3 case type")
    family_splits: defaultdict[str, set[str]] = defaultdict(set)
    chunks = {item["chunk_id"]: item for item in manifest["chunks"]}
    for case in dataset.cases:
        family_splits[case.case_family].add(case.split)
        if case.annotator_identity_sha256s or case.annotation_record_sha256s:
            raise ValueError("machine preannotation cannot contain human record hashes")
        if case.label_consensus_record_sha256 is not None:
            raise ValueError("machine preannotation cannot claim human consensus")
        if case.case_type in {"cross_domain_confusion", "metadata_routing_error"}:
            primary_domains = {
                chunks[item]["domain"] for item in case.primary_relevant_chunk_ids
            }
            excluded_domains = {
                chunks[item]["domain"] for item in case.excluded_chunk_ids
            }
            if not any(domain not in primary_domains for domain in excluded_domains):
                raise ValueError(
                    f"cross-domain case lacks a cross-domain confuser: {case.case_id}"
                )
        if case.case_type == "filter_boundary" and not any(
            chunks[item]["domain"] not in case.allowed_domains
            or chunks[item]["source_type"] not in case.source_types
            for item in case.excluded_chunk_ids
        ):
            raise ValueError(
                f"filter-boundary case lacks a filter-mismatched exclusion: {case.case_id}"
            )
        if not case.expected_no_evidence and not case.excluded_chunk_ids:
            raise ValueError(f"evidence case lacks an excluded confuser: {case.case_id}")
    if len(family_splits) != 100 or any(len(splits) != 1 for splits in family_splits.values()):
        raise ValueError("machine preannotation family leakage detected")
    provenance_splits: defaultdict[str, set[str]] = defaultdict(set)
    split_by_case = {case.case_id: case.split for case in dataset.cases}
    for row in provenance["cases"]:
        provenance_splits[row["semantic_family_key"]].add(
            split_by_case[row["case_id"]]
        )
    if any(len(splits) != 1 for splits in provenance_splits.values()):
        raise ValueError("machine preannotation semantic family leakage detected")
    if len(provenance_splits) != 100:
        raise ValueError("machine preannotation semantic family keys must be unique")
    if provenance["human_annotator_count"] != 0:
        raise ValueError("machine preannotation cannot claim human annotators")
    if provenance["eligible_as_independent_eval_evidence"]:
        raise ValueError("machine preannotation cannot claim independent evidence")
    return {
        "status": "valid_machine_preannotation_candidate",
        "dataset_version": dataset.version,
        "dataset_canonical_sha256": provenance["dataset_canonical_sha256"],
        "provenance_sha256": supplied_provenance_hash,
        "case_count": len(dataset.cases),
        "tuning_count": split_counts["tuning"],
        "holdout_count": split_counts["holdout"],
        "case_type_count": len(type_counts),
        "family_count": len(family_splits),
        "no_evidence_count": len(dataset.no_evidence_cases()),
        "evidence_case_count": len(dataset.evidence_cases()),
        "human_annotator_count": 0,
        "eligible_as_independent_eval_evidence": False,
    }


def _write_new_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except FileExistsError as exc:
        raise FileExistsError(f"refusing to overwrite machine preannotation: {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or validate Eval V3 machine preannotations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    build.add_argument("--authoring-dir", type=Path, default=DEFAULT_AUTHORING_DIR)
    build.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    validate.add_argument("--dataset", type=Path, default=DEFAULT_OUTPUT_DIR / "dataset.json")
    validate.add_argument("--provenance", type=Path, default=DEFAULT_OUTPUT_DIR / "provenance.json")
    args = parser.parse_args(argv)
    if args.command == "build":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        dataset, provenance = build_machine_dataset(manifest, _load_slots(args.authoring_dir))
        dataset_path = args.output_dir / "dataset.json"
        provenance_path = args.output_dir / "provenance.json"
        _write_new_json(dataset_path, dataset)
        try:
            _write_new_json(provenance_path, provenance)
            summary = validate_machine_dataset(
                dataset_path,
                provenance_path,
                args.manifest,
            )
        except Exception:
            dataset_path.unlink(missing_ok=True)
            provenance_path.unlink(missing_ok=True)
            raise
    else:
        summary = validate_machine_dataset(args.dataset, args.provenance, args.manifest)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
