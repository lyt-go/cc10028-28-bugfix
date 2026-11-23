"""跨模块集成测试：验证8-10组真实数据链路的输入→转换→版本化产物→下游消费。"""
import os
import sys
import json as _json
import uuid as _uuid
import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from app.services.file_intake import FileIntakeService
from app.services.format_parse import FormatParseService
from app.services.layout_restore import LayoutRestoreService
from app.services.table_extract import TableExtractService
from app.services.ocr_engine import OcrEngineService
from app.services.chunk_split import ChunkSplitService
from app.services.entity_recognize import EntityRecognizeService
from app.services.relation_extract import RelationExtractService
from app.services.term_normalize import TermNormalizeService
from app.services.sensitive_filter import SensitiveFilterService
from app.services.vector_embed import VectorEmbedService
from app.services.kg_build import KgBuildService
from app.services.extract_quality import ExtractQualityService
from app.services.annot_review import AnnotReviewService
from app.services.doc_version import DocVersionService
from app.services.extract_lineage import ExtractLineageService
from app.services.result_deliver import ResultDeliverService
from app.services.parse_retry import ParseRetryService
from app.services.base import DATA_ROOT


def _uid(prefix="ID"):
    return f"{prefix}-{_uuid.uuid4().hex[:8].upper()}"


class TestFileIntakeToFormatParse:
    """链路1：文件接入→格式解析"""

    def test_onboard_batch_persists(self):
        batch_key = _uid("BI")
        doc_id = _uid("DOC")
        svc = FileIntakeService()
        result = svc.onboard_batch({
            "批次编号": batch_key, "来源系统": "测试", "文件清单": f"{doc_id}.pdf",
            "文件类型": "PDF", "字节总量": "1024000", "校验值": "", "采集方式": "接口", "接入游标": "0",
        })
        assert result["批次编号"] == batch_key
        # 验证IO产物
        onboard_file = DATA_ROOT / "batches" / "onboard.jsonl"
        assert onboard_file.exists()

    def test_validate_batch_writes(self):
        batch_key = _uid("BI")
        doc_id = _uid("DOC")
        svc = FileIntakeService()
        cksum = svc._compute_checksum(f"{doc_id}.pdf")
        created = svc.onboard_batch({
            "批次编号": batch_key, "来源系统": "测试", "文件清单": f"{doc_id}.pdf",
            "文件类型": "PDF", "字节总量": "1024000", "校验值": cksum, "采集方式": "接口", "接入游标": "0",
        })
        eid = created["id"]
        svc.run_action(eid, "提交校验")
        result = svc.validate_batch(eid)
        assert result["valid"] is True
        validated_file = DATA_ROOT / "batches" / "validated.jsonl"
        assert validated_file.exists()


class TestFormatParseToChunkSplit:
    """链路2：格式解析→分块切分→实体识别"""

    def test_execute_parse_produces_output(self):
        doc_id = _uid("DD")
        svc = FormatParseService()
        task, _ = svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
            "页码范围": "1-10", "解析模式": "full", "输出格式": "jsonl", "超时秒数": "300", "解析器版本": "v2.1.0",
        })
        result = svc.execute_parse(task["id"])
        assert "error" not in result, result.get("error", "")
        assert result["pages"] > 0
        parsed_file = DATA_ROOT / "parsed" / f"{doc_id}_parsed.jsonl"
        assert parsed_file.exists()

    def test_chunk_split_reads_parse(self):
        doc_id = _uid("DD")
        svc = FormatParseService()
        task, _ = svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        svc.execute_parse(task["id"])
        chunk_svc = ChunkSplitService()
        result = chunk_svc.split_from_document(f"{doc_id}_parsed")
        assert "error" not in result, result.get("error", "")
        assert result["chunks"] > 0
        chunk_file = DATA_ROOT / "chunks" / f"{doc_id}_parsed_chunks.jsonl"
        assert chunk_file.exists()

    def test_entity_recognize_reads_chunks(self):
        doc_id = _uid("DD")
        svc = FormatParseService()
        task, _ = svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        svc.execute_parse(task["id"])
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        entity_svc = EntityRecognizeService()
        result = entity_svc.recognize_from_chunk(f"{doc_id}_parsed")
        assert "error" not in result, result.get("error", "")
        assert result["entities"] > 0
        entity_file = DATA_ROOT / "entities" / f"{doc_id}_parsed_entities.jsonl"
        assert entity_file.exists()

    def test_relation_extract_reads_entities(self):
        doc_id = _uid("DD")
        svc = FormatParseService()
        task, _ = svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        svc.execute_parse(task["id"])
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        EntityRecognizeService().recognize_from_chunk(f"{doc_id}_parsed")
        rel_svc = RelationExtractService()
        result = rel_svc.extract_from_entities(f"{doc_id}_parsed")
        assert "error" not in result, result.get("error", "")
        assert result["relations"] > 0
        rel_file = DATA_ROOT / "relations" / f"{doc_id}_parsed_relations.jsonl"
        assert rel_file.exists()

    def test_kg_build_reads_relations(self):
        doc_id = _uid("DD")
        svc = FormatParseService()
        task, _ = svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        svc.execute_parse(task["id"])
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        EntityRecognizeService().recognize_from_chunk(f"{doc_id}_parsed")
        RelationExtractService().extract_from_entities(f"{doc_id}_parsed")
        kg_svc = KgBuildService()
        result = kg_svc.build_from_relations(f"{doc_id}_parsed")
        assert "error" not in result, result.get("error", "")
        assert result["triples"] > 0


class TestLayoutRestoreToTableOcr:
    """链路3：版式还原→表格抽取/OCR"""

    def test_layout_restore_produces_output(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        layout_svc = LayoutRestoreService()
        result = layout_svc.restore_from_document(doc_id)
        assert "error" not in result, result.get("error", "")
        assert result["pages"] > 0
        layout_file = DATA_ROOT / "layout" / f"{doc_id}_layout.jsonl"
        assert layout_file.exists()

    def test_table_extract_reads_layout(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        table_file = DATA_ROOT / "layout" / f"{doc_id}_tables.jsonl"
        assert table_file.exists(), f"parse should create table regions for {doc_id}"
        LayoutRestoreService().restore_from_document(doc_id)
        table_svc = TableExtractService()
        result = table_svc.extract_from_layout(doc_id)
        assert "error" not in result, result.get("error", "")
        assert result["tables_extracted"] > 0

    def test_ocr_reads_image_regions(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        LayoutRestoreService().restore_from_document(doc_id)
        ocr_svc = OcrEngineService()
        result = ocr_svc.recognize_from_area(doc_id)
        assert "error" not in result, result.get("error", "")
        assert result["images_processed"] > 0


class TestSecondaryLinks:
    """次级数据链路"""

    def test_sensitive_filter_hits(self):
        svc = SensitiveFilterService()
        result = svc.apply_to_text("手机13800138000邮箱test@example.com")
        assert result["hit_count"] > 0, "应命中手机号和邮箱规则"
        assert "***" in result["filtered_text"] or "[已" in result["filtered_text"]

    def test_term_normalize_maps(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        EntityRecognizeService().recognize_from_chunk(f"{doc_id}_parsed")
        term_svc = TermNormalizeService()
        result = term_svc.normalize_from_entities(f"{doc_id}_parsed")
        assert "error" not in result, result.get("error", "")
        assert result["mappings"] > 0

    def test_vector_embed_reads_chunks(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        vec_svc = VectorEmbedService()
        result = vec_svc.embed_from_chunk(f"{doc_id}_parsed")
        assert "error" not in result, result.get("error", "")
        assert result["chunks"] > 0

    def test_extract_quality_reports(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        EntityRecognizeService().recognize_from_chunk(f"{doc_id}_parsed")
        RelationExtractService().extract_from_entities(f"{doc_id}_parsed")
        quality_svc = ExtractQualityService()
        result = quality_svc.check_from_batch(doc_id)
        assert "error" not in result, result.get("error", "")
        assert result["accuracy"] > 0


class TestDocVersionDelivery:
    """链路：版本快照→结果交付"""

    def test_create_snapshot_and_deliver(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        EntityRecognizeService().recognize_from_chunk(f"{doc_id}_parsed")
        RelationExtractService().extract_from_entities(f"{doc_id}_parsed")
        KgBuildService().build_from_relations(f"{doc_id}_parsed")
        version_svc = DocVersionService()
        snap_result = version_svc.create_snapshot(doc_id, "v1.0")
        assert "error" not in snap_result, snap_result.get("error", "")
        deliver_svc = ResultDeliverService()
        del_result = deliver_svc.deliver_from_snapshot(snap_result["snapshot"]["快照编号"])
        assert "error" not in del_result, del_result.get("error", "")
        assert del_result["total_records"] > 0


class TestLineageReview:
    """血缘与人工复核"""

    def test_lineage_records_chain(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        EntityRecognizeService().recognize_from_chunk(f"{doc_id}_parsed")
        RelationExtractService().extract_from_entities(f"{doc_id}_parsed")
        KgBuildService().build_from_relations(f"{doc_id}_parsed")
        lineage_svc = ExtractLineageService()
        result = lineage_svc.record_from_parse(doc_id)
        assert "error" not in result, result.get("error", "")
        assert result["nodes"] >= 2

    def test_create_review_tasks(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        EntityRecognizeService().recognize_from_chunk(f"{doc_id}_parsed")
        RelationExtractService().extract_from_entities(f"{doc_id}_parsed")
        review_svc = AnnotReviewService()
        result = review_svc.create_review_tasks(doc_id, threshold=0.99)
        assert result["tasks_created"] > 0


class TestParseRetry:
    """重采与中断恢复"""

    def test_retry_from_failures(self):
        batch_key = _uid("BT")
        svc = ParseRetryService()
        result = svc.create_retry_from_failures(batch_key)
        assert "batch_key" in result

    def test_retry_execution(self):
        batch_key = _uid("BT")
        svc = ParseRetryService()
        task = {
            "重采编号": "RETRY-00099", "失败环节": "格式解析",
            "原始批次": batch_key, "失败原因": "测试", "重试次数": "0",
            "重采范围": "DOC-X", "恢复游标": "0", "重采结果": "", "status": "待重采",
        }
        tf = DATA_ROOT / "retries" / f"{batch_key}_retries.jsonl"
        svc._write_jsonl(tf, [task])
        result = svc.execute_retry("RETRY-00099")
        assert result.get("status") != "已放弃", f"不应放弃: {result}"


class TestDataArtifactIntegrity:
    """端到端产物完整性"""

    def test_full_pipeline_artifacts(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        pr = parse_svc.execute_parse(task["id"])
        assert "error" not in pr
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        EntityRecognizeService().recognize_from_chunk(f"{doc_id}_parsed")
        RelationExtractService().extract_from_entities(f"{doc_id}_parsed")
        KgBuildService().build_from_relations(f"{doc_id}_parsed")
        VectorEmbedService().embed_from_chunk(f"{doc_id}_parsed")
        LayoutRestoreService().restore_from_document(doc_id)
        DocVersionService().create_snapshot(doc_id, "v1.0")
        for sub, name in [("parsed", "_parsed"), ("chunks", "_parsed_chunks"),
                          ("entities", "_parsed_entities"), ("relations", "_parsed_relations"),
                          ("graph", "_parsed_graph"), ("vectors", "_parsed_vectors"),
                          ("layout", "_layout"), ("versions", "_vv1.0")]:
            fp = DATA_ROOT / sub / f"{doc_id}{name}.jsonl"
            assert fp.exists(), f"产物文件 {fp} 应存在"


class TestDownstreamConsumption:
    """验证下游模块真实读取上游产物文件"""

    def test_chunk_consumes_parse(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        parsed_file = DATA_ROOT / "parsed" / f"{doc_id}_parsed.jsonl"
        assert parsed_file.exists()
        result = ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        assert "error" not in result, result.get("error", "")
        assert result["chunks"] > 0

    def test_entities_consumes_chunk(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        result = EntityRecognizeService().recognize_from_chunk(f"{doc_id}_parsed")
        assert "error" not in result, result.get("error", "")
        assert result["entities"] > 0

    def test_kg_consumes_relation(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        ChunkSplitService().split_from_document(f"{doc_id}_parsed")
        EntityRecognizeService().recognize_from_chunk(f"{doc_id}_parsed")
        RelationExtractService().extract_from_entities(f"{doc_id}_parsed")
        result = KgBuildService().build_from_relations(f"{doc_id}_parsed")
        assert "error" not in result, result.get("error", "")
        assert result["triples"] > 0


class TestDataQuality:
    """数据质量验证"""

    def test_entity_dedup(self):
        entities = [
            {"entity_id": "E1", "entity_type": "ORG", "entity_text": "X", "confidence": 0.80},
            {"entity_id": "E2", "entity_type": "ORG", "entity_text": "X", "confidence": 0.95},
        ]
        deduped = EntityRecognizeService()._deduplicate_entities(entities)
        assert len(deduped) == 1
        assert deduped[0]["confidence"] == 0.95

    def test_relation_dedup(self):
        relations = [
            {"relation_id": "R1", "head_entity": "A", "relation_type": "X", "tail_entity": "B", "confidence": 0.8},
            {"relation_id": "R2", "head_entity": "A", "relation_type": "X", "tail_entity": "B", "confidence": 0.9},
        ]
        deduped = RelationExtractService()._deduplicate_relations(relations)
        assert len(deduped) == 1
        assert deduped[0]["confidence"] == 0.9

    def test_empty_parse_safe(self):
        result = ChunkSplitService().split_from_document("NONEXISTENT")
        assert "error" in result, "空分支应安全返回error"

    def test_checkpoint_persistence(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        task, _ = parse_svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
        })
        parse_svc.execute_parse(task["id"])
        cp_file = DATA_ROOT / "checkpoints" / f"parse_{doc_id}.json"
        assert cp_file.exists(), f"检查点文件 {cp_file} 应存在"

class TestLocalParseCorrection:
    """局部补跑：范围解析、页组确认与版式还原变动页消费"""

    def _task(self, svc, doc_id, page_range):
        task, _ = svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
            "页码范围": page_range, "解析模式": "full", "输出格式": "jsonl",
            "超时秒数": "300", "解析器版本": "v2.1.0",
        })
        return task

    def test_page_range_parser(self):
        svc = FormatParseService()
        assert svc._parse_page_numbers("", 3) == [1, 2, 3]
        assert svc._parse_page_numbers("2-2", 3) == [2]
        assert svc._parse_page_numbers("1-3,2", 3) == [1, 2, 3]
        # 超出真实页数的终点截到末页，不会虚增完成页
        assert svc._parse_page_numbers("2-99", 3) == [2, 3]
        with pytest.raises(ValueError):
            svc._parse_page_numbers("5-6", 3)
        with pytest.raises(ValueError):
            svc._parse_page_numbers("3-1", 3)
        with pytest.raises(ValueError):
            svc._parse_page_numbers("abc", 3)

    def test_local_run_only_writes_scope_pages(self):
        doc_id = _uid("DD")
        svc = FormatParseService()
        full = svc.execute_parse(self._task(svc, doc_id, "" )["id"])
        assert "error" not in full, full.get("error")
        correction = svc.execute_parse(self._task(svc, doc_id, "2-2")["id"])
        assert "error" not in correction, correction.get("error")
        # 本次产物页码只有第 2 页，不把范围外的页算成本次完成页数
        assert correction["page_numbers"] == [2]
        assert correction["pages"] == 1
        # 合并后的整篇页组仍是 1-3，且没有重复页
        assert correction["merged_pages"] == [1, 2, 3]
        on_disk = svc._read_jsonl(DATA_ROOT / "parsed" / f"{doc_id}_parsed.jsonl")
        assert [r["page_num"] for r in on_disk] == [1, 2, 3]
        # 第 1、3 页保留全量解析的来源，只有第 2 页换成新页组
        runs = {r["page_num"]: r["parse_run"] for r in on_disk}
        assert runs[1] == full["run_id"]
        assert runs[3] == full["run_id"]
        assert runs[2] == correction["run_id"]

    def test_page_group_records_confirmed_scope(self):
        doc_id = _uid("DD")
        svc = FormatParseService()
        svc.execute_parse(self._task(svc, doc_id, "")["id"])
        correction = svc.execute_parse(self._task(svc, doc_id, "2-2")["id"])
        group = svc._read_json(svc._page_group_path(doc_id))
        assert group["run_id"] == correction["run_id"]
        assert group["scope_pages"] == [2]
        assert group["pages"] and all(p["page"] == 2 for p in group["pages"])
        assert group["merged_pages"] == [1, 2, 3]
        assert group["confirmed"] is True
        assert group["confirmed_pages"] == [2]

    def test_layout_consumes_page_group_and_keeps_old_pages(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        full = parse_svc.execute_parse(self._task(parse_svc, doc_id, "")["id"])
        layout_svc = LayoutRestoreService()
        before = {r["page"]: r for r in layout_svc._read_jsonl(DATA_ROOT / "layout" / f"{doc_id}_layout.jsonl")}
        assert sorted(before) == [1, 2, 3]

        correction = parse_svc.execute_parse(self._task(parse_svc, doc_id, "2-2")["id"])
        assert "error" not in correction, correction.get("error")
        after = {r["page"]: r for r in layout_svc._read_jsonl(DATA_ROOT / "layout" / f"{doc_id}_layout.jsonl")}
        assert sorted(after) == [1, 2, 3]
        # 只有更正页换成新页组，其他页沿用旧版式记录
        assert after[2]["parse_run"] == correction["run_id"]
        assert after[1]["parse_run"] == full["run_id"]
        assert after[3]["parse_run"] == full["run_id"]
        assert after[1]["version"] == before[1]["version"]
        assert after[3]["table_bboxes"] == before[3]["table_bboxes"]

        layout_group = layout_svc._read_json(layout_svc._data_dir("page_groups") / f"{doc_id}_layout_group.json")
        assert layout_group["scope_pages"] == [2]
        assert layout_group["pages_restored"] == [2]
        assert layout_group["carried_pages"] == [1, 3]
        assert layout_group["confirmed"] is True

        cp = layout_svc._read_checkpoint(f"layout_{doc_id}")
        assert cp["restored_pages"] == [2]

    def test_layout_direct_call_with_page_group(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        parse_svc.execute_parse(self._task(parse_svc, doc_id, "")["id"])
        correction = parse_svc.execute_parse(self._task(parse_svc, doc_id, "2-2")["id"])
        group = parse_svc._read_json(parse_svc._page_group_path(doc_id))
        # 版式还原被单独调用时读取已确认解析页组，只处理变动页
        result = LayoutRestoreService().restore_from_document(doc_id, page_group=group)
        assert "error" not in result, result.get("error")
        assert result["pages_restored"] == [2]
        assert result["carried_pages"] == [1, 3]
        assert result["pages"] == 3

    def test_layout_scope_mismatch_is_rejected(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        parse_svc.execute_parse(self._task(parse_svc, doc_id, "")["id"])
        group = parse_svc._read_json(parse_svc._page_group_path(doc_id))
        bad_group = dict(group, scope_pages=[2, 9], pages=[{"page": 2}, {"page": 9}], run_id="PG-BAD")
        result = LayoutRestoreService().restore_from_document(doc_id, page_group=bad_group)
        assert "error" in result, "页组含不存在的页时应拒绝还原"
        # 失败时不能留下半套版式
        layout_pages = [r["page"] for r in LayoutRestoreService()._read_jsonl(
            DATA_ROOT / "layout" / f"{doc_id}_layout.jsonl")]
        assert layout_pages == [1, 2, 3]

    def test_local_run_without_full_history_is_refused(self):
        doc_id = _uid("DD")
        svc = FormatParseService()
        result = svc.execute_parse(self._task(svc, doc_id, "2-2")["id"])
        assert "error" in result
        assert "全量解析" in result["error"]
        parsed_file = DATA_ROOT / "parsed" / f"{doc_id}_parsed.jsonl"
        assert not parsed_file.exists(), "缺全量结果时不得落盘半次产物"

    def test_out_of_range_end_clamped_not_counted(self):
        doc_id = _uid("DD")
        svc = FormatParseService()
        full = svc.execute_parse(self._task(svc, doc_id, "")["id"])
        result = svc.execute_parse(self._task(svc, doc_id, "2-99")["id"])
        assert "error" not in result, result.get("error")
        # 范围外页码不计入完成页数，只完成真实的 2-3 页
        assert result["page_numbers"] == [2, 3]
        assert result["pages"] == 2
        assert result["merged_pages"] == [1, 2, 3]
        runs = {r["page_num"]: r["parse_run"] for r in svc._read_jsonl(
            DATA_ROOT / "parsed" / f"{doc_id}_parsed.jsonl")}
        assert runs[1] == full["run_id"]

    def test_rerun_same_page_is_idempotent(self):
        doc_id = _uid("DD")
        svc = FormatParseService()
        svc.execute_parse(self._task(svc, doc_id, "")["id"])
        first = svc.execute_parse(self._task(svc, doc_id, "2-2")["id"])
        second = svc.execute_parse(self._task(svc, doc_id, "2-2")["id"])
        pages = [r["page_num"] for r in svc._read_jsonl(DATA_ROOT / "parsed" / f"{doc_id}_parsed.jsonl")]
        assert pages == [1, 2, 3]
        layout_pages = [r["page"] for r in LayoutRestoreService()._read_jsonl(
            DATA_ROOT / "layout" / f"{doc_id}_layout.jsonl")]
        assert layout_pages == [1, 2, 3]
        runs = {r["page"]: r["parse_run"] for r in LayoutRestoreService()._read_jsonl(
            DATA_ROOT / "layout" / f"{doc_id}_layout.jsonl")}
        assert runs[2] == second["run_id"] and runs[2] != first["run_id"]


class TestStalePageGroup:
    """旧页组不能代表当前版式"""

    def _task(self, svc, doc_id, page_range=""):
        task, _ = svc.create_entry({
            "任务编号": _uid("PR"), "文档编号": doc_id, "解析器类型": "PDF",
            "页码范围": page_range,
        })
        return task

    def test_stale_group_rejected(self):
        doc_id = _uid("DD")
        svc = FormatParseService()
        first = svc.execute_parse(self._task(svc, doc_id)["id"])
        stale = svc._read_json(svc._page_group_path(doc_id))
        # 又一次补跑改写第 2 页后，旧页组不再代表当前产物
        svc.execute_parse(self._task(svc, doc_id, "2-2")["id"])
        result = LayoutRestoreService().restore_from_document(doc_id, page_group=stale)
        assert "error" in result, "过期页组不得用于版式还原"
        assert stale["run_id"] == first["run_id"]


class TestDownstreamIdempotency:
    """局部补跑下游产物不重复堆积"""

    def _run(self, svc, doc_id, page_range, tag):
        task, _ = svc.create_entry({
            "任务编号": tag, "文档编号": doc_id, "解析器类型": "PDF",
            "页码范围": page_range,
        })
        return svc.execute_parse(task["id"])

    def test_table_and_ocr_not_duplicated(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        self._run(parse_svc, doc_id, "", _uid("PR"))
        self._run(parse_svc, doc_id, "2-2", _uid("PR"))
        table_rows = parse_svc._read_jsonl(DATA_ROOT / "tables" / f"{doc_id}_tables.jsonl")
        assert [(r["page"], r["doc_id"]) for r in table_rows] == [(1, doc_id), (3, doc_id)]
        ocr_rows = parse_svc._read_jsonl(DATA_ROOT / "ocr" / f"{doc_id}_ocr.jsonl")
        assert [(r["image_area"], r["bbox"]) for r in ocr_rows] == [(2, "(10, 9, 160, 159)")]

    def test_regions_carry_real_doc_id(self):
        doc_id = _uid("DD")
        parse_svc = FormatParseService()
        self._run(parse_svc, doc_id, "", _uid("PR"))
        tables = parse_svc._read_jsonl(DATA_ROOT / "layout" / f"{doc_id}_tables.jsonl")
        images = parse_svc._read_jsonl(DATA_ROOT / "layout" / f"{doc_id}_images.jsonl")
        assert tables and all(t["doc_id"] == doc_id for t in tables)
        assert images and all(i["doc_id"] == doc_id for i in images)
