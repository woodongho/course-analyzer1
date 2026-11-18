from difflib import SequenceMatcher
import re
import pandas as pd


TOOL_CATALOG = [
    {
        "name": "quality_report",
        "description": "열별 빈칸 개수, 비정상 숫자(변환 실패·음수), 비슷한 값 후보를 점검합니다.",
        "default_columns": ["시간표", "대표교수직급", "폐강일자", "폐강사유", "신청_전", "신청_4"],
    },
    {
        "name": "group_rate",
        "description": "단과대, 이수구분, 대상학년 등 그룹별 전체 행 수, 폐강 수, 폐강률(%)을 계산합니다.",
        "default_columns": ["단과대", "이수구분", "대상학년", "폐강일자"],
    },
    {
        "name": "top_bottom_n",
        "description": "수강신청인원(신청_전) 또는 제한인원(제한_전) 기준으로 상위 N개와 하위 N개 강좌를 뽑습니다.",
        "default_columns": ["교과목번호", "교과목명", "분반", "단과대", "신청_전", "제한_전"],
    },
    {
        "name": "fill_rate",
        "description": "폐강을 제외하고 수강신청인원 ÷ 수강제한인원 충원율(%)을 단과대·이수구분별로 계산합니다.",
        "default_columns": ["단과대", "신청_전", "제한_전", "폐강일자"],
    },
    {
        "name": "distribution",
        "description": "분반당 수강신청인원(신청_전)이 어느 인원 구간(0명, 1~10명 등)에 몰려 있는지 분포를 계산합니다.",
        "default_columns": ["신청_전", "폐강일자"],
    },
    {
        "name": "weekday_building",
        "description": "시간표 문자열을 쪼개어 요일별 및 건물별 수업 건수를 집계합니다.",
        "default_columns": ["시간표"],
    },
    {
        "name": "compare_one_vs_all",
        "description": "선택한 특정 그룹(예: 특정 단과대 또는 이수구분)의 평균·중앙값을 전체와 비교합니다.",
        "default_columns": ["단과대", "이수구분", "신청_전", "제한_전", "폐강일자"],
    },
    {
        "name": "custom_query",
        "description": "사용자 질문에 맞춰 조건(filters)으로 행을 거른 뒤, 그룹(group_by)별로 숫자 열(metric)의 건수·합계·평균·중앙값·최소·최대(agg)를 계산합니다.",
        "params_schema": {
            "filters": "[{'column': '단과대|이수구분|대상학년|대표교수직급|교과목명|학점|신청_전|제한_전', 'op': '==|!=|contains|>|>=|<|<=', 'value': '값'}]",
            "group_by": "단과대|이수구분|대상학년|대표교수직급|학점|교과목명|폐강사유|null",
            "metric": "신청_전|신청_1|신청_2|신청_3|신청_4|제한_전|학점|이론|실습",
            "agg": "count|sum|mean|median|min|max",
            "exclude_cancelled": "true|false",
            "limit": "1~30 (기본 20)",
            "sort_desc": "true|false",
        },
        "default_columns": ["단과대", "이수구분", "대상학년", "신청_전", "제한_전"],
    },
]


def _find_similar_candidates(df: pd.DataFrame) -> list[dict]:
    """범주형 열에서 띄어쓰기나 표현 차이로 유사해 보이는 값 쌍을 찾습니다."""
    candidates = []
    for col in ["이수구분", "대표교수직급", "폐강사유"]:
        if col not in df.columns:
            continue
        counts = df[col].dropna().value_counts()
        vals = list(counts.index)
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                a, b = str(vals[i]), str(vals[j])
                ac, bc = a.replace(" ", ""), b.replace(" ", "")
                same_no_space = ac == bc
                ratio = SequenceMatcher(None, ac, bc).ratio()
                if same_no_space or (len(ac) >= 4 and len(bc) >= 4 and ratio >= 0.75):
                    candidates.append({
                        "열": col,
                        "값_A": f"{a} ({int(counts[a])}건)",
                        "값_B": f"{b} ({int(counts[b])}건)",
                        "유형": "띄어쓰기 차이" if same_no_space else "유사한 표현",
                    })
    return candidates


def quality_report(df: pd.DataFrame, **kwargs) -> dict:
    """1. 빈칸 개수(열별), 비정상 숫자, 비슷한 값 후보를 점검합니다."""
    total_rows = int(len(df))
    records = []

    for col in df.columns:
        if col == "단과대":
            continue
        na_cnt = int(df[col].isna().sum())
        if na_cnt > 0:
            records.append({
                "점검항목": "빈칸",
                "대상열": col,
                "건수": na_cnt,
                "비고": "정상 개설(해당 없음)" if col in ("폐강일자", "폐강사유") else "누락·미정 확인 필요",
            })

    numeric_cols = [
        "학점", "이론", "실습",
        "신청_전", "신청_1", "신청_2", "신청_3", "신청_4",
        "제한_전", "제한_1", "제한_2", "제한_3", "제한_4",
    ]
    for col in numeric_cols:
        if col not in df.columns:
            continue
        neg_mask = df[col].notna() & (df[col] < 0)
        if neg_mask.any():
            excel_rows = [int(i) + 3 for i in df.index[neg_mask]]
            records.append({
                "점검항목": "음수 값",
                "대상열": col,
                "건수": int(neg_mask.sum()),
                "비고": f"엑셀 행 위치: {excel_rows[:10]}",
            })

    for sim in _find_similar_candidates(df):
        records.append({
            "점검항목": f"비슷한 값({sim['유형']})",
            "대상열": sim["열"],
            "건수": 2,
            "비고": f"{sim['값_A']} vs {sim['값_B']}",
        })

    return {
        "tool_name": "quality_report",
        "title": "데이터 품질 점검표 (빈칸 · 비정상 숫자 · 비슷한 값)",
        "definition": "전체 행을 대상으로 열별 빈칸, 숫자 열의 음수(0 미만), 범주형 열의 유사 표기를 점검했습니다.",
        "used_rows": total_rows,
        "excluded_rows": 0,
        "excluded_reason": "전체 행을 빠짐없이 점검하여 제외한 행이 없습니다.",
        "used_columns": [c for c in df.columns if c != "단과대"],
        "unit": "건",
        "cautions": "폐강일자·폐강사유의 빈칸은 정상 개설 강좌(해당 없음)이며, 비정상 값은 수정하지 않고 위치만 표시했습니다.",
        "records": records,
    }


def group_rate(
    df: pd.DataFrame,
    group_by: str = "단과대",
    filter_college: str | None = None,
    **kwargs,
) -> dict:
    """2. 그룹(단과대/이수구분/대상학년)별 행 수·폐강 수·폐강률(분반 행 단위)을 계산합니다."""
    allowed = ["단과대", "이수구분", "대상학년"]
    if group_by not in allowed:
        group_by = "단과대"

    total_rows = int(len(df))
    work = df.copy()
    reasons = []

    if filter_college and "단과대" in work.columns:
        target_col = str(filter_college).strip()
        col_mask = work["단과대"].fillna("").astype(str).str.strip() == target_col
        diff = int((~col_mask).sum())
        if diff > 0:
            reasons.append(f"단과대 != '{target_col}' {diff}건")
        work = work[col_mask]

    valid_mask = work[group_by].notna()
    na_cnt = int((~valid_mask).sum())
    if na_cnt > 0:
        reasons.append(f"{group_by} 빈칸 {na_cnt}건")
    used_df = work[valid_mask].copy()
    excluded_rows = total_rows - int(len(used_df))

    used_df["_is_cancelled"] = used_df["폐강일자"].notna().astype(int)
    grouped = (
        used_df.groupby(group_by, dropna=False)
        .agg(전체행수=("_is_cancelled", "count"), 폐강수=("_is_cancelled", "sum"))
        .reset_index()
    )
    grouped["폐강률(%)"] = (grouped["폐강수"] / grouped["전체행수"] * 100).round(2)
    grouped = grouped.sort_values(by=["폐강률(%)", "전체행수"], ascending=[False, False])

    records = grouped.to_dict(orient="records")
    title_prefix = f"{filter_college} - " if filter_college else ""
    return {
        "tool_name": "group_rate",
        "title": f"{title_prefix}{group_by}별 개설 현황 및 폐강률",
        "definition": f"한 행(학과별 개설 분반)을 1건으로 세어 {group_by}별 전체 행 수, 폐강일자 기재 행 수(폐강 수), 폐강률(폐강 수 ÷ 전체 행 수 × 100)을 계산했습니다.",
        "used_rows": int(len(used_df)),
        "excluded_rows": excluded_rows,
        "excluded_reason": ", ".join(reasons) + " 제외" if reasons else "제외한 행 없음",
        "used_columns": sorted({group_by, "폐강일자", "단과대"} if filter_college else {group_by, "폐강일자"}),
        "unit": "건, %",
        "cautions": "여러 학과에 공동 개설된 분반은 학과별 행으로 각각 집계됩니다. 표기 차이가 있는 값은 합치지 않고 원본 기준으로 집계했습니다.",
        "records": records,
        "chart": {
            "label": "폐강률(%)",
            "labels": [str(r[group_by]) for r in records[:15]],
            "values": [float(r["폐강률(%)"]) for r in records[:15]],
        },
    }


def top_bottom_n(
    df: pd.DataFrame,
    metric: str = "신청_전",
    n: int = 5,
    exclude_cancelled: bool = True,
    **kwargs,
) -> dict:
    """3. 지정한 숫자 열 기준으로 상위 N개와 하위 N개 강좌를 뽑습니다."""
    if metric not in ("신청_전", "제한_전", "학점"):
        metric = "신청_전"
    n = max(1, min(int(n), 20))

    total_rows = int(len(df))
    work = df.copy()
    reasons = []

    if exclude_cancelled:
        not_cancelled = work["폐강일자"].isna()
        can_cnt = int((~not_cancelled).sum())
        if can_cnt > 0:
            reasons.append(f"폐강 강좌 {can_cnt}건")
        work = work[not_cancelled]

    valid_num = work[metric].notna() & (work[metric] >= 0)
    bad_cnt = int((~valid_num).sum())
    if bad_cnt > 0:
        reasons.append(f"{metric} 빈칸·음수 {bad_cnt}건")
    work = work[valid_num]

    used_rows = int(len(work))
    excluded_rows = total_rows - used_rows

    cols = ["교과목번호", "교과목명", "분반", "단과대", "이수구분", "신청_전", "제한_전"]
    top_df = work.sort_values(by=metric, ascending=False).head(n)[cols].copy()
    top_df.insert(0, "구분", f"상위 {n}")

    bot_df = work.sort_values(by=metric, ascending=True).head(n)[cols].copy()
    bot_df.insert(0, "구분", f"하위 {n}")

    combined = pd.concat([top_df, bot_df], ignore_index=True).fillna("")
    records = combined.to_dict(orient="records")

    return {
        "tool_name": "top_bottom_n",
        "title": f"{metric} 기준 상위 {n}개 · 하위 {n}개 강좌",
        "definition": f"{metric} 값이 큰 순서(상위 {n}개)와 작은 순서(하위 {n}개)로 강좌 행을 정렬했습니다.",
        "used_rows": used_rows,
        "excluded_rows": excluded_rows,
        "excluded_reason": ", ".join(reasons) + " 제외" if reasons else "제외한 행 없음",
        "used_columns": ["교과목번호", "교과목명", "분반", "단과대", "이수구분", "신청_전", "제한_전", "폐강일자"],
        "unit": "명" if metric != "학점" else "학점",
        "cautions": "동점 강좌가 여러 개일 경우 파일에 나온 순서대로 상위/하위 N개만 표시됩니다.",
        "records": records,
    }


def fill_rate(df: pd.DataFrame, group_by: str = "단과대", **kwargs) -> dict:
    """4. 충원율(신청_전 ÷ 제한_전)을 계산합니다. 폐강 및 제한_전 0·빈칸은 제외합니다."""
    if group_by not in ("단과대", "이수구분", "대상학년"):
        group_by = "단과대"

    total_rows = int(len(df))
    not_cancelled = df["폐강일자"].isna()
    cancelled_cnt = int((~not_cancelled).sum())
    active = df[not_cancelled].copy()

    valid_cap = active["제한_전"].notna() & (active["제한_전"] > 0)
    zero_cap_cnt = int((~valid_cap).sum())
    valid_enr = active["신청_전"].notna() & (active["신청_전"] >= 0)
    bad_enr_cnt = int((valid_cap & ~valid_enr).sum())

    work = active[valid_cap & valid_enr].copy()
    work["충원율(%)"] = (work["신청_전"] / work["제한_전"]) * 100
    over_cap_cnt = int((work["신청_전"] > work["제한_전"]).sum())

    used_rows = int(len(work))
    excluded_rows = total_rows - used_rows
    reasons = []
    if cancelled_cnt > 0:
        reasons.append(f"폐강 강좌 {cancelled_cnt}건")
    if zero_cap_cnt > 0:
        reasons.append(f"제한_전 0 또는 빈칸 {zero_cap_cnt}건")
    if bad_enr_cnt > 0:
        reasons.append(f"신청_전 빈칸·음수 {bad_enr_cnt}건")

    grouped = (
        work.groupby(group_by, dropna=False)
        .agg(
            강좌수=("충원율(%)", "count"),
            평균충원율=("충원율(%)", "mean"),
            중앙값충원율=("충원율(%)", "median"),
            신청합계=("신청_전", "sum"),
            제한합계=("제한_전", "sum"),
        )
        .reset_index()
    )
    grouped["평균충원율(%)"] = grouped["평균충원율"].round(2)
    grouped["중앙값충원율(%)"] = grouped["중앙값충원율"].round(2)
    grouped["전체합산충원율(%)"] = (grouped["신청합계"] / grouped["제한합계"] * 100).round(2)
    grouped = grouped.drop(columns=["평균충원율", "중앙값충원율"]).sort_values(
        by="평균충원율(%)", ascending=False
    )

    records = grouped.to_dict(orient="records")
    return {
        "tool_name": "fill_rate",
        "title": f"{group_by}별 수강 충원율 (신청_전 ÷ 제한_전)",
        "definition": f"폐강되지 않고 제한_전 > 0인 강좌를 대상으로 강좌별 충원율(신청_전 ÷ 제한_전 × 100)을 구한 뒤 {group_by}별 평균·중앙값·합산 충원율을 계산했습니다.",
        "used_rows": used_rows,
        "excluded_rows": excluded_rows,
        "excluded_reason": ", ".join(reasons) + " 제외" if reasons else "제외한 행 없음",
        "used_columns": [group_by, "신청_전", "제한_전", "폐강일자"],
        "unit": "건, 명, %",
        "cautions": f"신청 인원이 제한 인원보다 많은 강좌({over_cap_cnt}건)는 100% 초과 그대로 계산에 포함했습니다.",
        "records": records,
        "chart": {
            "label": "평균충원율(%)",
            "labels": [str(r[group_by]) for r in records[:15]],
            "values": [float(r["평균충원율(%)"]) for r in records[:15]],
        },
    }


def distribution(df: pd.DataFrame, exclude_cancelled: bool = True, **kwargs) -> dict:
    """5. 분반당 신청 인원(신청_전) 구간별 분포를 계산합니다."""
    total_rows = int(len(df))
    work = df.copy()
    reasons = []

    if exclude_cancelled:
        not_cancelled = work["폐강일자"].isna()
        can_cnt = int((~not_cancelled).sum())
        if can_cnt > 0:
            reasons.append(f"폐강 강좌 {can_cnt}건")
        work = work[not_cancelled]

    valid_num = work["신청_전"].notna() & (work["신청_전"] >= 0)
    bad_cnt = int((~valid_num).sum())
    if bad_cnt > 0:
        reasons.append(f"신청_전 빈칸·음수 {bad_cnt}건")
    work = work[valid_num].copy()

    bins = [-1, 0, 10, 20, 30, 50, 100, float("inf")]
    labels = ["0명", "1~10명", "11~20명", "21~30명", "31~50명", "51~100명", "101명 이상"]
    work["인원구간"] = pd.cut(work["신청_전"], bins=bins, labels=labels)

    counts = work["인원구간"].value_counts().reindex(labels, fill_value=0)
    used_rows = int(len(work))
    excluded_rows = total_rows - used_rows

    records = [
        {
            "인원구간": lbl,
            "분반수": int(cnt),
            "비율(%)": round(int(cnt) / used_rows * 100, 2) if used_rows > 0 else 0.0,
        }
        for lbl, cnt in counts.items()
    ]

    return {
        "tool_name": "distribution",
        "title": "분반당 수강신청인원(신청_전) 구간별 분포",
        "definition": "분반(행)의 신청_전 인원을 7개 구간(0명, 1~10명, 11~20명, 21~30명, 31~50명, 51~100명, 101명 이상)으로 나누어 빈도를 셌습니다.",
        "used_rows": used_rows,
        "excluded_rows": excluded_rows,
        "excluded_reason": ", ".join(reasons) + " 제외" if reasons else "제외한 행 없음",
        "used_columns": ["신청_전", "폐강일자"],
        "unit": "건, %",
        "cautions": "학과별 공동개설 행도 각각 1건의 분반 행으로 집계했습니다.",
        "records": records,
        "chart": {
            "label": "분반 수",
            "labels": [r["인원구간"] for r in records],
            "values": [r["분반수"] for r in records],
        },
    }


def weekday_building(df: pd.DataFrame, **kwargs) -> dict:
    """6. 시간표를 쪼개어 요일별·건물별 분반 수를 계산합니다."""
    total_rows = int(len(df))
    has_tt = df["시간표"].notna() & (df["시간표"].astype(str).str.strip() != "")
    excluded_rows = int((~has_tt).sum())
    work = df[has_tt].copy()
    used_rows = int(len(work))

    day_order = ["월", "화", "수", "목", "금", "토", "일"]
    day_counts = {d: 0 for d in day_order}
    building_counts: dict[str, int] = {}

    for tt in work["시간표"].astype(str):
        days = set(re.findall(r"[월화수목금토일]", tt))
        for d in days:
            day_counts[d] += 1

        # 예: '누리606(월1,2,3)', '한울B109-1(...)' -> 건물명 '누리', '한울' 추출
        buildings = set(re.findall(r"([가-힣]+)[A-Za-z0-9-]*\(", tt))
        for b in buildings:
            building_counts[b] = building_counts.get(b, 0) + 1

    records = []
    for d in day_order:
        if day_counts[d] > 0:
            records.append({"구분": "요일", "항목": f"{d}요일", "분반수": day_counts[d]})
    for b, cnt in sorted(building_counts.items(), key=lambda x: x[1], reverse=True):
        records.append({"구분": "건물", "항목": b, "분반수": cnt})

    return {
        "tool_name": "weekday_building",
        "title": "요일별 · 건물별 수업 분포 (시간표 분해)",
        "definition": "시간표 문자열에서 요일([월~일])과 괄호 앞 한글 건물명을 추출해 각각 중복 없이 강좌당 1회씩 집계했습니다.",
        "used_rows": used_rows,
        "excluded_rows": excluded_rows,
        "excluded_reason": f"시간표가 비어 있는 행 {excluded_rows}건 제외" if excluded_rows > 0 else "제외한 행 없음",
        "used_columns": ["시간표"],
        "unit": "건",
        "cautions": "한 강좌가 여러 요일이나 여러 건물에서 수업할 경우 각 요일·건물에 모두 1건씩 합산되므로 합계가 사용 행 수(2,134건)보다 클 수 있습니다.",
        "records": records,
        "chart": {
            "label": "요일별 수업 수",
            "labels": [f"{d}요일" for d in day_order if day_counts[d] > 0],
            "values": [day_counts[d] for d in day_order if day_counts[d] > 0],
        },
    }


def compare_one_vs_all(
    df: pd.DataFrame,
    group_by: str = "단과대",
    target: str | None = None,
    **kwargs,
) -> dict:
    """7. 선택한 대상(예: 특정 단과대)과 전체의 평균·중앙값을 비교합니다."""
    if group_by not in ("단과대", "이수구분", "대상학년"):
        group_by = "단과대"

    total_rows = int(len(df))
    not_cancelled = df["폐강일자"].isna()
    can_cnt = int((~not_cancelled).sum())
    valid_enr = df["신청_전"].notna() & (df["신청_전"] >= 0)
    bad_cnt = int((not_cancelled & ~valid_enr).sum())

    work = df[not_cancelled & valid_enr].copy()
    used_rows = int(len(work))
    excluded_rows = total_rows - used_rows

    reasons = []
    if can_cnt > 0:
        reasons.append(f"폐강 강좌 {can_cnt}건")
    if bad_cnt > 0:
        reasons.append(f"신청_전 빈칸·음수 {bad_cnt}건")

    available_targets = work[group_by].dropna().value_counts()
    if not target or target not in available_targets.index:
        target = str(available_targets.index[0])

    target_df = work[work[group_by].astype(str) == str(target)]

    def _stats(sub: pd.DataFrame, label: str) -> dict:
        cap_ok = sub[sub["제한_전"].notna() & (sub["제한_전"] > 0)]
        avg_fill = round(float((cap_ok["신청_전"] / cap_ok["제한_전"] * 100).mean()), 2) if len(cap_ok) > 0 else None
        return {
            "비교대상": label,
            "강좌수": int(len(sub)),
            "신청_전_평균": round(float(sub["신청_전"].mean()), 2) if len(sub) > 0 else 0.0,
            "신청_전_중앙값": round(float(sub["신청_전"].median()), 2) if len(sub) > 0 else 0.0,
            "평균충원율(%)": avg_fill if avg_fill is not None else "계산불가",
        }

    records = [
        _stats(target_df, f"{group_by}: {target}"),
        _stats(work, "전체 (미폐강)"),
    ]

    return {
        "tool_name": "compare_one_vs_all",
        "title": f"선택 대상({target}) vs 전체 비교",
        "definition": f"미폐강 강좌를 대상으로 '{group_by}={target}' 집단과 전체 집단의 신청_전 평균·중앙값 및 평균 충원율(제한_전 > 0 기준)을 비교했습니다.",
        "used_rows": used_rows,
        "excluded_rows": excluded_rows,
        "excluded_reason": ", ".join(reasons) + " 제외" if reasons else "제외한 행 없음",
        "used_columns": [group_by, "신청_전", "제한_전", "폐강일자"],
        "unit": "건, 명, %",
        "cautions": "평균은 극단적으로 큰 대형 강좌의 영향을 받으므로 중앙값을 함께 확인하는 것이 좋습니다.",
        "records": records,
        "chart": {
            "label": "신청_전 평균(명)",
            "labels": [r["비교대상"] for r in records],
            "values": [r["신청_전_평균"] for r in records],
        },
    }


def _to_bool(val, default: bool = True) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "no", "n", "")
    return bool(val) if val is not None else default


def custom_query(
    df: pd.DataFrame,
    filters: list[dict] | None = None,
    group_by: str | None = None,
    metric: str = "신청_전",
    agg: str = "mean",
    exclude_cancelled: bool = True,
    limit: int = 20,
    sort_desc: bool = True,
    **kwargs,
) -> dict:
    """8. 사용자 질문 조건(filters)과 그룹(group_by)·집계(agg)를 실행합니다."""
    allowed_metrics = [
        "신청_전", "신청_1", "신청_2", "신청_3", "신청_4",
        "제한_전", "제한_1", "제한_2", "제한_3", "제한_4",
        "학점", "이론", "실습",
    ]
    allowed_groups = [
        "단과대", "이수구분", "대상학년", "대표교수직급", "학점", "교과목명", "폐강사유",
    ]
    allowed_aggs = {
        "count": "건수",
        "sum": "합계",
        "mean": "평균",
        "median": "중앙값",
        "min": "최솟값",
        "max": "최댓값",
    }

    if metric not in allowed_metrics:
        metric = "신청_전"
    if group_by not in allowed_groups:
        group_by = None
    if agg not in allowed_aggs:
        agg = "count"
    limit = max(1, min(int(limit or 20), 30))
    exclude_cancelled = _to_bool(exclude_cancelled, True)
    sort_desc = _to_bool(sort_desc, True)

    total_rows = int(len(df))
    work = df.copy()
    reasons = []
    used_cols = {metric}

    if exclude_cancelled:
        not_can = work["폐강일자"].isna()
        can_cnt = int((~not_can).sum())
        if can_cnt > 0:
            reasons.append(f"폐강 강좌 {can_cnt}건")
        work = work[not_can]
        used_cols.add("폐강일자")

    filter_desc = []
    if isinstance(filters, list):
        for f in filters:
            if not isinstance(f, dict):
                continue
            col = str(f.get("column", "")).strip()
            op = str(f.get("op", "==")).strip()
            val = f.get("value")
            if col not in work.columns or val is None:
                continue
            used_cols.add(col)
            before_cnt = len(work)
            if op in (">", ">=", "<", "<="):
                num_val = pd.to_numeric(val, errors="coerce")
                col_num = pd.to_numeric(work[col], errors="coerce")
                if pd.isna(num_val):
                    continue
                if op == ">":
                    work = work[col_num > num_val]
                elif op == ">=":
                    work = work[col_num >= num_val]
                elif op == "<":
                    work = work[col_num < num_val]
                elif op == "<=":
                    work = work[col_num <= num_val]
            elif op == "contains":
                work = work[work[col].fillna("").astype(str).str.contains(str(val), case=False, regex=False)]
            elif op == "!=":
                work = work[work[col].fillna("").astype(str).str.strip() != str(val).strip()]
            else:
                op = "=="
                work = work[work[col].fillna("").astype(str).str.strip() == str(val).strip()]

            diff = before_cnt - len(work)
            filter_desc.append(f"{col} {op} '{val}'")
            if diff > 0:
                reasons.append(f"조건({col} {op} '{val}') 미해당 {diff}건")

    if agg != "count":
        valid_num = work[metric].notna() & (work[metric] >= 0)
        bad_cnt = int((~valid_num).sum())
        if bad_cnt > 0:
            reasons.append(f"{metric} 빈칸·음수 {bad_cnt}건")
        work = work[valid_num]

    used_rows = int(len(work))
    excluded_rows = total_rows - used_rows
    agg_label = allowed_aggs[agg]
    val_col_name = f"{metric}_{agg_label}" if agg != "count" else "강좌(행)수"

    records = []
    chart = None
    if group_by:
        used_cols.add(group_by)
        if used_rows > 0:
            grouped = work.groupby(group_by, dropna=False)[metric].agg(agg).reset_index()
            grouped.columns = [group_by, val_col_name]
            if agg in ("mean", "median"):
                grouped[val_col_name] = grouped[val_col_name].round(2)
            grouped[group_by] = grouped[group_by].fillna("(빈칸)")
            grouped = grouped.sort_values(by=val_col_name, ascending=not sort_desc).head(limit)
            records = grouped.to_dict(orient="records")
            chart = {
                "label": val_col_name,
                "labels": [str(r[group_by]) for r in records[:15]],
                "values": [float(r[val_col_name]) for r in records[:15]],
            }
    else:
        val = int(len(work)) if agg == "count" else (
            round(float(work[metric].agg(agg)), 2) if used_rows > 0 else 0
        )
        records = [{"항목": ", ".join(filter_desc) or "전체", val_col_name: val, "강좌수": used_rows}]

    cond_text = ", ".join(filter_desc) if filter_desc else "조건 없음"
    group_text = f"{group_by}별 " if group_by else ""
    return {
        "tool_name": "custom_query",
        "title": f"질문 맞춤 집계: {group_text}{val_col_name} ({cond_text})",
        "definition": f"조건({cond_text})에 맞는 행을 추출한 뒤, {group_text}{metric}의 {agg_label}을 계산했습니다.",
        "used_rows": used_rows,
        "excluded_rows": excluded_rows,
        "excluded_reason": ", ".join(reasons) + " 제외" if reasons else "제외한 행 없음",
        "used_columns": sorted(used_cols),
        "unit": "건" if agg == "count" else ("학점" if metric == "학점" else "명"),
        "cautions": "학과별 공동개설 분반은 각각 1행으로 집계됩니다. 최대 상위 30개 그룹까지 표시합니다.",
        "records": records,
        "chart": chart,
    }


TOOLS = {
    "quality_report": quality_report,
    "group_rate": group_rate,
    "top_bottom_n": top_bottom_n,
    "fill_rate": fill_rate,
    "distribution": distribution,
    "weekday_building": weekday_building,
    "compare_one_vs_all": compare_one_vs_all,
    "custom_query": custom_query,
}
