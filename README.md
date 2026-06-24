<div align="center">

# ultraresearch

**하나의 질의 → 여러 소스 병렬 고속 수집 → 교차검증 → 출처 인용 리포트.**

Claude Code용 멀티소스 트렌드·리서치 하네스.
[insane-search](https://github.com/fivetaku/insane-search)(차단 우회 수집)와
[lazycodex](https://github.com/code-yeongyu/lazycodex)식 하네스 패턴(fan-out → 증거 → 검증)을 조합했다.

</div>

---

## 무엇인가

X·Reddit·Hacker News에서 "요새 핫한 브랜드", "요새 뜨는 AI 개발 트렌드"를 빠르게 조사하는 스킬이다.
그냥 평소처럼 부탁하면 된다:

> *"X에서 요새 핫한 화장품 브랜드 찾아줘"*
> *"레딧·해커뉴스에서 요새 뜨는 AI 코딩 에이전트 트렌드 조사해줘"*
> *"ultraresearch rust web framework"*

Claude가 이 스킬을 잡아 **수집 → 교차검증 → 인용 리포트**까지 한 번에 돈다.

## 어떻게 동작하나 (2-레이어 하네스)

```
질의
 │
 ├─[수집기 research/]  HN·Reddit·dev.to·GitHub·arXiv 동시 수집 → 정규화·중복제거·랭킹 JSON   (보통 1~3초)
 │      └ 막히는 소스(Reddit)는 curl_cffi TLS 임퍼소네이션으로 우회
 │
 ├─[엔진 engine/]      검색 API 없는 X는 에이전트가 WebSearch→engine으로 트윗 개별 회수
 │      └ 번들된 insane-search: 차단된 단일 URL을 Phase 0→3으로 뚫음
 │
 └─[하네스 SKILL.md]   R1 수집 우선 · R2 단일출처는 트렌드 아님(2+ 교차검증) · R3 항상 인용
        · R4 최신성 창 · R5 차단≠포기 → 출처 인용 리포트
```

- **수집기**가 새 레이어다 — insane-search(단일 URL 리더)를 fan-out 리서치 프리미티브로 끌어올린다.
- **엔진**은 번들된 insane-search. 사회/커뮤니티 소스가 실제로 콘텐츠를 돌려주게 만드는 차단 우회 핵심.
- **SKILL.md**가 두 레이어를 묶는 하네스 규율(주장 전 수집·교차검증·인용·최신성).

## 소스

| 소스 | 잘 잡는 것 | 설치 |
|------|-----------|------|
| **x** | 소비자 브랜드·바이럴·실시간 여론 | 엔진(curl_cffi) |
| **reddit** | 커뮤니티 반응·솔직한 후기 | curl_cffi |
| **hn** | 개발/AI/스타트업 트렌드 | 불필요 |
| **devto** | 개발자 글·도구 화제 | 불필요 |
| **github** | 떠오르는 OSS(스타순·최근 푸시) | 불필요 |
| **lobsters** | 큐레이션 테크 글 | 불필요 |
| **arxiv** | AI/ML 신규 논문(깊이용) | 불필요 |
| **naver** | 한국어 블로그·뉴스(통합검색) | curl_cffi + bs4 |

> HN·dev.to·GitHub·Lobsters·arXiv는 **무설치**(stdlib만). Reddit·Bluesky·Naver·X-via-engine만 `curl_cffi>=0.15`가 필요하다.

## 설치

### A) Claude Code 플러그인으로

이 폴더를 로컬 마켓플레이스로 추가하거나, 깃 저장소로 올려 설치한다:

```
/plugin marketplace add <이 폴더 또는 깃 URL>
/plugin install ultraresearch
/reload-plugins
```

### B) 단일 스킬로 (가장 간단)

`skills/ultraresearch/` 폴더를 통째로 사용자 스킬 경로에 둔다:

```
~/.claude/skills/ultraresearch/      (SKILL.md, engine/, research/, references/)
```

### 의존성 (선택 — Reddit/X 쓸 때만)

```bash
# Windows는 py, macOS/Linux는 python3
py  -m pip install -U "curl_cffi>=0.15.0" beautifulsoup4 pyyaml     # Windows
python3 -m pip install -U "curl_cffi>=0.15.0" beautifulsoup4 pyyaml # macOS/Linux
```

> **Windows 주의:** `python`/`python3`가 Microsoft Store 스텁이면 무용지물이다. `py`를 쓰라
> (`py --version`이 `Python 3.x`를 출력하는지 확인). 스킬은 비ASCII 출력이 콘솔 코드페이지(cp949)에서
> 깨지지 않도록 stdout을 UTF-8로 강제한다.

## 직접 CLI로도 쓸 수 있다

스킬 폴더(`skills/ultraresearch/`)에서:

```bash
# 개발/AI 트렌드 — 무설치 4소스 병렬
py -m research "AI coding agent" --sources hn,devto,github,arxiv --since 14d --limit 5 --format md

# 자동 교차검증 리포트 (R2 검증/미검증 자동 분류)
py -m research "AI coding agent" --sources all --since 14d --format report

# 한국어 주제 (Naver 블로그·뉴스 포함)
py -m research "아이폰 18" --sources naver,bluesky,reddit --format report

# 브랜드/여론 — X 레시피 + Reddit
py -m research "indie skincare brand" --sources x,reddit --since 7d --format json

# 단일 차단 URL 직접 회수 (번들 엔진)
py -m engine "https://x.com/<handle>/status/<id>"
```

실측(개발 머신, 4소스 병렬): **약 1.3초**에 HN 38h·GitHub 수 시간·arXiv 12h 전 항목까지 최신으로 회수.

```
# ultraresearch — AI coding agent
## hn  (4)
- [I built Ponytrail, a local audit trail for AI coding-agent edits](...)  ▲23 · 💬13 · 38.9h
## github  (4)
- [bytedance/deer-flow](...)  ▲74087 · 💬943 · 2.9h
## arxiv  (4)
- ["Zooming In" on Agentic Web Browsers as Assistive Technologies ...](...)  12.0h
```

## 출력 스키마 (수집기 JSON)

```jsonc
{
  "query": "...", "since": "7d", "generated_at": "...Z",
  "by_source": { "hn": 12, "reddit": 8 },
  "items": [ { "source","title","url","author","score","comments",
               "created_at","age_hours","hotness","query","route" } ],
  "diagnostics": [ { "source","ok","note" } ],   // 막힌 소스도 정직하게
  "agent_routes": { "x": { "reason","do","note" } } // X 발굴 레시피
}
```

## 웹으로 쓰기 (Vercel 배포)

이 저장소는 Vercel에 **그대로 배포**해 누구나 브라우저에서 검색할 수 있는 웹 UI를 갖는다.

```
ultraresearch/
├── api/research.py     # Python 서버리스 핸들러 (= CLI와 동일 엔진)
├── public/index.html   # 정적 프런트엔드 (다크 UI, 소스 토글, 리포트 렌더)
├── vercel.json         # @vercel/python runtime + 라우팅
└── requirements.txt    # curl_cffi, beautifulsoup4
```

**로컬 dev 서버** (Vercel 동일 핸들러를 그대로 호출):

```bash
py scripts/dev_server.py --port 3010
# -> http://localhost:3010/
```

**배포:**

```bash
vercel link        # 최초 1회
vercel             # 프리뷰 배포
vercel --prod      # 프로덕션
```

### ⚠ 솔직한 IP 제약 (R5)

Vercel 데이터센터 IP에서는 **차단 우회가 약화**된다. 프런트엔드의 소스 칩에 ⚠ 마크로 표시했고 진단(`diagnostics`)에 자동 보고된다:

| 소스 | Vercel | 로컬 CLI |
|------|--------|----------|
| HN · GitHub · dev.to · Lobsters · arXiv | ✅ 정상 | ✅ |
| Reddit · Bluesky | ⚠ TLS·지역 게이팅으로 자주 차단 | ✅ |
| Naver | ❌ Vercel IP 명시 차단(실측) | ✅ |
| X | n/a (검색 API 없음 — 에이전트 경로만) | ✅ (engine 경유) |

**전략:** Vercel에는 공개 API 5개만 두고, 한국어/소셜 리서치는 로컬 CLI(또는 자가 서버)로 — 같은 코드, 같은 명령(`py -m research`). 정직한 진단 = 데이터센터 IP의 한계 그대로 보여주기.

### 환경 변수 (선택)

| 키 | 기본값 | 용도 |
|---|---|---|
| `ULTRARESEARCH_CACHE_DIR` | `/tmp/ultraresearch-cache` | 핸들러 캐시 디렉터리 |
| `ULTRARESEARCH_CACHE_TTL` | 600 | 캐시 TTL (초) |
| `ULTRARESEARCH_MAX_LIMIT` | 30 | 소스당 최대 항목 |
| `ULTRARESEARCH_MAX_SOURCES` | 8 | 최대 동시 소스 수 |
| `GITHUB_TOKEN` | (없음) | GitHub 검색 rate-limit 상향 |

## 라이선스 · 귀속

MIT (see [LICENSE](LICENSE)). `skills/ultraresearch/engine/`는
[fivetaku/insane-search](https://github.com/fivetaku/insane-search)(MIT)를 벤더링한 것이며
Windows UTF-8 패치 등 로컬 수정이 있다 — 자세한 내용은 [NOTICE](NOTICE).
오케스트레이션·수집기·문서는 ultraresearch 고유 저작물이다.
