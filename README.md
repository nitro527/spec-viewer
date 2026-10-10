# spec-viewer

3GPP 스펙의 **버전 이력**을 한눈에 보는 데스크톱 뷰어.

- **History 모드**: 문서의 어느 부분이 어느 버전에서 추가·변경됐는지 단어 단위로 색칠
  (릴리즈별 색상, 같은 릴리즈 안에서는 후반 버전일수록 진하게: 예) Rel-16 노랑 → 주황)
- **Compare 모드**: 임의의 두 버전을 코드 리뷰처럼 비교 (inline / side by side)
- **이동 감지**: 다른 절로 옮겨진 문단은 "§X 에서 이동"으로 표시하고 원래 버전 색을 유지
- **CR 연결**: Change history 표 + CR 표지(Reason / Summary / **Clauses affected** / WI code)를 받아
  절마다 "이 절을 수정한 CR"을 표시 (38.211 Rel-16 기준 diff 결과와 96~98% 일치)
- **TR 38.822 UE feature 연결**: CR 의 work item → 38.822 의 해당 WI 섹션 feature, 본문 용어와 겹치는 것부터
- **Statistics**: 버전별 변경 절 수·CR 수, 가장 자주 바뀐 절
- **Annotation 서버(선택)**: 서버에서 만든 변경 의도 요약(LLM) pack 을 받아 표시 — `server/` 참고
- 절 단위 스트리밍 스크롤, 전체 검색(Ctrl+F, SQLite FTS5), 변경 위치 눈금자, 목차 필터/접기
- 탭 + 스펙 간 참조 링크(`clause 5.2.1 of TS 38.213` 클릭 → 새 탭), Ctrl+P 명령 팔레트
- 북마크·메모(버전이 바뀌어도 절에 유지), 인용 복사, 비교 결과 HTML 보고서 내보내기
- 3GPP archive 에서 스펙 전 버전 자동 다운로드 (여러 스펙 동시 선택, 릴리즈 필터)
- 구형 Equation Editor/MathType 수식(WMF)을 내장 변환기로 SVG 렌더링

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

# 2) 스펙 받기 (CLI 로도 가능, 앱의 Library 탭에서도 가능)
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
