# spec-viewer 설계

## 목표

핵심은 **스펙 히스토리 뷰어**다. 특정 버전을 열면 각 부분이 언제(어느 버전), 왜(어느 CR) 바뀌었는지 바로 보여야 한다.
다른 기능(다운로드, 탭, 검색, 의도 요약)은 이 화면을 방해하지 않는 위치(사이드 패널, 별도 탭, 오버레이)에 둔다.

## 문서 포맷

| 후보 | 판단 |
|---|---|
| HWP | 무겁고 파싱 도구 부족 |
| PDF | 텍스트 흐름·표 구조가 깨져 단어 단위 diff/하이라이트가 사실상 불가 |
| Markdown | 표(병합 셀)·수식 표현력이 부족 |
| **HTML 조각 + LaTeX 수식 (채택)** | 원본 docx 를 pandoc 으로 변환. 표·스타일 보존, KaTeX 렌더링, diff/색칠/링크 자유로움 |

원본은 3GPP 가 배포하는 `.docx` 를 그대로 쓰고, 앱 내부 표현만 HTML 로 바꾼다.

### 수식 리스크

- Word 수식(OMML)은 pandoc 이 LaTeX 로 변환 → KaTeX 렌더링 + 텍스트처럼 diff 가능.
- 38.211/38.212 의 구형 수식은 Equation Editor/MathType **OLE 객체(WMF 미리보기 이미지)** 인 경우가 많다.
  현재는 LibreOffice 로 PNG 변환 후 표시하고, 이미지 해시가 다르면 "변경"으로 잡힌다(수식 내부 diff 는 불가).
  실제 문서로 검증 필요. 개선안: MathType OLE 의 MTEF 데이터를 MathML 로 변환.

## 데이터 모델 (SQLite, `~/.specview/library.sqlite`)

```
spec(number, title)
version(id, spec, version, release, sort_key, source)
clause_ver(version_id, ord, key, number, title, level, status, introduced, last_changed, history, deleted)
block_ver(version_id, clause_key, ord, kind, style, raw_hash, annot_hash)
blob(hash, html, text)            -- 원본 블록 내용, 해시로 중복 제거
annot(hash, html, introduced, changed, history)  -- 이력 주석이 붙은 블록, 역시 중복 제거
asset(hash, mime, data)           -- 이미지(수식 그림 포함)
cr(spec, new_version, date, meeting, tdoc, cr, rev, cat, subject)
blob_fts                          -- FTS5 전문 검색 인덱스
```

- **단위**: 절(clause) → 블록(문단/표/수식/리스트). 블록이 이력 추적·diff·검색의 기본 단위.
- **중복 제거**: 버전 간 대부분의 블록은 동일 → 내용 해시로 1회만 저장. 수십 개 버전도 1~2개 분량 수준.
- **절 식별**: 절 번호가 key. 번호가 바뀐 경우 같은 제목의 사라진 절과 매칭(재번호 대응).

## 이력(blame) 엔진 — `backend/specview/history.py`

1. 버전을 오름차순으로 순회.
2. 절 key 로 이전 버전과 매칭, 절 안에서 블록 시퀀스를 정렬:
   해시가 같으면 동일, 아니면 텍스트 유사도로 "수정" 쌍을 찾고 나머지는 추가/삭제.
3. 수정 블록은 HTML 인지 토큰(태그·단어·공백·수식 원자 토큰) 단위로 diff 하여
   **토큰마다 처음 등장한 버전**을 이어받는다 → 단어 단위로 "16.1.0 에서 추가" 표시.
4. 결과 HTML 은 `<span class="vc" data-v="16.1.0">` 으로 감싸며 태그 경계를 넘지 않음(항상 유효한 HTML).

색상은 UI 가 결정: `기준 버전 < v ≤ 현재 버전` 인 것만 칠함 → "R17 이후 바뀐 것만" 같은 필터가 즉시 동작.

## CR 연결 (LLM 불필요 부분)

| 단계 | 소스 | 상태 |
|---|---|---|
| 버전 → CR 목록 | 스펙 끝 Change history 표 (Date/Meeting/TDoc/CR/Rev/Cat/Subject/New version) | ✅ |
| CR → 절 | TSG Tdoc zip(`.../TSG_RAN/TSGR_87e/Docs/RP-200186.zip`) 안의 CR docx 표지 "Clauses affected" | ✅ (`crdocs.py`) |
| CR → 이유 | 표지 "Reason for change" / "Summary of change" / WI code | ✅ |
| 건너뛴 버전 | 라이브러리에 없는 버전의 CR 은 그 다음 받은 버전에 귀속 (`lib_version`) | ✅ |
| CR → 38.822 | WI code ↔ 38.822 섹션 제목(레이어별·릴리즈별 매칭), 본문 약어(PRACH…)로 정렬 | ✅ (`features.py`) |
| 절 내 여러 CR 중복 시 구분 | CR 본문(수정 표시) ↔ diff 매칭, 애매하면 LLM | annotation 서버 |

검증: 38.211 Rel-16 11개 버전에서 CR 이 "Clauses affected"로 적은 절의 98%가 실제 diff 에서 변경으로 잡혔고,
diff 로 잡힌 변경 절의 96%가 CR 로 설명됨.

## 로컬 API (`specview serve`, 127.0.0.1)

```
GET  /api/specs
GET  /api/specs/{spec}/versions
GET  /api/specs/{spec}/{version}/toc
GET  /api/specs/{spec}/{version}/clauses?start=&count=     # 스트리밍 스크롤용 페이지
GET  /api/specs/{spec}/{version}/search?q=                  # FTS5
GET  /api/specs/{spec}/compare?old=&new=
GET  /api/specs/{spec}/crs[?version=]
GET  /api/asset/{hash}
GET  /api/remote/{spec}                                     # 3GPP archive 버전 목록
POST /api/fetch   {specs, releases?, latest_per_release?}   # 백그라운드 다운로드+변환
GET  /api/jobs
```

## 큰 문서 처리 (스트리밍 + 검색)

- 문서 전체를 DOM 에 올리지 않고 **가상 스크롤**: 화면 근처 절만 25개 단위 페이지로 로드.
- 가상 스크롤에서는 브라우저 기본 Ctrl+F 가 미로드 영역을 못 찾으므로 **FTS5 인덱스로 자체 검색**,
  결과 클릭 시 해당 절/블록으로 이동 후 강조.

## 로드맵

1. ✅ 변환, History/Compare/Clean/Statistics, Change history CR, 다운로드, 탭, 검색, 참조 링크, 이동 감지.
2. ✅ CR 표지 → 절 단위 CR 매핑, Reason/Summary, WI code.
3. ✅ 38.822 feature 연결 (WI 기반 + 파라미터 이름).
4. 🔶 **Annotation 서버 (LLM)** — 골격 완료 (`server/`): pack 생성(CR·feature·Claude 의도 요약), 배포 API,
   클라이언트 동기화/표시. Chairman's notes 연결은 stub (`chairnotes.py`).
   - Chairman's notes / Tdoc agreements ↔ 스펙 변경 연결, 변경 의도 요약, 38.822 feature 매핑.
   - 클라이언트는 서버 없이도 완전히 동작해야 함(pack 은 선택).
   - pack 형식(초안):
     ```json
     {"spec": "38.211", "version": "17.3.0", "generated": "...",
      "blocks": [{"clause": "7.4.1.5.3", "raw_hash": "…", "crs": ["R1-2203456"],
                  "intent": "…", "features": ["38.822:23-1"], "agreements": [{"meeting": "RAN1#108-e", "url": "…"}]}]}
     ```
     `raw_hash` 로 붙이므로 로컬 변환 결과와 독립적으로 매칭된다.
5. ✅ 사용성: 북마크/메모, 인용 복사, Ctrl+P 팔레트, HTML 보고서 내보내기, Windows 패키징 설정(docs/PACKAGING.md).

### 다음 후보
- CR 본문의 track changes(w:ins/w:del)를 읽어 문단 단위로 정확한 CR 지정 (현재는 절 단위)
- Chairman's notes agreement ↔ 변경 연결 (서버)
- 수식 내부 diff (LaTeX 토큰 단위), 표 셀 단위 diff
- 38.212/38.213/38.214 등 여러 스펙 동시 다운로드 성능(병렬 변환)
