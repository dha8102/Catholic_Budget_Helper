"""사례 DB 저장 폴더 변경·복사, 예전 data 폴더 가져오기, 저장된 사례의 키워드 표시·수정."""
import json

import pytest
from streamlit.testing.v1 import AppTest

import ai
import config
import storage
from conftest import ROOT, FakeAI, goto


def seed(keywords=("자문수당", "자문료")):
    """사례 1건(문서 1개)과 지침 1개를 현재 저장 폴더에 만든다."""
    storage.save_guideline("지침.txt", "제1조 테스트 지침".encode("utf-8"))
    rec = {"business_name": "테스트 사업", "expense_item": "자문수당", "amount": 300000, "review_result": "판단 불가",
           "ai_question": "재가 받았습니까?", "user_answer": "받음", "keywords": list(keywords)}
    return storage.save_cases([rec], [("기안문.txt", "기안문 내용".encode("utf-8"))])["saved_ids"][0]


# ---------------------------------------------------------------- 저장 폴더 변경
def test_change_folder_with_copy_keeps_cases_and_files(data_dir, tmp_path):
    cid = seed()
    new = tmp_path / "새 폴더" / "사례DB"
    msg = storage.change_data_dir(str(new), copy=True)

    assert storage.data_dir() == new and "사례 1건 · 지침 1개 · 문서 1개" in msg
    assert json.loads(config.LOCAL_SETTINGS.read_text(encoding="utf-8")) == {"data_dir": str(new)}  # 이 PC 전체 설정으로 기록
    case = storage.get_cases([cid])[0]
    assert storage.resolve_case_file(case["file_paths"][0]).read_text(encoding="utf-8") == "기안문 내용"  # 상대경로 그대로 유효
    assert storage.get_case_index()[cid]["keywords"] == ["자문수당", "자문료"]
    assert [p.name for p in storage.list_guidelines()] == ["지침.txt"]
    assert storage.folder_summary(data_dir)["cases"] == 1  # 원래 폴더는 그대로 남음


def test_change_folder_without_copy_starts_empty(data_dir, tmp_path):
    seed()
    storage.change_data_dir(str(tmp_path / "빈폴더"), copy=False)
    assert storage.list_cases() == [] and storage.list_guidelines() == []


def test_copy_refuses_to_overwrite_existing_data(data_dir, tmp_path):
    seed()
    other = tmp_path / "other"
    config.DATA_DIR = other
    storage.init_storage()
    seed()  # 대상 폴더에도 이미 사례가 있음
    config.DATA_DIR = data_dir
    with pytest.raises(ValueError, match="덮어쓰지 않았습니다"):
        storage.change_data_dir(str(other), copy=True)
    assert storage.data_dir() == data_dir and not config.LOCAL_SETTINGS.exists()  # 실패하면 바뀌지 않음
    msg = storage.change_data_dir(str(other), copy=False)  # 복사 없이 그 폴더를 그대로 쓰는 것은 가능
    assert "새 폴더에 있던 자료를 사용합니다" in msg


@pytest.mark.parametrize("path, error", [
    ("", "경로를 입력"),
    ("상대경로/폴더", "전체 경로"),
    ("{cur}", "같습니다"),
    ("{cur}/documents/x", "안쪽으로는"),
])
def test_change_folder_rejects_bad_paths(data_dir, path, error):
    with pytest.raises(ValueError, match=error):
        storage.change_data_dir(path.format(cur=data_dir), copy=True)
    assert storage.data_dir() == data_dir


def test_saved_folder_setting_is_used_at_start(tmp_path, monkeypatch):
    target = tmp_path / "설정된폴더"
    monkeypatch.setattr(config, "LOCAL_SETTINGS", tmp_path / "local_settings.json")
    config.save_local_settings({"data_dir": str(target)})
    assert config.load_local_settings()["data_dir"] == str(target)
    assert config.DEFAULT_DATA_DIR.name == "Catholic_Budget_Helper_DB"


# ---------------------------------------------------------------- 예전 data 폴더 가져오기
def test_import_legacy_data(data_dir):
    legacy = config.LEGACY_DATA_DIR
    config.DATA_DIR = legacy
    storage.init_storage()
    cid = seed()
    config.DATA_DIR = data_dir
    assert storage.legacy_data_waiting() == {"cases": 1, "guidelines": 1, "documents": 1}
    storage.import_legacy_data()
    assert [c["id"] for c in storage.list_cases()] == [cid]
    assert storage.legacy_data_waiting() is None  # 가져온 뒤에는 안내하지 않음


# ---------------------------------------------------------------- 화면
def app():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    at.run()
    return at


def test_settings_page_changes_folder(data_dir, tmp_path):
    seed()
    at = goto(app(), "환경 설정")
    assert any("원래 폴더에 있던 자료" in w.value and "복사해서 가져가야 합니다" in w.value for w in at.warning)
    new = tmp_path / "C_DB"
    at.text_input(key="set_data_dir").input(str(new))
    at.button(key="change_data_dir").click().run()
    assert not at.exception
    assert storage.data_dir() == new and len(storage.list_cases()) == 1
    assert any("저장 폴더를" in s.value for s in at.success)


def test_settings_folder_locked_in_web_mode(data_dir, monkeypatch):
    monkeypatch.setattr(config, "WEB_MODE", True)
    at = goto(app(), "환경 설정")
    assert not any(t.key == "set_data_dir" for t in at.text_input)
    assert any("웹 모드에서는 화면에서 저장 폴더를 바꿀 수 없습니다" in i.value for i in at.info)


def test_legacy_notice_and_import_button(data_dir):
    config.DATA_DIR = config.LEGACY_DATA_DIR
    storage.init_storage()
    seed()
    config.DATA_DIR = data_dir
    at = app()
    assert any("이전 폴더" in w.value for w in at.warning)
    at.button(key="import_legacy").click().run()
    assert not at.exception and len(storage.list_cases()) == 1
    assert not any("이전 폴더" in w.value for w in at.warning)


def test_cases_page_shows_and_edits_keywords(data_dir):
    cid = seed()
    at = goto(app(), "저장된 사례")
    assert any("🔑 자문수당, 자문료" in e.label for e in at.expander)  # 목록에서 바로 보임
    assert any(":blue-badge[자문수당]" in m.value for m in at.markdown)
    at.text_input(key=f"kw_{cid}").input("자문수당, 외부 전문가, 사업단장 재가")
    at.button(key=f"save_kw_{cid}").click().run()
    assert not at.exception
    assert storage.get_case_index()[cid]["keywords"] == ["자문수당", "외부전문가", "사업단장재가"]


def test_cases_page_makes_missing_keywords(data_dir, monkeypatch):
    cid = seed(keywords=())
    fake = FakeAI({"case_keywords": {"cases": [{"case_id": cid, "keywords": ["자문수당", "재가"]}]}})
    monkeypatch.setattr(ai, "get_client", lambda **kw: fake)
    at = goto(app(), "저장된 사례")
    assert any("🔑 키워드 없음" in e.label for e in at.expander)
    at.button(key="make_keywords").click().run()
    assert not at.exception
    assert storage.get_case_index()[cid]["keywords"] == ["자문수당", "재가"]
    assert any("🔑 자문수당, 재가" in e.label for e in at.expander)
