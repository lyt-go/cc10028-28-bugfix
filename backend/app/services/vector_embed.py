"""向量嵌入服务：文本分块的向量嵌入模型选择、批次写入与向量索引地址登记。

从分块产物读取文本分块，执行向量嵌入计算并将向量写入索引文件。
"""
from __future__ import annotations

import time
import hashlib
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class VectorEmbedService(BaseModuleService):
    MODULE = "vector_embed"
    ENTITY = "向量化任务"
    OPERATION_TERM = "写入"
    LIST_FIELDS = ["任务编号", "分块编号", "嵌入模型", "向量维度", "批次大小", "向量条数", "索引地址", "模型版本"]
    REQUIRED_FIELDS = ["任务编号", "分块编号", "嵌入模型"]
    STATUS_ORDER = ["草稿", "待嵌入", "嵌入中", "已完成"]
    ACTION_RULES = {"启动嵌入": "待嵌入", "标记完成": "已完成", "标记废弃": "已完成"}
    NEGATIVE_ACTIONS = []

    MODEL_PRESETS = {
        "text2vec-large": {"dimension": 1024, "batch_size": 32},
        "bge-large": {"dimension": 1024, "batch_size": 64},
        "m3e-base": {"dimension": 768, "batch_size": 32},
    }
# 数据操作：写入文本分块向量化生成向量索引数据集

    def embed_from_chunk(self, chunk_set_id: str) -> dict[str, Any]:
        """从分块产物读取文本分块，执行向量嵌入并持久化到向量索引文件。"""
        chunk_file = self._data_dir("chunks") / f"{chunk_set_id}_chunks.jsonl"
        chunks = self._read_jsonl(chunk_file)
        if not chunks:
            return {"error": f"分块产物 {chunk_set_id} 不存在或为空，请先执行分块切分"}

        model = "text2vec-large"
        model_info = self.MODEL_PRESETS.get(model, {"dimension": 1024, "batch_size": 32})
        dimension = model_info["dimension"]

        vectors = []
        for chunk in chunks:
            text = chunk.get("text", "")
            if not text:
                continue
            emb = self._compute_embedding(text, dimension)
            vectors.append({
                "chunk_id": chunk.get("chunk_id"),
# 数据产物：向量索引数据集写入vectors目录持久化文件
                "doc_id": chunk.get("doc_id"),
                "vector_dim": dimension,
                "vector": emb,
                "model": model,
                "text_length": len(text),
                "index": f"vector_index/{chunk.get('chunk_id')}",
                "embedded_at": time.time(),
            })

        output_path = self._data_dir("vectors") / f"{chunk_set_id}_vectors.jsonl"
        # 向量产物按整篇重算，整体替换避免重复补跑把同一批记录追加成多份
        written = self._replace_jsonl(output_path, vectors)

        self._write_checkpoint(f"vector_{chunk_set_id}", {
            "chunk_set_id": chunk_set_id,
            "model": model,
            "dimension": dimension,
            "chunks_embedded": len(vectors),
            "total_dimension": len(vectors) * dimension,
        })

        return {
            "chunk_set_id": chunk_set_id,
            "model": model,
            "dimension": dimension,
            "chunks": len(vectors),
            "records_written": written,
            "output": str(output_path),
        }

    def _compute_embedding(self, text: str, dimension: int) -> list[float]:
        """模拟向量嵌入计算，基于文本哈希生成伪嵌入向量用于测试数据流。"""
        hash_bytes = hashlib.sha256(text.encode("utf-8")).digest()
        vector = []
        for i in range(dimension):
            byte_idx = i % len(hash_bytes)
            value = (hash_bytes[byte_idx] / 255.0) - 0.5
            if i % 3 == 0:
                value *= 1.5
            elif i % 5 == 0:
                value *= 0.8
            vector.append(round(value, 6))
        return vector

    def batch_embed_documents(self, doc_ids: list[str]) -> dict[str, Any]:
        """批量对多个文档执行向量嵌入，返回汇总统计。"""
        results = []
        for doc_id in doc_ids:
            try:
                r = self.embed_from_chunk(f"{doc_id}_parsed")
                if "error" not in r:
                    results.append(r)
            except Exception:
                pass
        return {
            "documents": len(doc_ids),
            "success": len(results),
            "total_vectors": sum(r.get("chunks", 0) for r in results),
        }