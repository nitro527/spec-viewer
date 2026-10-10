# specview annotation server

뷰어가 선택적으로 받아 쓰는 **annotation pack** 을 만들고 배포합니다. 서버가 없어도 뷰어는 완전히 동작합니다.

```bash
pip install -e ../backend -e ".[dev]"

# 1) specview 라이브러리 준비 (스펙 + CR 표지 + 38.822)
specview --home ./lib fetch 38.211 --with-crs
specview --home ./lib fetch-features

# 2) pack 생성 (규칙 기반: CR 매핑 + 38.822 feature)
specview-annotate --out packs build 38.211 --library ./lib/library.sqlite

# 2') + Claude 변경 의도 요약 (ANTHROPIC_API_KEY 또는 `ant auth login` 필요, 비용 발생)
specview-annotate --out packs build 38.211 --library ./lib/library.sqlite --version 16.1.0 --llm

# 3) 배포
specview-annotate --out packs serve --port 8800
```

뷰어: Library 탭 → "Annotation 서버"에 `http://<host>:8800` 저장 → 문단 선택 후 "서버에서 동기화".

## pack 형식 (`specview-annotations/1`)

```json
{"format": "specview-annotations/1", "spec": "38.211", "version": "16.1.0", "generated": "...",
 "generator": {"intent_model": "claude-opus-5-5"},
 "changes": [{"clause": "6.3.3.2", "title": "...", "raw_hash": "9d02c3ef8689a37dbe86", "change": "modified",
              "crs": [{"cr": "0046", "rev": "1", "tdoc": "RP-200688", "wg_tdoc": "R1-2005128", "wi_code": "NR_IAB-Core", ...}],
              "features": ["5.1.12:20-3"], "agreements": [],
              "intent": {"summary": "...", "motivation": "...", "impact": "...", "cr": "0046", "confidence": "high", "model": "..."}}]}
```

`raw_hash` 는 블록 HTML 의 내용 해시라서, 클라이언트가 같은 docx 를 직접 변환해도 그대로 붙습니다.

## 구성

- `pack.py` — 버전별 변경 블록 추출, CR/feature 매핑, intent 모델 호출
- `llm.py` — Claude(`claude-opus-5-5`) structured output 으로 의도 요약, 안전 거절 시 서버측 fallback
- `chairnotes.py` — RAN1 Chairman's notes 연결 (골격)
- `app.py` — `GET /v1/packs`, `GET /v1/packs/{spec}/{version}.json`
