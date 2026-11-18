from difflib import SequenceMatcher
from functools import wraps
import hmac
import io
import json
import logging
import os
import secrets
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
import pandas as pd

import tools

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)


def _get_configured_password() -> str:
    return os.getenv("APP_PASSWORD", "").strip()


def _is_password_valid() -> bool:
    return len(_get_configured_password()) >= 8


def login_required(fn):
    """8자리 이상 비밀번호 인증을 통과한 세션만 API를 호출할 수 있도록 보호합니다."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not _is_password_valid():
            return jsonify({
                "error": "서버(.env)에 8자리 이상 APP_PASSWORD가 설정되지 않아 앱을 사용할 수 없습니다."
            }), 503
        if not session.get("authenticated"):
            return jsonify({"error": "비밀번호(8자리 이상)를 입력해 잠금을 해제한 뒤 이용해 주세요."}), 401
        return fn(*args, **kwargs)
    return wrapper

EXPECTED_COLUMNS = [
    "조직분류코드", "교과목번호", "교과목명", "분반", "이수구분",
    "학점", "이론", "실습", "개설학과", "대상학년",
    "신청_전", "신청_1", "신청_2", "신청_3", "신청_4",
    "제한_전", "제한_1", "제한_2", "제한_3", "제한_4",
    "시간표", "대표교수직급", "폐강일자", "폐강사유",
]

NUMERIC_COLUMNS = [
    "학점", "이론", "실습",
    "신청_전", "신청_1", "신청_2", "신청_3", "신청_4",
    "제한_전", "제한_1", "제한_2", "제한_3", "제한_4",
]

CATEGORY_COLUMNS = ["이수구분", "대표교수직급", "폐강사유"]

# 디스크에 저장하지 않고 메모리에만 데이터셋 1개를 보관합니다.
DATASET_STORE: dict = {}


def _find_similar_values(df: pd.DataFrame) -> list[dict]:
    """범주형 열에서 띄어쓰기·표기 차이로 같은 뜻처럼 보이는 값 쌍을 찾습니다."""
    candidates = []
    for col in CATEGORY_COLUMNS:
        counts = df[col].dropna().value_counts()
        values = list(counts.index)
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                a, b = str(values[i]), str(values[j])
                a_compact = a.replace(" ", "")
                b_compact = b.replace(" ", "")
                same_without_space = a_compact == b_compact
                ratio = SequenceMatcher(None, a_compact, b_compact).ratio()
                if same_without_space or (len(a_compact) >= 4 and len(b_compact) >= 4 and ratio >= 0.75):
                    candidates.append({
                        "column": col,
                        "value_a": a,
                        "count_a": int(counts[a]),
                        "value_b": b,
                        "count_b": int(counts[b]),
                        "reason": "띄어쓰기 차이" if same_without_space else "유사한 표현",
                    })
    return candidates


def _load_and_convert_xls(file_bytes: bytes, filename: str) -> dict:
    """메모리의 .xls 바이트를 읽어 열 이름을 지정하고 2차 진단까지 수행합니다."""
    raw_df = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str, engine="xlrd")

    if raw_df.shape[1] != len(EXPECTED_COLUMNS):
        raise ValueError(
            f"열 개수가 {len(EXPECTED_COLUMNS)}개여야 하지만 {raw_df.shape[1]}개입니다."
        )
    if raw_df.shape[0] < 3:
        raise ValueError("데이터가 3행부터 시작해야 하지만 행 수가 부족합니다.")

    df = raw_df.iloc[2:].copy().reset_index(drop=True)
    df.columns = EXPECTED_COLUMNS

    # 빈 문자열만 있는 셀은 결측치(NaN)로 통일합니다.
    df = df.replace(r"^\s*$", pd.NA, regex=True)

    numeric_issues = []
    for col in NUMERIC_COLUMNS:
        raw_series = df[col]
        converted = pd.to_numeric(raw_series, errors="coerce")

        # 원래 값이 있었는데 숫자 변환에 실패한 경우 (몰래 고치지 않고 위치만 기록)
        fail_mask = raw_series.notna() & converted.isna()
        if fail_mask.any():
            excel_rows = [int(i) + 3 for i in df.index[fail_mask]]
            numeric_issues.append({
                "column": col,
                "issue": "숫자 변환 실패",
                "count": int(fail_mask.sum()),
                "excel_rows": excel_rows[:20],
            })

        # 음수(-) 값 점검 (몰래 고치지 않고 위치만 기록)
        neg_mask = converted.notna() & (converted < 0)
        if neg_mask.any():
            excel_rows = [int(i) + 3 for i in df.index[neg_mask]]
            numeric_issues.append({
                "column": col,
                "issue": "음수 값",
                "count": int(neg_mask.sum()),
                "excel_rows": excel_rows[:20],
            })

        df[col] = converted

    # 단과대 파생 열 분리 (개설학과를 '>>'로 나눈 첫 부분)
    df["단과대"] = df["개설학과"].fillna("").astype(str).str.split(">>").str[0].str.strip()
    df.loc[df["단과대"] == "", "단과대"] = pd.NA

    # 빈칸 점검 (원본 24개 열 대상)
    missing_by_column = []
    for col in EXPECTED_COLUMNS:
        missing_cnt = int(df[col].isna().sum())
        if missing_cnt > 0:
            if col in ("폐강일자", "폐강사유"):
                note = "정상 개설 강좌는 비어 있음 (해당 없음)"
            else:
                note = "누락 또는 미정 여부 확인 필요"
            missing_by_column.append({
                "column": col,
                "count": missing_cnt,
                "note": note,
            })

    # 분반 / 과목 / 폐강 / 단과대 수 집계
    cancelled_mask = df["폐강일자"].notna()
    active_df = df[~cancelled_mask]
    cancelled_count = int(cancelled_mask.sum())
    active_count = int(len(active_df))
    section_count = int(df[["교과목번호", "분반"]].drop_duplicates().shape[0])
    course_code_count = int(df["교과목번호"].nunique())
    course_name_count = int(df["교과목명"].nunique())
    college_count = int(df["단과대"].nunique())

    # 미폐강 강좌의 정원 0 및 초과 신청 건수
    zero_cap_active = int((active_df["제한_전"].isna() | (active_df["제한_전"] == 0)).sum())
    over_cap_active = int(
        ((active_df["제한_전"] > 0) & (active_df["신청_전"] > active_df["제한_전"])).sum()
    )

    similar_values = _find_similar_values(df)

    # 경고 목록 생성
    warnings = [
        {
            "type": "열 이름 2줄 처리",
            "message": "원본 엑셀의 1~2행(병합된 2단 머리글)을 건너뛰고 3행부터 데이터로 읽었으며, 24개 표준 열 이름을 직접 지정했습니다.",
        },
        {
            "type": "글자 숫자 변환",
            "message": f"글자로 저장된 {len(NUMERIC_COLUMNS)}개 숫자 열(학점·이론·실습·신청_*·제한_*)을 숫자로 변환했습니다.",
        },
    ]

    if numeric_issues or zero_cap_active > 0 or over_cap_active > 0:
        issue_parts = [
            f"{it['column']} {it['issue']} {it['count']}건(엑셀 {it['excel_rows']}행)"
            for it in numeric_issues
        ]
        if zero_cap_active > 0:
            issue_parts.append(f"미폐강 강좌 중 제한_전 0/빈칸 {zero_cap_active}건")
        if over_cap_active > 0:
            issue_parts.append(f"미폐강 강좌 중 신청>제한 {over_cap_active}건")
        warnings.append({
            "type": "비정상 값",
            "message": "비정상 또는 주의가 필요한 숫자 값이 발견되었습니다: " + ", ".join(issue_parts),
        })

    if missing_by_column:
        missing_parts = [f"{m['column']}({m['count']}건)" for m in missing_by_column]
        warnings.append({
            "type": "빈칸",
            "message": "빈칸이 있는 열이 발견되었습니다: " + ", ".join(missing_parts) + ". (폐강일자·폐강사유 빈칸은 정상 개설로 '해당 없음')",
        })

    if similar_values:
        sim_parts = [
            f"{s['column']}: '{s['value_a']}'({s['count_a']}건) vs '{s['value_b']}'({s['count_b']}건)"
            for s in similar_values
        ]
        warnings.append({
            "type": "비슷한 값 후보",
            "message": "같은 뜻인데 표기가 다른 값 후보가 있습니다: " + " / ".join(sim_parts),
        })

    # AI에게 보낼 안전한 진단 요약 (원본 행은 절대 포함하지 않음)
    diagnostic_summary = {
        "filename": filename,
        "row_count": int(len(df)),
        "col_count": len(EXPECTED_COLUMNS),
        "columns": EXPECTED_COLUMNS,
        "derived_columns": ["단과대"],
        "college_names": sorted(df["단과대"].dropna().astype(str).unique().tolist()),
        "category_names": sorted(df["이수구분"].dropna().astype(str).unique().tolist()),
        "section_count": section_count,
        "course_code_count": course_code_count,
        "course_name_count": course_name_count,
        "college_count": college_count,
        "active_count": active_count,
        "cancelled_count": cancelled_count,
        "zero_capacity_active_count": zero_cap_active,
        "over_capacity_active_count": over_cap_active,
        "missing_by_column": missing_by_column,
        "numeric_issues": numeric_issues,
        "similar_values": similar_values,
        "warnings": warnings,
    }

    DATASET_STORE.clear()
    DATASET_STORE["df"] = df
    DATASET_STORE["filename"] = filename
    DATASET_STORE["summary"] = diagnostic_summary
    DATASET_STORE["results"] = {}

    return {
        **diagnostic_summary,
        "sample_rows": df[EXPECTED_COLUMNS].head(3).fillna("").to_dict(orient="records"),
    }


@app.get("/")
def index():
    """메인 웹 화면을 렌더링합니다."""
    return render_template("index.html")


@app.get("/health")
def health():
    """서버 상태와 Gemini API 키 설정 여부(참/거짓만)를 반환합니다."""
    has_api_key = bool(os.getenv("GEMINI_API_KEY", "").strip())
    logger.info("Health check requested (api_key_configured=%s)", has_api_key)
    return jsonify({"status": "ok", "api_key_configured": has_api_key})


@app.get("/auth/status")
def auth_status():
    """현재 로그인 상태와 서버 비밀번호(8자리 이상) 설정 여부를 반환합니다."""
    return jsonify({
        "authenticated": bool(session.get("authenticated")) and _is_password_valid(),
        "password_configured": _is_password_valid(),
    })


@app.post("/login")
def login():
    """8자리 이상 비밀번호를 확인해 세션을 엽니다."""
    if not _is_password_valid():
        return jsonify({
            "error": ".env 파일의 APP_PASSWORD가 8자리 이상으로 설정되지 않았습니다."
        }), 503

    payload = request.get_json(silent=True) or {}
    pw = str(payload.get("password", "")).strip()
    if len(pw) < 8:
        return jsonify({"error": "비밀번호는 8자리 이상 입력해야 합니다."}), 400

    if not hmac.compare_digest(pw, _get_configured_password()):
        logger.warning("Failed login attempt")
        return jsonify({"error": "비밀번호가 올바르지 않습니다."}), 401

    session["authenticated"] = True
    logger.info("User authenticated successfully")
    return jsonify({"authenticated": True})


@app.post("/logout")
def logout():
    """세션을 잠급니다."""
    session.clear()
    return jsonify({"authenticated": False})


@app.post("/inspect")
@login_required
def inspect_file():
    """업로드된 .xls 파일을 메모리에서 읽고 1차 정제 및 숫자 변환 결과를 반환합니다."""
    if "file" not in request.files:
        return jsonify({"error": "업로드된 파일이 없습니다. 파일을 선택해 주세요."}), 400

    uploaded = request.files["file"]
    filename = (uploaded.filename or "").strip()
    if not filename:
        return jsonify({"error": "파일 이름이 비어 있습니다. .xls 파일을 선택해 주세요."}), 400

    if not filename.lower().endswith(".xls"):
        return jsonify({"error": "'.xls' 형식의 엑셀 파일만 업로드할 수 있습니다."}), 400

    file_bytes = uploaded.read()
    if not file_bytes:
        return jsonify({"error": "빈 파일입니다. 내용이 있는 .xls 파일을 올려 주세요."}), 400

    try:
        summary = _load_and_convert_xls(file_bytes, filename)
    except ValueError as exc:
        logger.warning("Upload validation error (%s): %s", filename, exc)
        return jsonify({"error": f"엑셀 파일 구조가 예상과 다릅니다: {exc}"}), 400
    except Exception as exc:
        logger.exception("Failed to read uploaded .xls file: %s", filename)
        return jsonify({"error": f"엑셀 파일을 읽는 중 문제가 발생했습니다: {exc}"}), 500

    logger.info(
        "Inspected file '%s': %d rows, %d cols, %d numeric issue entries",
        filename,
        summary["row_count"],
        summary["col_count"],
        len(summary["numeric_issues"]),
    )
    return jsonify(summary)


GEMINI_MODEL = "gemini-3.5-flash-lite"


def _get_genai_client() -> genai.Client:
    """환경변수 GEMINI_API_KEY를 확인한 뒤 Gemini 클라이언트를 반환합니다."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다. .env 파일을 확인해 주세요.")
    return genai.Client(api_key=api_key)


def _generate_content_with_retry(client: genai.Client, contents: str, config: types.GenerateContentConfig):
    """503/429 등 일시적인 AI 서버 혼잡 시 짧게 대기 후 재시도합니다."""
    last_exc = None
    for attempt in range(3):
        try:
            return client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=config,
            )
        except genai_errors.ServerError as exc:
            last_exc = exc
            if getattr(exc, "code", None) in (500, 502, 503, 504) and attempt < 2:
                logger.warning("Gemini server busy (code=%s), retrying (%d/3)...", getattr(exc, "code", None), attempt + 1)
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last_exc


def _format_ai_error(exc: Exception) -> str:
    """AI 오류를 초보자가 이해할 수 있는 한국어 안내 문장으로 바꿉니다."""
    msg = str(exc)
    if "503" in msg or "UNAVAILABLE" in msg:
        return "현재 Gemini AI 서버에 이용자가 몰려 일시적으로 바쁩니다. 5~10초 후 다시 눌러 주세요."
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        return "AI 요청 한도를 잠시 초과했습니다. 10~20초 후 다시 시도해 주세요."
    return f"AI 서버와 통신 중 문제가 발생했습니다: {msg}"


@app.post("/suggest")
@login_required
def suggest_analyses():
    """진단 요약과 도구 목록만 AI에게 보내 실행 가능한 분석 후보 3~5개를 JSON으로 받습니다."""
    if "summary" not in DATASET_STORE:
        return jsonify({"error": "먼저 .xls 엑셀 파일을 업로드해 주세요."}), 400

    try:
        client = _get_genai_client()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    summary = DATASET_STORE["summary"]
    prompt = (
        "당신은 대학 행정 데이터를 돕는 데이터 분석 컨설턴트입니다.\n"
        "아래 [진단 요약]을 보고, [사용 가능한 도구 목록] 중에서 이 데이터에 가장 유용한 분석 3~5개를 골라 제안하세요.\n"
        "규칙:\n"
        "1) 반드시 [사용 가능한 도구 목록]의 'name'에 있는 도구만 고르세요. 목록에 없는 도구나 임의의 코드는 절대 제안하지 마세요.\n"
        "2) 반드시 아래 JSON 형식으로만 응답하세요.\n"
        '{"suggestions": [{"tool": "도구이름", "reason": "이 분석을 추천하는 쉬운 한국어 이유", "columns": ["사용할 열1", "사용할 열2"], "params": {}}]}\n\n'
        f"[진단 요약]\n{json.dumps(summary, ensure_ascii=False)}\n\n"
        f"[사용 가능한 도구 목록]\n{json.dumps(tools.TOOL_CATALOG, ensure_ascii=False)}"
    )

    try:
        response = _generate_content_with_retry(
            client,
            prompt,
            types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        raw_text = (response.text or "").strip()
        parsed = json.loads(raw_text)
    except Exception as exc:
        logger.exception("Gemini /suggest request failed")
        return jsonify({"error": _format_ai_error(exc)}), 502

    raw_items = parsed.get("suggestions", parsed) if isinstance(parsed, dict) else parsed
    if not isinstance(raw_items, list):
        return jsonify({"error": "AI 응답이 올바른 목록(JSON) 형식이 아닙니다."}), 502

    valid_suggestions = []
    rejected_tools = []
    seen_tools = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool", "")).strip()
        if tool_name not in tools.TOOLS:
            rejected_tools.append(tool_name or "(이름 없음)")
            logger.warning("Rejected unknown tool suggested by AI: %s", tool_name)
            continue
        if tool_name in seen_tools:
            continue
        seen_tools.add(tool_name)
        cols = item.get("columns")
        if not isinstance(cols, list):
            cols = []
        params = item.get("params")
        if not isinstance(params, dict):
            params = {}
        valid_suggestions.append({
            "tool": tool_name,
            "reason": str(item.get("reason", "")).strip() or "데이터 특성을 확인하기 위한 분석입니다.",
            "columns": [str(c) for c in cols],
            "params": params,
        })

    if not valid_suggestions:
        return jsonify({"error": "AI가 유효한 도구 목록 안의 분석을 제안하지 못했습니다. 다시 시도해 주세요."}), 502

    valid_suggestions = valid_suggestions[:5]
    logger.info(
        "Generated %d valid suggestions (rejected=%d)",
        len(valid_suggestions),
        len(rejected_tools),
    )
    return jsonify({
        "suggestions": valid_suggestions,
        "rejected_tools": rejected_tools,
    })


@app.post("/ask")
@login_required
def ask_question():
    """사용자의 자연어 질문을 도구 목록과 파라미터(JSON)로 번역합니다. 계산은 하지 않습니다."""
    if "summary" not in DATASET_STORE:
        return jsonify({"error": "먼저 .xls 엑셀 파일을 업로드해 주세요."}), 400

    payload = request.get_json(silent=True) or {}
    question = str(payload.get("question", "")).strip()
    if not question:
        return jsonify({"error": "분석할 질문을 입력해 주세요."}), 400

    try:
        client = _get_genai_client()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    summary = DATASET_STORE["summary"]
    prompt = (
        "당신은 대학 행정 직원의 질문을 [사용 가능한 도구 목록]과 파라미터로 번역하는 도우미입니다.\n"
        "절대 직접 숫자를 계산하거나 임의의 파이썬 코드를 작성하지 마세요.\n"
        "사용자의 질문을 [진단 요약]의 열과 [사용 가능한 도구 목록]만으로 계산할 수 있는지 판단해 아래 JSON 형식으로만 답하세요.\n\n"
        "규칙:\n"
        "1) 질문에 맞는 기존 도구(group_rate, fill_rate, top_bottom_n, distribution, weekday_building, compare_one_vs_all, quality_report)가 있으면 그것을 고르고, "
        "특정 조건(단과대, 이수구분, 학년 등)으로 거르거나 합계/평균/건수를 묻는 질문이면 custom_query를 고르세요.\n"
        "2) 데이터에 없는 정보(예: 성적, 학생 이름, 교수 이름, 만족도 등)를 물으면 answerable을 false로 두고 reason에 이유를 설명하세요.\n"
        "3) 반드시 JSON만 출력하세요:\n"
        '{"answerable": true, "tool": "도구이름", "reason": "질문을 이 도구와 조건으로 해석한 이유", "columns": ["사용할 열"], "params": {}}\n\n'
        f"[사용자 질문]\n{question}\n\n"
        f"[진단 요약]\n{json.dumps(summary, ensure_ascii=False)}\n\n"
        f"[사용 가능한 도구 목록]\n{json.dumps(tools.TOOL_CATALOG, ensure_ascii=False)}"
    )

    try:
        response = _generate_content_with_retry(
            client,
            prompt,
            types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        parsed = json.loads((response.text or "").strip())
    except Exception as exc:
        logger.exception("Gemini /ask request failed")
        return jsonify({"error": _format_ai_error(exc)}), 502

    if not isinstance(parsed, dict):
        return jsonify({"error": "AI 응답 형식이 올바르지 않습니다."}), 502

    if not parsed.get("answerable", False):
        return jsonify({
            "answerable": False,
            "reason": str(parsed.get("reason", "현재 파일의 열만으로는 계산할 수 없는 질문입니다.")),
        })

    tool_name = str(parsed.get("tool", "")).strip()
    if tool_name not in tools.TOOLS:
        logger.warning("Rejected unknown tool in /ask: %s", tool_name)
        return jsonify({"error": f"AI가 목록에 없는 도구를 선택해 거절되었습니다: {tool_name}"}), 502

    cols = parsed.get("columns") if isinstance(parsed.get("columns"), list) else []
    params = parsed.get("params") if isinstance(parsed.get("params"), dict) else {}
    reason = str(parsed.get("reason", "")).strip() or f"질문('{question}')에 맞춘 분석입니다."

    logger.info("Translated question to tool '%s'", tool_name)
    return jsonify({
        "answerable": True,
        "suggestion": {
            "tool": tool_name,
            "reason": f"[직접 질문] {reason}",
            "columns": [str(c) for c in cols],
            "params": params,
        },
    })


@app.post("/run")
@login_required
def run_tools():
    """사용자가 선택한 도구들을 pandas로 계산하고 결과를 메모리에 보관합니다."""
    if "df" not in DATASET_STORE:
        return jsonify({"error": "먼저 .xls 엑셀 파일을 업로드해 주세요."}), 400

    payload = request.get_json(silent=True) or {}
    raw_selected = payload.get("selected") or payload.get("tools") or []
    if not isinstance(raw_selected, list) or not raw_selected:
        return jsonify({"error": "실행할 분석을 1개 이상 선택해 주세요."}), 400

    df = DATASET_STORE["df"]
    executed_results = []

    for item in raw_selected:
        if isinstance(item, str):
            tool_name = item.strip()
            params = {}
        elif isinstance(item, dict):
            tool_name = str(item.get("tool", "")).strip()
            params = item.get("params") if isinstance(item.get("params"), dict) else {}
        else:
            return jsonify({"error": "요청 형식이 올바르지 않습니다."}), 400

        if tool_name not in tools.TOOLS:
            logger.warning("Rejected unknown tool in /run: %s", tool_name)
            return jsonify({"error": f"허용되지 않은 도구 이름입니다: {tool_name}"}), 400

        try:
            res = tools.TOOLS[tool_name](df, **params)
        except Exception as exc:
            logger.exception("Tool execution failed (%s)", tool_name)
            return jsonify({"error": f"'{tool_name}' 계산 중 오류가 발생했습니다: {exc}"}), 500

        executed_results.append(res)

    DATASET_STORE["results"] = executed_results
    logger.info("Executed %d tool(s): %s", len(executed_results), [r["tool_name"] for r in executed_results])
    return jsonify({"results": executed_results})


@app.post("/explain")
@login_required
def explain_results():
    """계산 결과(또는 비교용 진단 요약)를 바탕으로 AI 해석 보고서를 생성합니다."""
    if "summary" not in DATASET_STORE:
        return jsonify({"error": "먼저 .xls 엑셀 파일을 업로드해 주세요."}), 400

    payload = request.get_json(silent=True) or {}
    mode = str(payload.get("mode", "B")).strip().upper()
    if mode not in ("A", "B"):
        return jsonify({"error": "보고서 모드는 'A' 또는 'B'만 선택할 수 있습니다."}), 400

    try:
        client = _get_genai_client()
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    if mode == "A":
        # 비교용: 계산 결과 없이 진단 요약만 주어 일부러 허술한 보고서와 차이를 비교합니다.
        summary = DATASET_STORE["summary"]
        prompt = (
            "다음 대학 개설강좌 데이터의 진단 요약을 보고, 이 데이터를 분석해서 설명해 줘.\n\n"
            f"[진단 요약]\n{json.dumps(summary, ensure_ascii=False)}"
        )
    else:
        results = DATASET_STORE.get("results") or []
        if not results:
            return jsonify({"error": "먼저 분석 항목을 선택해 계산을 실행해 주세요."}), 400

        prompt = (
            "당신은 대학 행정 보고서를 작성하는 데이터 분석가입니다.\n"
            "아래 [계산 결과 JSON]만 바탕으로 마크다운(.md) 형식의 정식 보고서를 작성하세요.\n\n"
            "필수 지시사항:\n"
            "- 주어진 숫자만 인용하라. 새 숫자를 만들거나 다시 계산하지 마라. 파일만으로 알 수 없는 것은 알 수 없다고 써라.\n"
            "- 표나 본문에 적는 모든 숫자는 반드시 아래 [계산 결과 JSON]에 직접 적혀 있는 숫자만 그대로 사용하세요 (합계, 차이, 새로운 비율 등을 직접 암산해 추가하지 마세요).\n\n"
            "반드시 아래 7개 제목을 순서대로 포함해 작성하세요:\n"
            "## 1. 무엇을 봤는가\n"
            "## 2. 기준(정의)과 사용한 열\n"
            "## 3. 제외된 항목\n"
            "## 4. 계산 결과\n"
            "## 5. 읽어낼 수 있는 것\n"
            "## 6. 이 데이터로는 알 수 없는 것\n"
            "## 7. 다음에 확인해볼 것\n\n"
            f"[계산 결과 JSON]\n{json.dumps(results, ensure_ascii=False)}"
        )

    try:
        response = _generate_content_with_retry(
            client,
            prompt,
            types.GenerateContentConfig(temperature=0.2),
        )
        report_text = (response.text or "").strip()
    except Exception as exc:
        logger.exception("Gemini /explain (mode=%s) failed", mode)
        return jsonify({"error": _format_ai_error(exc)}), 502

    if not report_text:
        return jsonify({"error": "AI가 빈 보고서를 반환했습니다. 다시 시도해 주세요."}), 502

    logger.info("Generated report in mode %s (length=%d chars)", mode, len(report_text))
    return jsonify({"mode": mode, "report": report_text})


if __name__ == "__main__":
    logger.info("Starting Flask server on http://127.0.0.1:5000")
    app.run(debug=True, host="127.0.0.1", port=5000)
