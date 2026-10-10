# specview annotation server

뷰어가 선택적으로 받아 쓰는 **annotation pack** 을 만들고 배포합니다. 서버가 없어도 뷰어는 완전히 동작합니다.

```bash
pip install -e ../backend -e ".[dev]"

# 1) specview 라이브러리 준비 (스펙 + CR 표지 + 38.822)
specview --home ./lib fetch 38.211 --with-crs
specview --home ./lib fetch-features

# 2) pack 생성 (규칙 기반: CR 매핑 + 38.822 feature)
specview-annotate --out packs build 38.211 --library ./lib/library.sqlite

# 2') + 변경 의도 요약 (LLM 선택)
#   사내 로컬 LLM (OpenAI 호환 API: vLLM / SGLang / Ollama / LM Studio 등)
specview-annotate --out packs build 38.211 --library ./lib/library.sqlite --version 16.1.0 \
    --llm openai --llm-base-url http://<사내LLM서버>:8000/v1 --llm-model glm-5.3      # 키가 필요하면 LLM_API_KEY
#   Gemini (GEMINI_API_KEY)
specview-annotate ... --llm gemini --llm-model gemini-3.8-flash --limit 10          # --limit: 처음 N개만 시험
#   Claude (ANTHROPIC_API_KEY 또는 `ant auth login`)
specview-annotate ... --llm claude

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
- `prompting.py` — 모든 모델이 공유하는 프롬프트와 출력 스키마
- `llm_openai.py` — OpenAI 호환 chat-completions (로컬 GLM 등, Gemini). json_schema 미지원 서버는 JSON 모드로 자동 전환,
  `<think>` 블록·코드펜스 제거
- `llm.py` — Claude(`claude-opus-5-5`) structured output, 안전 거절 시 서버측 fallback
- `chairnotes.py` — RAN1 Chairman's notes 연결 (골격)
- `app.py` — `GET /v1/packs`, `GET /v1/packs/{spec}/{version}.json`
