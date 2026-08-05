"""2.1.3 数据质量控制模块的宿主级 API 回归测试。"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.dq213_routes import register_dq213_routes


class FakeTimelineDb:
    def query(self, sql, params=None):
        normalized = " ".join(str(sql).split())
        if "FROM mental_health_patients" in normalized and "WHERE patient_id=%s" in normalized:
            return [
                {
                    "id": 1,
                    "patient_id": "P-001",
                    "diagnosis": "抑郁障碍",
                    "admission_date": "2026-01-03",
                    "discharge_date": "2026-01-18",
                    "medication": "舍曲林",
                    "outcome": "好转",
                    "HAMD_total": 18,
                    "HAMA_total": 12,
                    "PHQ9_total": 15,
                }
            ], None
        if "FROM mental_health_patients" in normalized:
            return [
                {
                    "patient_id": "P-001",
                    "diagnosis": "抑郁障碍",
                    "admission_date": "2026-01-03",
                }
            ], None
        if "FROM mental_health_clinical_notes" in normalized:
            return [
                {
                    "id": 2,
                    "note_type": "progress",
                    "note_date": "2026-01-05",
                    "title": "病程记录",
                    "content": "睡眠改善。",
                }
            ], None
        if "FROM mental_health_assessments" in normalized:
            return [
                {
                    "id": 3,
                    "scale_name": "HAMD",
                    "assess_date": "2026-01-06",
                    "total_score": 14,
                    "item_scores": "{\"1\": 2}",
                    "visit_type": "住院",
                }
            ], None
        if "FROM mental_health_med_orders" in normalized:
            return [
                {
                    "id": 4,
                    "drug_name": "舍曲林",
                    "dose": "50mg",
                    "frequency": "每日一次",
                    "route": "口服",
                    "start_date": "2026-01-04",
                    "end_date": None,
                    "status": "active",
                }
            ], None
        if "FROM mental_health_examinations" in normalized:
            return [
                {
                    "id": 5,
                    "exam_type": "头颅 MRI",
                    "exam_date": "2026-01-07",
                    "body_site": "脑",
                    "finding": "未见明显异常",
                    "conclusion": "随访观察",
                }
            ], None
        if "FROM mental_health_lab_reports" in normalized:
            return [
                {
                    "id": 6,
                    "report_date": "2026-01-08",
                    "item_name": "白细胞",
                    "value_num": 6.1,
                    "value_text": None,
                    "unit": "10^9/L",
                    "flag": "normal",
                }
            ], None
        if "FROM mental_health_followups" in normalized:
            return [
                {
                    "id": 7,
                    "visit_date": "2026-02-01",
                    "visit_type": "门诊",
                    "HAMD_total": 8,
                    "HAMA_total": 6,
                    "PHQ9_total": 7,
                    "medication": "舍曲林",
                    "medication_dose_mg": 50,
                    "adverse_events": "[]",
                    "notes": "病情稳定",
                }
            ], None
        if "FROM mental_health_multimodal_assets" in normalized:
            return [
                {
                    "id": 8,
                    "modality": "image",
                    "mime_type": "image/png",
                    "uri": "/medical-assets/P-001/mri.png",
                    "thumbnail_uri": "/medical-assets/P-001/mri-thumb.png",
                    "title": "MRI 影像",
                    "size_bytes": 2048,
                    "checksum": "a" * 64,
                    "event_source_table": "mental_health_examinations",
                    "event_source_id": 5,
                    "metadata_json": "{\"series\": \"T1\"}",
                    "captured_at": "2026-01-07 10:00:00",
                }
            ], None
        return [], None


def _current_user():
    return SimpleNamespace(user_id=7)


def _make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DQ213_PSEUDONYM_SECRET", "test-dq213-secret-at-least-32-bytes")
    monkeypatch.setenv("DQ213_REPORT_DIR", str(tmp_path / "reports"))
    app = FastAPI()
    register_dq213_routes(
        app,
        current_user_dependency=_current_user,
        db_handler=FakeTimelineDb(),
        static_dir="src/frontend/web/dq213",
    )
    return TestClient(app), app


def test_routes_and_production_page_exclude_demo_seed(tmp_path, monkeypatch):
    client, app = _make_client(tmp_path, monkeypatch)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/dq213/health" in paths
    assert "/dq213/qc/assess" in paths
    assert "/dq213/phi/anonymize" in paths
    assert "/dq213/timeline/query" in paths
    assert "/dq213/demo/seed" not in paths

    response = client.get("/dq213-app")
    assert response.status_code == 200
    assert "/dq213/demo/seed" not in response.text


def test_inline_quality_report_and_download(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    response = client.post(
        "/dq213/qc/assess",
        json={
            "rows": [
                {
                    "patient_id": f"P-{index:03d}",
                    "age": 20 + index,
                    "gender": "男" if index % 2 else "女",
                    "diagnosis": "抑郁障碍",
                    "HAMD_total": 8 + index,
                    "HAMA_total": 7 + index,
                    "PHQ9_total": 6 + index,
                    "relapse": 0,
                }
                for index in range(12)
            ],
            "unstructured_rows": [{"content": "完整的医疗文本记录，长度符合要求。"}],
            "multimodal_items": [
                {
                    "asset_id": "IMG-1",
                    "modality": "image",
                    "mime_type": "image/png",
                    "uri": "/medical-assets/IMG-1.png",
                    "size_bytes": 2048,
                    "checksum": "a" * 64,
                }
            ],
            "export": True,
        },
    )
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["ok"] is True
    assert set(result["core_metrics"]) >= {
        "missing_rate",
        "field_anomaly_rate",
        "outlier_rate",
        "unstructured_issue_rate",
        "multimodal_issue_rate",
    }
    report = client.get(f"/dq213/qc/reports/{result['report_id']}")
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("application/json")


def test_phi_and_timeline_end_to_end(tmp_path, monkeypatch):
    client, _ = _make_client(tmp_path, monkeypatch)
    phi = client.post(
        "/dq213/phi/anonymize",
        json={"text": "患者张伟，手机13812345678，病案号ZY20260001。", "mode": "replace"},
    )
    assert phi.status_code == 200
    anonymized = phi.json()["data"]["anonymized"]
    assert "13812345678" not in anonymized
    assert "ZY20260001" not in anonymized

    timeline = client.post(
        "/dq213/timeline/query",
        json={"patient_id": "P-001", "event_types": [], "limit": 500},
    )
    assert timeline.status_code == 200
    assert timeline.json()["data"]["n_events"] == 0

    timeline = client.post(
        "/dq213/timeline/query",
        json={"patient_id": "P-001", "keyword": "MRI", "limit": 500},
    )
    assert timeline.status_code == 200
    result = timeline.json()["data"]
    assert result["ok"] is True
    assert result["by_type"] == {"examination": 1}
    assert result["events"][0]["assets"][0]["modality"] == "image"


def test_phi_can_load_secret_from_restricted_file(tmp_path, monkeypatch):
    from backend.phi_anonymize_service import anonymize_dataset

    secret_file = tmp_path / "dq213-secret"
    secret_file.write_text("file-backed-dq213-secret-at-least-32-bytes", encoding="utf-8")
    secret_file.chmod(0o600)
    monkeypatch.delenv("DQ213_PSEUDONYM_SECRET", raising=False)
    monkeypatch.setenv("DQ213_PSEUDONYM_SECRET_FILE", str(secret_file))

    result = anonymize_dataset([{"patient_id": "P-001"}])
    assert result["rows"][0]["patient_id"].startswith("PID_")
