# spec-viewer

3GPP 스펙의 **버전 이력**을 한눈에 보는 데스크톱 뷰어.

- **History 모드**: 문서의 어느 부분이 어느 버전에서 추가·변경됐는지 단어 단위로 색칠
  (릴리즈별 색상, 같은 릴리즈 안에서는 후반 버전일수록 진하게: 예) Rel-16 노랑 → 주황)
- **Compare 모드**: 임의의 두 버전을 코드 리뷰처럼 비교 (inline / side by side)
- **이동 감지**: 다른 절로 옮겨진 문단은 "§X 에서 이동"으로 표시하고 원래 버전 색을 유지
- **CR 연결**: Change history 표 + CR 표지(Reason / Summary / **Clauses affected** / WI code)를 받아
  절마다 "이 절을 수정한 CR"을 표시 (38.211 Rel-16 기준 diff 결과와 96~98% 일치)
- **TR 38.822 UE feature 연결**: CR 의 work item → 38.822 의 해당 WI 섹션 feature, 본문 용어와 겹치는 것부터
- **Feature 로 찾기**: 새 뷰어 탭 → "Feature 로 찾기"에서 38.822 feature 를 고르면 받은 모든 스펙의 관련 절을
  스펙별로 모아 보여 줌 (근거: 그 WI 의 CR 이 고친 절 / 본문에 RRC 필드 이름이 나오는 절, 겹치는 용어 순)
- **Statistics**: 버전별 변경 절 수·CR 수, 가장 자주 바뀐 절
- **AI 변경 의도 요약(선택)**: 메인 탭에서 LLM API(Gemini 또는 사내 OpenAI 호환 서버: vLLM·Ollama 등)를 설정하면
  바뀐 문단마다 "무엇이·왜·구현 영향"을 CR 표지 근거로 요약 (버전 단위 또는 절 하나씩, 이미 요약한 문단은 재요청 안 함)
- **회의 합의 연결**: AI 요약 때 CR 의 WG 회의(RAN1#95 등) 최종 회의록에서 그 변경의 Agreement/Decision/TP 를 찾아
  패널에 보여 주고 요약 근거로 사용 (묶음 CR 은 그 안의 개별 draft CR 표지도 받아 씀)
- **회의 논의 (FL summary)**: 그 합의가 나온 안건의 Feature lead / Moderator summary(모든 개정본)에서
  이 변경과 관련된 쟁점·제안·회사 의견을 발췌해 보여 주고 요약 근거로 사용 (회의 Tdoc 목록 xlsx 로 찾음)
- **회의 문서 뷰어(새 창)**: FL summary·회의록·draft CR 을 앱 안의 별도 창으로 열어 해당 문단/합의 위치로 바로 이동
  (수식·그림 포함, 문서 안 R1-xxxx 링크는 같은 회의의 그 문서를 또 새 창으로)
- **CR 문서 보기(새 창)**: CR 목록·비교 화면에서 CR 원문(표지와 바뀐 절)을 같은 창으로 열고, CR 의 수정 표시는
  앱의 diff 와 같은 <ins>추가</ins>·<del>삭제</del> 로 보여 줌
- **문단 변화 이력**: 문단을 클릭하면 그 문단이 가졌던 모든 텍스트를 오래된 버전부터 단계별로 — 바뀐 부분, 그 버전의
  CR, 단계별 AI 요약(한 단계씩 또는 요약 없는 단계 모두)
- **CR 단위 분석**: 문단을 요약할 때 관련 CR 을 먼저 통째로 분석(CR 수정 내용·세부 CR·회의 합의·FL 요약 근거로
  무엇을·왜·회의 결정과 대안·절별 변경·구현 영향)해 저장하고, 문단 요약은 그 분석을 근거로 씀. 같은 CR 이 바꾼 다른 문단은
  저장된 분석을 바로 보여 주고 재요청하지 않음. CR 문서 창 위쪽에서 전체 분석을 보고 "AI 로 이 CR 분석"/"다시 분석" 가능
- **CR 목록(CR 모드)**: 스펙의 Change history CR 전체를 한 표로 — 릴리즈·카테고리·WI·AI 분석 여부·텍스트(제목, Tdoc,
  절, 분석 내용)로 거르고, 펼치면 표지·AI 분석 요약·바뀐 절(클릭 시 그 절로 이동)·CR 문서 창. 걸러진 CR 중 분석 없는 것을
  한 번에 차례로 AI 분석 (예: WI 하나의 CR 전부)
- **WI 단위 요약**: CR 모드에서 WI 를 고르면 받은 모든 스펙에 걸친 그 WI 의 CR(스펙별 개수, CR 분석 수)과 AI 요약 —
  기능 개요·도입 배경·스펙별 변경 절(클릭 시 그 절, 다른 스펙은 새 뷰어 탭)·진행 과정(도입 CR → 이후 수정)·구현 영향.
  CR 표지와 저장된 CR 분석을 근거로 쓰므로 CR 분석을 먼저 해 두면 더 정확
- **AI 채팅**: 상단 "AI 채팅" 탭에서 스펙 질문 → AI 가 받은 스펙(모든 버전)·변경 이력·CR·회의 합의·AI 분석을 도구로 직접
  찾아 읽고(검색·절 읽기·이력·버전 비교·CR·WI) 출처를 달아 답함. 출처를 누르면 그 버전의 그 절/CR 문서가 열림.
  뷰어 문단 패널의 "AI 에게 질문"으로 문단을 맥락으로 질문. 도구 호출을 못 하는 LLM 서버는 검색 결과 기반으로 자동 전환
- **전체 AI 분석(퇴근할 때 걸어 두기)**: 메인 탭 ④ — 받은 모든 스펙의 모든 버전 문단 요약(최신 버전부터, CR 분석 포함) →
  남은 CR 분석 → 모든 WI 요약을 한 번에. 10개 요청마다 저장·이미 한 것은 건너뜀(멈췄다 다시 시작하면 이어서),
  LLM 서버가 끊기면 기다렸다 재시도, 도는 동안 Windows 절전 방지, 진행 상황·중지 버튼
- **AI 릴리즈 노트**: Compare 에서 두 버전 사이 CR 을 WI 별 기능·주요 수정·구현 영향으로 정리(저장, HTML/Markdown 내보내기).
  채팅에서도 "v16.1.0 → v16.2.0 뭐 바뀌었어?"에 답함
- **AI 결과 내보내기**: 채팅 답변(⧉ Markdown 복사·⬇ HTML, 출처 목록 포함)·대화 전체·릴리즈 노트를 파일로
- **새 버전·관심 절 알림**: 메인 탭 맨 위 — 받은 스펙의 3GPP 새 버전(새 버전만 받기), 북마크·메모한 절 중 표시 이후
  바뀐 것(누르면 그 뒤 변경만 색칠해 열림, "확인함")
- **이 절을 인용하는 곳**: 정보 패널 — 이 절을 인용하는 문단(같은 스펙·받은 다른 스펙)을 찾아 영향 범위 확인, AI 채팅 도구로도 사용
- **CR 목록 CSV(Excel) 내보내기**: 걸러진 CR 목록을 표지·AI 분석·포털 링크와 함께
- **분석 현황판**: 메인 탭 ④ 아래 — 스펙·버전별 문단 요약/CR 분석/WI 요약 진행률과 마지막 전체 분석 결과
- **문구 이력 검색**: 검색창(Ctrl+F)의 "버전별" — 문구(단어 순서 그대로)가 처음 나온 버전, 버전별 절 수 막대,
  버전마다 그 문구가 생기거나 사라진 절(스니펫). 항목을 누르면 그 버전으로 바꿔 그 절로 이동
- **Annotation 서버(선택)**: 서버에서 만든 변경 의도 요약(LLM) pack 을 받아 표시 — `server/` 참고
- 절 단위 스트리밍 스크롤, 전체 검색(Ctrl+F, SQLite FTS5), 변경 위치 눈금자, 목차 필터/접기
- 메인 탭(다운로드·설정)과 뷰어 탭 분리: 새 뷰어 탭(+, Ctrl+T)에 받은 스펙이 카드로 한눈에 보임
- 스펙 간 참조 링크(`clause 5.2.1 of TS 38.213` 클릭 → 새 뷰어 탭, 안 받은 스펙이면 메인 탭에 다운로드 제안), Ctrl+P 명령 팔레트
- 북마크·메모(버전이 바뀌어도 절에 유지), 인용 복사, 비교 결과 HTML 보고서 내보내기
- 3GPP archive 에서 스펙 전 버전 자동 다운로드 (여러 스펙 동시 선택, 릴리즈 필터)
- 구형 Equation Editor/MathType 수식(WMF)을 내장 변환기로 SVG 렌더링

**사용 방법은 화면 캡처와 함께 [docs/GUIDE.md](docs/GUIDE.md)에 자세히 정리되어 있습니다.**

설계와 로드맵은 [docs/DESIGN.md](docs/DESIGN.md) 참고.

## 구성

```
backend/   Python: 다운로드 → docx 변환(pandoc) → 절/블록 분할 → SQLite 저장 → 이력 계산 → 로컬 API
app/       Electron + React + TypeScript 뷰어 (백엔드를 자식 프로세스로 실행)
server/    annotation 서버 (pack 생성: CR 매핑 + 38.822 + Claude 변경 의도 요약, pack 배포 API)
docs/      설계 문서, Windows 패키징 (docs/PACKAGING.md)
```

## 필요 환경

- Python 3.10+
- [pandoc](https://pandoc.org/installing.html) (docx → HTML/수식 변환)
- Node.js 20+
- (선택) LibreOffice: 구형 `.doc` 스펙과 EMF 그림 변환용 (WMF 수식은 내장 변환기가 처리)

## 실행

```bash
# 1) 백엔드 설치
cd backend
pip install -e ".[dev]"

# 2) 스펙 받기 (CLI 로도 가능, 앱의 메인 탭에서도 가능)
specview fetch 38.211 --with-crs          # 모든 버전 + CR 표지 (--release 16, --latest-per-release 로 줄이기)
specview fetch-features                   # TR 38.822 UE feature list
#   이미 받은 파일이 있다면:  specview import 38211-g10.docx 38211-h50.docx ...

# 3) 앱 빌드 & 실행
cd ../app
npm install
npm run build
npm run electron
```

라이브러리는 기본적으로 `~/.specview/` 에 저장됩니다 (`SPECVIEW_HOME` 또는 `--home` 으로 변경).
Windows 에서 Python 경로가 다르면 `SPECVIEW_PYTHON` 환경 변수로 지정하세요.

개발 모드:

```bash
specview serve --port 8765        # 터미널 1
cd app && npm run dev             # 터미널 2 → http://localhost:5173 (브라우저로도 확인 가능)
npm run electron:dev              # (선택) Electron 창으로 dev 서버 열기
```

## 단축키

| 키 | 동작 |
|---|---|
| Ctrl+F | 스펙 전체 검색 |
| Ctrl+P | 명령 팔레트 (절 번호·제목, `38.213 10.1`, `v16.2`, 모드) |
| Ctrl+T / Ctrl+W | 새 탭 / 탭 닫기 |
| Alt+← / Alt+→ | 이전 / 다음 버전 |
| 타임라인 클릭 / Shift+클릭 | 해당 버전 보기 / 변경 표시 기준 버전 지정 |

## 테스트

```bash
cd backend && python -m pytest
cd server && python -m pytest
cd app && npm run typecheck
```

테스트는 3GPP 문서 구조를 흉내 낸 합성 docx(`backend/tests/fixtures/make_fixture.py`)를 사용합니다.
