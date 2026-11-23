"""格式解析服务：PDF/Word/HTML等格式文档的解析器选择、页码范围切分与输出格式控制。

解析完成后写入已解析文件，自动触发分块切分和版式还原。
同一文档允许多次解析：全量解析建立页组，局部补跑只替换范围内的页，
每次解析都会生成一份按范围确认的页组清单，供版式还原等下游只处理变动页。
"""
from __future__ import annotations

import time
import re
import uuid
from typing import Any

from app.services.base import BaseModuleService, _ensure_dir
from app.store import store


class FormatParseService(BaseModuleService):
    MODULE = "format_parse"
    ENTITY = "解析任务"
    OPERATION_TERM = "解析"
    LIST_FIELDS = ["任务编号", "文档编号", "解析器类型", "页码范围", "解析模式", "输出格式", "超时秒数", "解析器版本"]
    REQUIRED_FIELDS = ["任务编号", "文档编号", "解析器类型"]
    STATUS_ORDER = ["草稿", "待解析", "解析中", "已完成", "解析失败"]
    ACTION_RULES = {"启动解析": "解析中", "标记完成": "已完成", "标记失败": "解析失败"}
    NEGATIVE_ACTIONS = ["标记失败"]

    # 支持的解析器类型与对应正则
    PARSER_REGISTRY = {
        "PDF": {"description": "PDF文档解析器，提取文本段落与页面布局"},
        "DOCX": {"description": "Word文档解析器，提取段落样式与层级结构"},
        "HTML": {"description": "HTML文档解析器，提取标签语义与DOM树"},
# 数据产物：从接入批次中批量创建解析任务数据集
        "TXT": {"description": "纯文本文档解析器，按字符集编码读取"},
        "MARKDOWN": {"description": "Markdown解析器，提取标题层级与代码块"},
    }

    def create_from_batch(self, batch_key: str) -> dict[str, Any]:
        """从接入批次创建解析任务，读取批次中的文档清单逐个生成解析任务。"""
        # 读取已校验批次文件
        batch_file = self._data_dir("batches") / "validated.jsonl"
        batches = self._read_jsonl(batch_file)
        matched = [b for b in batches if b.get("batch_key") == batch_key]
        if not matched:
            return {"error": f"批次 {batch_key} 未找到校验记录，可能未通过校验"}
        batch = matched[0]

        batch_rows = [r for r in store.rows("file_intake") if r.get("批次编号") == batch_key]
        if not batch_rows:
            return {"error": f"批次 {batch_key} 未在file_intake登记"}

        batch_row = batch_rows[0]
        file_list = batch_row.get("文件清单", "").split(",")
        file_type = batch_row.get("文件类型", "PDF")
        parser_type = self.PARSER_REGISTRY.get(file_type, self.PARSER_REGISTRY["PDF"])

        created = []
        for i, fname in enumerate(file_list):
            fname = fname.strip()
            if not fname:
                continue
            task_values = {
                "任务编号": f"PARSE-{batch_key}-{i+1:03d}",
                "文档编号": fname,
                "解析器类型": file_type,
                "页码范围": "",
                "解析模式": "full",
                "输出格式": "jsonl",
                "超时秒数": "300",
                "解析器版本": "v2.1.0",
            }
            result, missing = self.create_entry(task_values)
            if result:
                created.append(result["任务编号"])
        return {"batch_key": batch_key, "tasks_created": len(created), "task_ids": created}

    def execute_parse(self, task_id: int) -> dict[str, Any]:
        """执行文档解析任务，将解析产物按页合并后持久化，同时触发下游分块切分。

        「页码范围」决定本次真正生成哪些页：留空为全量；填「2-2」表示只补跑第 2 页，
        本次产物、返回页数和页组清单都只包含范围内的页，范围外的历史页原样沿用。
        只有本次页组与磁盘上的产物核对一致后才把任务标成已完成。
        """
        task = store.find(self.MODULE, task_id)
        if task is None:
# 数据操作：解析文档生成已解析页面产物与区域检测
            return {"error": f"解析任务 {task_id} 不存在"}
        if task.get("status") not in ("草稿", "待解析", "解析中"):
            return {"error": f"解析任务 {task_id} 状态不允许执行解析，当前状态: {task.get('status')}"}

        task["status"] = "解析中"
        start_time = time.time()
        doc_id = task["文档编号"]
        parser = task["解析器类型"]

        try:
            # 模拟文档解析：先得到整篇页集合，再按页码范围筛出本次真正生成的页
            content = self._simulate_doc_content(doc_id, paragraphs=8)
            all_pages = self._simulate_pages(content)
            try:
                selected_pages = self._parse_page_numbers(task.get("页码范围"), len(all_pages))
            except ValueError as exc:
                task["status"] = "解析失败"
                task["abnormal"] = True
                task["pending"] = False
                return {"task_id": task_id, "doc_id": doc_id, "error": str(exc)}
            range_set = set(selected_pages)
            pages = [p for p in all_pages if p["page"] in range_set]

            # 本轮解析的页组标识：同一文档每次解析唯一，页组用来核对哪几页是本轮产物
            run_id = f"PG-{doc_id}-{uuid.uuid4().hex[:8]}"
            parsed_at = time.time()
            parsed_updates = []
            for p in pages:
                parsed_updates.append({
                    "doc_id": doc_id,
# 数据产物：解析page产物数据写入jsonl文件
                    "page_num": p["page"],
                    "paragraphs": len(p["paragraphs"]),
                    "text_length": p["text_length"],
                    "has_tables": p["has_tables"],
                    "has_images": p["has_images"],
                    "parser": parser,
                    "content_hash": self._compute_checksum("\n".join(p["paragraphs"])),
                    "parse_run": run_id,
                    "parsed_at": parsed_at,
                })

            # 按页合并解析产物：本次范围内的页替换，范围外的页保持原记录
            output_path = self._data_dir("parsed") / f"{doc_id}_parsed.jsonl"
            existing_parsed = self._read_jsonl(output_path)
            existing_pages = {
                int(r["page_num"]) for r in existing_parsed
                if str(r.get("page_num", "")).isdigit()
            }
            # 局部补跑只能建立在整套历史结果之上：缺少范围外的历史页时不得落盘，
            # 否则范围外的页会凭空消失，半次更新会被误当成完整版式。
            prior_pages_needed = set(range(1, len(all_pages) + 1)) - range_set
            missing_prior = sorted(prior_pages_needed - existing_pages)
            if missing_prior:
                raise RuntimeError(
                    f"文档 {doc_id} 缺少页 {missing_prior} 的历史解析结果，"
                    "请先执行全量解析再做按范围补跑")
            merged_parsed = self._merge_records_by_page(
                existing_parsed, parsed_updates, range_set, single_per_page=True)
            written = self._replace_jsonl(output_path, merged_parsed)

            # 表格/图片区域按页合并：本次范围内的页替换，其他页热点区域保持原关联
            table_regions = self._detect_table_regions(pages, doc_id)
            image_regions = self._detect_image_regions(pages, doc_id)
            table_path = self._data_dir("layout") / f"{doc_id}_tables.jsonl"
            image_path = self._data_dir("layout") / f"{doc_id}_images.jsonl"
            merged_tables = self._merge_records_by_page(
                self._read_jsonl(table_path), table_regions, range_set, page_key="page", single_per_page=True)
            merged_images = self._merge_records_by_page(
                self._read_jsonl(image_path), image_regions, range_set, page_key="page", single_per_page=True)
            self._replace_jsonl(table_path, merged_tables)
            self._replace_jsonl(image_path, merged_images)

            # 本轮解析页组：只登记范围内真正生成的页，记录可核对的内容哈希
            page_group = {
                "doc_id": doc_id,
                "run_id": run_id,
                "task_id": task_id,
                "parser": parser,
                "page_range": task.get("页码范围") or "",
                "scope_pages": selected_pages,
                "full_scan": range_set == set(range(1, len(all_pages) + 1)),
                "total_pages": len(all_pages),
                "pages": [
                    {
                        "page": rec["page_num"],
                        "content_hash": rec["content_hash"],
                        "text_length": rec["text_length"],
                        "has_tables": rec["has_tables"],
                        "has_images": rec["has_images"],
                    }
                    for rec in parsed_updates
                ],
                "merged_pages": [int(r["page_num"]) for r in merged_parsed],
                "tables_for_scope": [
                    {"page": t["page"], "bbox": t["bbox"]} for t in table_regions
                ],
                "images_for_scope": [
                    {"page": i["page"], "bbox": i["bbox"]} for i in image_regions
                ],
                "parsed_at": parsed_at,
                "confirmed": False,
            }
            self._write_json(self._page_group_path(doc_id), page_group)
            self._write_page_group_snapshot(doc_id, page_group)

            # 页组写入后必须能与磁盘产物逐页核对，否则不能继续往下游派发
            page_group["merged_verified"] = self._verify_merged_pages(output_path, page_group)
            self._write_json(self._page_group_path(doc_id), page_group)
            if not page_group["merged_verified"]:
                raise RuntimeError(
                    f"文档 {doc_id} 的解析页组与合并产物无法核对，本次解析不登记为完成")

            elapsed = time.time() - start_time
# 数据操作：解析完毕写入检查点，存储数据集至持久化文件
            if elapsed > float(task.get("超时秒数") or 300):
                task["abnormal"] = True

            # 写入检查点
            self._write_checkpoint(f"parse_{doc_id}", {
                "task_id": task_id,
                "doc_id": doc_id,
                "run_id": run_id,
                "pages": len(pages),
                "resolved_pages": selected_pages,
                "merged_pages": [int(r["page_num"]) for r in merged_parsed],
                "records_written": written,
                "elapsed": elapsed,
                "table_count": len(table_regions),
                "image_count": len(image_regions),
            })

            # 跨模块自动触发：分块切分消费整篇合并产物；版式还原只消费本轮页组
            triggers = {}
            trig_chunk = self._trigger_chunk_split(f"{doc_id}_parsed")
            triggers["chunk_split"] = trig_chunk
            trig_layout = self._trigger_layout_restore(doc_id, page_group=page_group)
            triggers["layout_restore"] = trig_layout
            trig_quality = self._trigger_quality_check(doc_id)
            triggers["extract_quality"] = trig_quality

            # 版式还原确认它的页组与本次解析页号一致后，解析页组才算确认完成
            layout_ok = isinstance(trig_layout, dict) and not trig_layout.get("error")
            layout_pages = set(trig_layout.get("pages_restored", [])) if layout_ok else set()
            page_group["confirmed"] = layout_ok and layout_pages == range_set
            page_group["confirmed_pages"] = sorted(layout_pages)
            self._write_json(self._page_group_path(doc_id), page_group)
            self._write_page_group_snapshot(doc_id, page_group)

            if not page_group["confirmed"]:
                task["status"] = "解析失败"
                task["abnormal"] = True
                task["pending"] = True
                return {
                    "task_id": task_id,
                    "doc_id": doc_id,
                    "run_id": run_id,
                    "error": "版式还原未产出与本次解析一致的页组，本次解析不计入完成页数",
                    "resolved_pages": selected_pages,
                    "layout_pages": sorted(layout_pages),
                    "triggers": triggers,
                }

            task["status"] = "已完成"
            task["pending"] = False

            return {
                "task_id": task_id,
                "doc_id": doc_id,
                "run_id": run_id,
                "pages": len(pages),
                "page_numbers": selected_pages,
                "merged_pages": [int(r["page_num"]) for r in merged_parsed],
                "records_written": written,
# 【数据工程】解析操作完成后触发下游分块数据集消费
                "elapsed": round(elapsed, 2),
                "output": str(output_path),
                "page_group": str(self._page_group_path(doc_id)),
                "triggers": triggers,
            }
        except Exception as e:
            task["status"] = "解析失败"
            task["abnormal"] = True
            return {"task_id": task_id, "error": str(e), "doc_id": doc_id}

    def _write_page_group_snapshot(self, doc_id: str, page_group: dict[str, Any]) -> None:
        """按 run_id 留存页组快照，便于复核某次补跑到底生成了哪几页。"""
        history_path = self._data_dir("page_groups") / f"{doc_id}_{page_group['run_id']}.json"
        self._write_json(history_path, page_group)

    def _verify_merged_pages(
        self, output_path: Path, page_group: dict[str, Any]
    ) -> bool:
        """回读合并后的产物，确认每页只有一条记录，且本轮范围内的页内容与页组登记一致。"""
        records = self._read_jsonl(output_path)
        on_disk: dict[int, str] = {}
        duplicated = False
        for record in records:
            try:
                page = int(record.get("page_num"))
            except (TypeError, ValueError):
                continue
            if page in on_disk:
                duplicated = True
            on_disk[page] = record.get("content_hash", "")
        if duplicated:
            return False
        for entry in page_group.get("pages", []):
            page = int(entry["page"])
            if on_disk.get(page) != entry["content_hash"]:
                return False
        return True

    def _simulate_pages(self, content: list[str]) -> list[dict[str, Any]]:
        """模拟将文本内容切分为多页文档，每页含段落、表格和图片区域标记。"""
        pages = []
        for page_num in range(1, 4):
            start = (page_num - 1) * (len(content) // 3 + 1)
            end = min(page_num * (len(content) // 3 + 1), len(content))
            page_lines = content[start:end]
            pages.append({
                "page": page_num,
                "paragraphs": page_lines,
                "text_length": sum(len(l) for l in page_lines),
                "has_tables": page_num % 2 == 1,
                "has_images": page_num == 2,
            })
        return pages

    def _detect_table_regions(self, pages: list[dict[str, Any]], doc_id: str) -> list[dict[str, Any]]:
        """检测页面中的表格区域边界并生成结构化输出标记，区域归属到真实文档编号。"""
        regions = []
        for p in pages:
            if p["has_tables"]:
                regions.append({
                    "page": p["page"],
                    "doc_id": doc_id,
                    "bbox": f"({p['page']*10}, {p['page']*8}, {p['page']*10+200}, {p['page']*8+80})",
                    "rows": 4,
                    "cols": 5,
                    "header_row": 1,
                    "merged_cells": 2,
                })
        return regions

    def _detect_image_regions(self, pages: list[dict[str, Any]], doc_id: str) -> list[dict[str, Any]]:
        """检测页面中的图片区域边界并标记识别语言和精度要求，区域归属到真实文档编号。"""
        regions = []
        for p in pages:
            if p["has_images"]:
                regions.append({
                    "page": p["page"],
                    "doc_id": doc_id,
                    "bbox": f"({p['page']*5}, {p['page']*4+1}, {p['page']*5+150}, {p['page']*4+151})",
                    "language": "chinese",
                    "precision": "high",
                })
        return regions
