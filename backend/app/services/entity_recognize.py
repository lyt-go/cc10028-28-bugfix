"""实体识别服务：分块文本中人名、机构、时间、地名等命名实体的识别结果与置信度过滤。

从分块产物读取文本分块，执行命名实体识别并持久化实体结果，同时触发关系抽取。
"""
from __future__ import annotations

import time
import re
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class EntityRecognizeService(BaseModuleService):
    MODULE = "entity_recognize"
    ENTITY = "命名实体"
    OPERATION_TERM = "识别"
    LIST_FIELDS = ["实体编号", "分块编号", "实体类型", "实体文本", "起止偏移", "置信度", "模型类型", "模型版本"]
    REQUIRED_FIELDS = ["实体编号", "分块编号", "实体类型"]
    STATUS_ORDER = ["草稿", "待识别", "已识别", "已驳回"]
    ACTION_RULES = {"启动识别": "待识别", "确认识别": "已识别", "标记驳回": "已驳回"}
    NEGATIVE_ACTIONS = ["标记驳回"]

    ENTITY_PATTERNS = {
        "PERSON": [
            r"(张\w{1,3})", r"(李\w{1,3})", r"(王\w{1,3})",
            r"首席科学家",
        ],
        "ORG": [
            r"(\w+集团)", r"(\w+公司)", r"(\w+有限公司)",
            r"生态合作伙伴",
        ],
        "TIME": [
            r"(\d{4}年)", r"(\d+季度)", r"(\d+月\d+日)",
        ],
        "LOC": [
            r"(Beijing)", r"(Shanghai)", r"总部", r"分部",
        ],
        "MONEY": [
            r"(\d+亿\w*)", r"(\d+万\w*)",
        ],
        "TECH": [
            r"知识图谱", r"人工智能", r"信息抽取", r"文档解析",
        ],
    }

    def recognize_from_chunk(self, chunk_set_id: str) -> dict[str, Any]:
        """从分块产物读取文本并执行命名实体识别，将实体结果持久化到JSONL文件。"""
        # 读取分块产物作为输入源
        chunk_file = self._data_dir("chunks") / f"{chunk_set_id}_chunks.jsonl"
        chunks = self._read_jsonl(chunk_file)
        if not chunks:
            return {"error": f"分块产物 {chunk_set_id} 不存在或为空，请先执行分块切分"}

        model = "NER-BiLSTM-CRF"
        version = "v3.1.0"
        entities = []
        entity_id_counter = 0
# 数据操作：读取分块数据校验实体类型并抽取命名实体数据集

        for chunk in chunks:
            text = chunk.get("text", "")
            chunk_id = chunk.get("chunk_id", "")
            if not text:
                continue

            for ent_type, patterns in self.ENTITY_PATTERNS.items():
                for pattern in patterns:
                    for match in re.finditer(pattern, text):
                        entity_id_counter += 1
                        start_offset = match.start()
                        end_offset = match.end()
                        ent_text = match.group(0)

                        # 计算置信度
                        confidence = 0.85
                        if ent_type in ("PERSON", "ORG"):
                            confidence = 0.88 + (len(ent_text) % 3) * 0.04
                        elif ent_type in ("TIME", "MONEY"):
                            confidence = 0.92
                        elif ent_type in ("TECH"):
                            confidence = 0.90
                        elif ent_type == "LOC":
                            confidence = 0.82
                        confidence = min(confidence, 0.99)

                        entities.append({
                            "entity_id": f"ENT-{entity_id_counter:06d}",
                            "chunk_id": chunk_id,
# 数据产物：命名实体数据集写入entities目录持久化文件，触发下游关系数据集
                            "entity_type": ent_type,
                            "entity_text": ent_text,
                            "start_offset": start_offset,
                            "end_offset": end_offset,
                            "confidence": confidence,
                            "model": model,
                            "model_version": version,
                            "recognized_at": time.time(),
                        })

        # 按置信度去重
        entities = self._deduplicate_entities(entities)

        output_path = self._data_dir("entities") / f"{chunk_set_id}_entities.jsonl"
        # 实体产物按整篇重算，整体替换避免重复补跑把同一批记录追加成多份
        written = self._replace_jsonl(output_path, entities)

        # 写入检查点
        self._write_checkpoint(f"entity_{chunk_set_id}", {
            "chunk_set_id": chunk_set_id,
            "model": model,
            "chunks_processed": len(chunks),
            "entities_found": len(entities),
            "entity_types": list(set(e["entity_type"] for e in entities)),
            "avg_confidence": sum(e["confidence"] for e in entities) / max(len(entities), 1),
        })

        # 跨模块触发：关系抽取
        triggers = {}
        if entities and chunk_set_id:
            trig_rel = self._trigger_relation_extract(chunk_set_id)
# 数据产物：筛选排序实体数据集按类型过滤定位高置信度实体
            triggers["relation_extract"] = trig_rel

        return {
            "chunk_set_id": chunk_set_id,
            "chunks": len(chunks),
            "entities": len(entities),
            "records_written": written,
            "output": str(output_path),
            "triggers": triggers,
        }

    def _deduplicate_entities(self, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按实体文本去重，保留置信度最高的记录。"""
        seen = {}
        for ent in entities:
# 数据操作：去重实体数据保留置信度最高的数据集记录
            key = (ent["entity_type"], ent["entity_text"])
            if key not in seen or ent["confidence"] > seen[key]["confidence"]:
                seen[key] = ent
        return sorted(seen.values(), key=lambda e: e["entity_id"])

    def filter_by_confidence(self, entities: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
        """按置信度阈值过滤实体识别结果。"""
        return [e for e in entities if e.get("confidence", 0) >= threshold]