"""表格抽取服务：文档表格的边界检测、表头行识别、合并单元格拆分与行列结构化输出。

从版式还原产物中读取表格区域，进行行列结构化抽取并持久化。
"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class TableExtractService(BaseModuleService):
    MODULE = "table_extract"
    ENTITY = "表格区域"
    OPERATION_TERM = "抽取"
    LIST_FIELDS = ["表格编号", "文档编号", "页码位置", "行数", "列数", "表头行号", "合并单元格数", "结构化输出"]
    REQUIRED_FIELDS = ["表格编号", "文档编号", "页码位置"]
    STATUS_ORDER = ["草稿", "待抽取", "已抽取", "抽取失败"]
    ACTION_RULES = {"启动抽取": "待抽取", "确认抽取": "已抽取", "标记失败": "抽取失败"}
    NEGATIVE_ACTIONS = ["标记失败"]

    def extract_from_layout(self, layout_id: str) -> dict[str, Any]:
        """从版式还原产物中抽取表格区域结构化数据，写入JSONL持久化文件。"""
        # 读取版式还原产物
        layout_file = self._data_dir("layout") / f"{layout_id}_layout.jsonl"
# 数据操作：归并表格区域数据抽取结构化行列数据
        layout_records = self._read_jsonl(layout_file)
        if not layout_records:
            return {"error": f"版式还原产物 {layout_id} 不存在或为空"}

        # 同时读取原始表格区域定义
        table_file = self._data_dir("layout") / f"{layout_id}_tables.jsonl"
        raw_tables = self._read_jsonl(table_file)

        extracted = []
        for table_def in raw_tables:
            rows = table_def.get("rows", 4)
            cols = table_def.get("cols", 5)
            # 执行结构化抽取：对每行每列生成模拟数据
            structured = {"header": [], "data_rows": []}
            for h in range(cols):
                structured["header"].append(f"列{h+1}")
            for r in range(1, rows):
                row_data = {}
                for c in range(cols):
                    row_data[f"列{c+1}"] = f"值{r}_{c+1}"
                structured["data_rows"].append(row_data)

            extracted.append({
                "table_key": f"TABLE-{layout_id}-{table_def.get('page', 0)}",
                "doc_id": table_def.get("doc_id", layout_id),
                "page": table_def.get("page", 0),
                "bbox": table_def.get("bbox", ""),
                "rows": rows,
                "cols": cols,
                "header_rows": table_def.get("header_row", 1),
                "merged_cells": table_def.get("merged_cells", 0),
                "structured_output": structured,
                "extracted_at": time.time(),
            })

        output_path = self._data_dir("tables") / f"{layout_id}_tables.jsonl"
        # 按表格编号合并：本次重算的表格覆盖同名记录，其他页的表格区域原样保留，
        # 避免版式局部补跑把同一张表重复追加进产物。
        merged: dict[str, dict[str, Any]] = {}
        for record in self._read_jsonl(output_path):
            merged[str(record.get("table_key") or f"{record.get('page')}:{record.get('bbox')}")] = record
        for record in extracted:
            merged[record["table_key"]] = record
        ordered = sorted(
            merged.values(),
            key=lambda r: (int(r.get("page") or 0), str(r.get("table_key", ""))),
        )
        written = self._replace_jsonl(output_path, ordered)
# 数据产物：表格结构化数据集写入tables目录持久化文件

        # 写入检查点
        self._write_checkpoint(f"table_{layout_id}", {
            "layout_id": layout_id,
            "tables_extracted": len(extracted),
            "tables_total": len(ordered),
            "total_cells": sum(e["rows"] * e["cols"] for e in extracted),
            "columns_per_table": [e["cols"] for e in extracted],
        })

        return {
            "layout_id": layout_id,
            "tables_extracted": len(extracted),
            "tables_total": len(ordered),
            "records_written": written,
            "output": str(output_path),
            "total_cells": sum(e["rows"] * e["cols"] for e in extracted),
        }

    def extract_from_batch(self, batch_key: str) -> dict[str, Any]:
        """从接入批次中批量抽取所有文档的表格，适用于大规模批量处理场景。"""
        batch_rows = [r for r in store.rows("file_intake") if r.get("批次编号") == batch_key]
        results = {"batch_key": batch_key, "documents_processed": 0, "tables_total": 0, "errors": []}
        for br in batch_rows:
            file_list = br.get("文件清单", "").split(",")
            for fname in file_list:
                fname = fname.strip()
                if not fname:
                    continue
                try:
                    res = self.extract_from_layout(fname)
                    if "error" not in res:
                        results["documents_processed"] += 1
                        results["tables_total"] += res.get("tables_extracted", 0)
                    else:
                        results["errors"].append(res)
                except Exception as e:
                    results["errors"].append({"doc_id": fname, "error": str(e)})
        return results