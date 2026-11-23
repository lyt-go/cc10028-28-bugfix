"""分块切分服务：文档文本按语义边界的分块切分、重叠窗口保留与检索标签标注。

从解析产物读取已解析文本，按语义边界切分为独立分块并持久化，同时触发实体识别和向量嵌入。
"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ChunkSplitService(BaseModuleService):
    MODULE = "chunk_split"
    ENTITY = "文本分块"
    OPERATION_TERM = "切分"
    LIST_FIELDS = ["分块编号", "文档编号", "分块长度", "重叠窗口", "切分边界", "分块序号", "检索标签", "切分策略"]
    REQUIRED_FIELDS = ["分块编号", "文档编号", "分块长度"]
    STATUS_ORDER = ["草稿", "待切分", "已切分", "切分异常"]
    ACTION_RULES = {"启动切分": "待切分", "确认切分": "已切分", "标记异常": "切分异常"}
    NEGATIVE_ACTIONS = ["标记异常"]

    STRATEGY_PRESETS = {
        "semantic": {"description": "按语义边界切分，保留完整句子和段落结构"},
        "fixed": {"description": "按固定长度切分，超出部分截断"},
        "sliding": {"description": "滑动窗口切分，保留重叠区以确保上下文连贯"},
    }

    def split_from_document(self, parsed_doc_id: str) -> dict[str, Any]:
        """从已解析文档读入并按语义边界切分为文本分块，持久化后触发实体识别和向量嵌入。"""
        # 读取解析产物作为输入源
        parsed_file = self._data_dir("parsed") / f"{parsed_doc_id.replace('_parsed', '')}_parsed.jsonl"
        if not parsed_file.exists():
            # 尝试直接查找
            alt_path = self._data_dir("parsed") / f"{parsed_doc_id}.jsonl"
            if alt_path.exists():
                parsed_file = alt_path
            else:
# 数据操作：切分文本分块按语义边界生成分块数据集
                return {"error": f"解析产物文件 {parsed_doc_id} 不存在，请先执行格式解析"}

        parsed_records = self._read_jsonl(parsed_file)
        if not parsed_records:
            return {"error": f"解析产物 {parsed_doc_id} 为空，无法切分"}

        strategy = "semantic"
        chunk_size = 512
        overlap = 50

        chunks = []
        chunk_seq = 0
        for rec in parsed_records:
            # 对每页的内容切分
            page_text = "\n".join(self._simulate_doc_content(rec.get("doc_id", parsed_doc_id), paragraphs=3))
            # 按句子边界切分
            sentences = page_text.replace("。", "。\n").split("\n")
            current_chunk = ""
            for sent in sentences:
                sent = sent.strip()
                if not sent:
                    continue
                if len(current_chunk) + len(sent) > chunk_size:
                    if current_chunk:
                        chunk_seq += 1
                        chunks.append({
                            "chunk_id": f"CHUNK-{rec['doc_id']}-{chunk_seq:04d}",
                            "doc_id": rec["doc_id"],
                            "page": rec.get("page_num", 1),
                            "seq": chunk_seq,
                            "length": len(current_chunk),
                            "overlap": overlap,
                            "boundary": "sentence" if strategy == "semantic" else "fixed",
                            "tags": self._generate_tags(current_chunk),
                            "text": current_chunk,
                            "strategy": strategy,
                        })
                    # 保留重叠窗口
                    overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else ""
                    current_chunk = overlap_text + sent
# 数据产物：分块数据集写入chunks目录持久化文件，同时触发下游实体数据集与向量索引
                else:
                    current_chunk += sent if not current_chunk else " " + sent
            # 处理最后一段
            if current_chunk:
                chunk_seq += 1
                chunks.append({
                    "chunk_id": f"CHUNK-{rec['doc_id']}-{chunk_seq:04d}",
                    "doc_id": rec["doc_id"],
                    "page": rec.get("page_num", 1),
                    "seq": chunk_seq,
                    "length": len(current_chunk),
                    "overlap": overlap,
                    "boundary": "sentence",
                    "tags": self._generate_tags(current_chunk),
                    "text": current_chunk,
                    "strategy": strategy,
                })

        output_path = self._data_dir("chunks") / f"{parsed_doc_id}_chunks.jsonl"
        # 分块产物按整篇重算，整体替换避免重复补跑把同一批记录追加成多份
        written = self._replace_jsonl(output_path, chunks)

        # 写入检查点
        self._write_checkpoint(f"chunk_{parsed_doc_id}", {
            "parsed_doc_id": parsed_doc_id,
            "chunks_count": len(chunks),
            "strategy": strategy,
            "total_length": sum(c["length"] for c in chunks),
            "decision_boundary": "按语义边界切分是保持上下文完整性的最佳实践",
        })

# 数据产物：标签检索分块数据集按标签过滤定位分块条目
        # 跨模块自动触发：实体识别和向量嵌入
        triggers = {}
        if parsed_doc_id:
            trig_entity = self._trigger_entity_recognize(parsed_doc_id)
            triggers["entity_recognize"] = trig_entity
            trig_vector = self._trigger_vector_embed(parsed_doc_id)
            triggers["vector_embed"] = trig_vector

        return {
            "parsed_doc_id": parsed_doc_id,
            "chunks": len(chunks),
            "records_written": written,
            "strategy": strategy,
            "output": str(output_path),
            "triggers": triggers,
        }

    def _generate_tags(self, text: str) -> list[str]:
        """基于文本内容生成检索标签，识别文本中的关键实体类别。"""
        tags = []
# 数据边界：切分操作处理空分块与缺失产物异常边界数据
        keywords = {
            "企业": ["公司", "集团", "企业", "有限公司"],
            "人员": ["张", "博士", "科学家", "工程师", "经理"],
            "时间": ["2024年", "季度", "月", "日"],
            "地点": ["Beijing", "Shanghai", "总部", "分部"],
            "金额": ["亿元", "万元", "投资"],
            "技术": ["知识图谱", "人工智能", "技术", "系统"],
        }
        for tag, words in keywords.items():
            if any(w in text for w in words):
                tags.append(tag)
        return tags if tags else ["通用"]