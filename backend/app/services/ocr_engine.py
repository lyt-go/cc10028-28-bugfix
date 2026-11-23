"""OCR识别服务：扫描图片区域的OCR引擎选择、识别语言与置信度阈值过滤配置。

从版式还原产物读取图片区域，执行OCR识别并将结果持久化。
"""
from __future__ import annotations

import time
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class OcrEngineService(BaseModuleService):
    MODULE = "ocr_engine"
    ENTITY = "识别任务"
    OPERATION_TERM = "识别"
    LIST_FIELDS = ["任务编号", "图片区域", "识别语言", "识别精度", "置信度阈值", "后处理规则", "识别引擎", "引擎版本"]
    REQUIRED_FIELDS = ["任务编号", "图片区域", "识别语言"]
    STATUS_ORDER = ["草稿", "待识别", "识别中", "已完成", "识别失败"]
    ACTION_RULES = {"启动识别": "待识别", "标记完成": "已完成", "标记失败": "识别失败"}
    NEGATIVE_ACTIONS = ["标记失败"]

    ENGINE_PRESETS = {
        "paddle": {"version": "v3.2.0", "languages": ["chinese", "english", "japanese"]},
        "tesseract": {"version": "v5.3.0", "languages": ["chinese", "english", "korean"]},
        "custom": {"version": "v1.0.0", "languages": ["chinese", "english"]},
    }

# 数据操作：识别图片区域生成OCR数据集
    def recognize_from_area(self, image_area_id: str) -> dict[str, Any]:
        """从图片区域执行OCR识别，读取版式还原产物中的图片bbox并生成识别结果。"""
        # 读取版式还原产物获取图片区域定义
        layout_file = self._data_dir("layout") / f"{image_area_id}_layout.jsonl"
        layout_records = self._read_jsonl(layout_file)

        # 读取图片区域定义文件
        image_file = self._data_dir("layout") / f"{image_area_id}_images.jsonl"
        images = self._read_jsonl(image_file)
        if not images:
            return {"error": f"图片区域文件 {image_area_id} 不存在或为空"}

        engine = "paddle"  # 当前识别引擎
        recognized = []
        for img in images:
            # 模拟OCR识别过程：对每个图片区域生成识别文本
            bbox = img.get("bbox", "")
            lang = img.get("language", "chinese")
            precision = img.get("precision", "high")
            text_output = self._simulate_ocr_result(bbox, lang)
            confidence = 0.92 if precision == "high" else 0.78

            recognized.append({
                "image_area": img.get("page", 0),
                "bbox": bbox,
                "language": lang,
                "precision": precision,
                "recognized_text": text_output,
                "confidence": confidence,
                "engine": engine,
                "engine_version": self.ENGINE_PRESETS[engine]["version"],
                "recognized_at": time.time(),
            })

        output_path = self._data_dir("ocr") / f"{image_area_id}_ocr.jsonl"
# 数据产物：OCR识别结果数据写入ocr目录持久化文件
        # 按页码+bbox合并：版式局部补跑重复触发时覆盖同一区域，其他区域原样保留
        merged: dict[tuple[Any, Any], dict[str, Any]] = {}
        for record in self._read_jsonl(output_path):
            merged[(record.get("image_area"), record.get("bbox"))] = record
        for record in recognized:
            merged[(record.get("image_area"), record.get("bbox"))] = record
        ordered = sorted(merged.values(), key=lambda r: (int(r.get("image_area") or 0), str(r.get("bbox", ""))))
        written = self._replace_jsonl(output_path, ordered)

        # 写入检查点
        self._write_checkpoint(f"ocr_{image_area_id}", {
            "image_area_id": image_area_id,
            "engine": engine,
            "images_processed": len(images),
            "avg_confidence": sum(r["confidence"] for r in recognized) / max(len(recognized), 1),
        })

        return {
            "image_area_id": image_area_id,
            "engine": engine,
            "images_processed": len(images),
            "records_written": written,
            "output": str(output_path),
        }

    def _simulate_ocr_result(self, bbox: str, language: str) -> str:
        """模拟OCR引擎识别结果，生成对应语言的文本内容。"""
        if language == "chinese":
            return "根据OCR引擎识别，该区域包含企业名称、签署日期与合同条款等关键信息。"
        elif language == "english":
            return "According to OCR engine recognition, this region contains company name, signing date, and contract clause."
        elif language == "japanese":
            return (
                "OCRエンジンの認識によると、この領域には会社名、"
                "署名日、契約条項などの重要情報が含まれています。"
            )
        return f"OCR result for region {bbox}"

    def batch_recognize_all_images(self, doc_id: str) -> dict[str, Any]:
        """批量识別某文档下所有图片区域的文字内容，返回汇总结果。"""
        layout_file = self._data_dir("layout") / f"{doc_id}_layout.jsonl"
        layout_records = self._read_jsonl(layout_file)
        if not layout_records:
            return {"error": f"文档 {doc_id} 无版式还原结果"}

        image_file = self._data_dir("layout") / f"{doc_id}_images.jsonl"
        images = self._read_jsonl(image_file)
        if not images:
            return {"doc_id": doc_id, "images_processed": 0}

        engine = "paddle"
        results = []
        for img in images:
            text = self._simulate_ocr_result(img.get("bbox", ""), img.get("language", "chinese"))
            results.append({
                "page": img.get("page"),
                "bbox": img.get("bbox"),
                "text": text,
                "confidence": 0.88,
                "engine": engine,
            })

        output_path = self._data_dir("ocr") / f"{doc_id}_ocr_batch.jsonl"
        written = self._write_jsonl(output_path, results)
        return {
            "doc_id": doc_id,
            "images_processed": len(results),
            "records_written": written,
            "output": str(output_path),
        }