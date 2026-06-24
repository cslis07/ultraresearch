---
name: ultraresearch
description: >
  Fast multi-source trend & research harness. Fan-out collects from Hacker News,
  Reddit, dev.to, GitHub, arXiv (and X/Twitter via the agent), cross-verifies
  across sources, and writes a cited report. Use when the user wants to know
  what's hot/trending, gather opinions across the web, scan a topic, or research
  a brand/tool/technology — especially on X, Reddit, or Hacker News where plain
  WebFetch gets blocked. Built on the bundled insane-search engine (block-resistant
  public-page reader) so social/community sources actually return content.
  Korean triggers: 리서치 해줘, 트렌드 조사, 요새 핫한, 가장 잘나가는, X에서 뜨는,
  레딧 반응, 해커뉴스 트렌드, 브랜드 조사, 여론 수집, ultraresearch, 울트라리서치.
  English triggers: ultraresearch, research this, what's trending, hot topics,
  scan the discourse, gather opinions, brand research, dev trends, find buzz.
  Do NOT trigger for a single known-URL fetch (use insane-search/WebFetch) or a
  one-shot factual lookup WebSearch answers directly.
---

# ultraresearch

> 하나의 질의 → 여러 소스에서 **병렬·고속 수집** → 교차검증 → **출처 인용 리포트**.
> insane-search 엔진(차단 우회 수집)을 fan-out 리서치 프리미티브로 끌어올린 하네스.

이 스킬은 두 레이어로 동작한다:
- **수집기**(`research/`) — HN·Reddit·dev.to·GitHub·arXiv를 동시에 긁어 정규화·중복제거·랭킹한 JSON을 돌려준다. 결정적이고 빠르다(보통 1~3초).
- **엔진**(`engine/`) — 번들된 insane-search. 차단된 단일 URL(특히 X 트윗/타임라인)을 Phase 0→3으로 뚫는다. X처럼 검색 API가 없는 소스는 에이전트가 이 엔진으로 개별 수집한다.

---

## 하네스 규칙 (지켜야 하는 고삐)

**R1 — 주장 전에 수집한다.** "요새 핫한 X"를 답하기 전에 반드시 `research/` 수집기를 먼저 돌린다. 기억·추측으로 트렌드를 단정하지 않는다. 수집 결과(JSON)가 1차 근거다.

**R2 — 단일 출처는 트렌드가 아니다(교차검증).** 어떤 브랜드/주제/도구를 "뜨고 있다"고 말하려면 **서로 다른 2개 이상의 독립 항목/소스**에서 확인돼야 한다. 1건뿐이면 리포트에 `미검증(single-source)`으로 표시한다.

**R3 — 항상 인용한다.** 모든 핵심 주장 옆에 출처 URL을 단다. 출처 없는 문장은 쓰지 않는다. 수집 JSON의 `url`·`route`가 출처다.

**R4 — 최신성 창을 존중한다.** 사용자가 "요새/최근/이번 주"라고 하면 `--since`(기본 7d)를 그 의도에 맞춘다. 리포트의 각 항목에 나이(`age_hours`)를 반영하고, 오래된 항목을 최신 트렌드로 포장하지 않는다.

**R5 — 차단 ≠ 포기.** 소스가 막히면(특히 Reddit/X) 수집기의 진단(`diagnostics`)과 `agent_routes`를 읽고 엔진/WebSearch로 우회한다. insane-search의 경계는 그대로 상속한다: **로그인·페이월에서 멈추고 그렇다고 보고**하되, 공개 경로는 끝까지 시도한다.

---

## Step 0 — 인터프리터 & 의존성 (세션당 1회)

수집기와 엔진은 Python으로 돈다. **이 스킬 폴더(SKILL.md가 있는 디렉터리)에서 실행**해야 `python -m research` / `python -m engine`가 잡힌다.

**인터프리터 해석(`<PY>`):** 순서대로 시도해 진짜 Python 3가 나오는 첫 명령을 쓴다.
- Windows: 보통 **`py`** (주의: `python`/`python3`는 Microsoft Store 스텁이라 무용지물일 수 있다 — `<PY> --version`이 `Python 3.x`를 출력하는지로 확인).
- macOS/Linux: 보통 **`python3`**.

**의존성(필요할 때만):**
- HN·dev.to·GitHub·arXiv → **설치 불필요**(stdlib만). 바로 된다.
- Reddit, X-via-engine → `curl_cffi>=0.15` 필요. 없으면 한 번 설치:
  ```bash
  <PY> -m pip install -U "curl_cffi>=0.15.0" beautifulsoup4 pyyaml
  ```
  수집기는 미설치 시 크래시하지 않고 해당 소스를 `diagnostics`에 "blocked"로 보고한다 — 그때 설치하고 재시도하면 된다.

---

## 워크플로우

### Phase 1 — 범위 & 검색 각도

1. 질의에서 **주제 + 의도 + 최신성**을 뽑는다. (예: "X에서 요새 핫한 화장품 브랜드" → 주제=화장품 브랜드, 의도=떠오르는 브랜드 발견, 최신성≈최근 몇 주, 핵심 소스=X)
2. **소스 선택**(아래 소스 가이드 참고). 사용자가 특정 소스를 짚으면 그것을 우선.
3. **2~4개 검색 각도**를 만든다 — 동의어/영문/관점 변형. (예: "indie skincare brand", "viral cosmetics", "K-beauty trending") 한국어 주제는 영문 각도도 추가하면 HN/Reddit 커버리지가 올라간다.

### Phase 2 — Fan-out 수집

스크립트 가능한 소스는 수집기 한 방으로:
```bash
<PY> -m research "<주제 또는 각도>" --sources hn,reddit,devto,github --since 7d --limit 15 --format json
```
- `--sources all` = hn,reddit,devto,github,arxiv. `x`를 넣으면 `agent_routes.x`(에이전트 수집 레시피)가 출력에 포함된다.
- 각도가 여러 개면 각도별로 호출하거나, 핵심 각도 1~2개로 호출 후 결과를 보고 추가한다.
- 출력 JSON의 `items`(정규화·랭킹됨), `by_source`, `diagnostics`, `agent_routes`를 읽는다.

**X/Twitter 수집(검색 API 없음 → 에이전트가 직접):** 출력의 `agent_routes.x`를 따른다:
1. `WebSearch`로 트윗 URL 발굴: `<주제> (site:x.com OR site:twitter.com)`, 최근성 필요시 `when:7d`.
2. 각 트윗 URL을 엔진으로 회수(무인증, Phase 0):
   ```bash
   <PY> -m engine "<tweet_url>"
   ```
3. 핸들만 있으면 타임라인: `<PY> -m engine "https://x.com/<handle>"` (syndication; rate-limit 시 백오프 후 재시도).
4. 막히면 R5 — 엔진 trace를 보고 다른 경로/재시도. 단건 트윗은 `tweet-result`/`oEmbed`로 거의 항상 회수된다.

> 네이버/유튜브/기타 단일 URL이 필요하면 동일하게 `<PY> -m engine "<URL>"`. 엔진이 알아서 에스컬레이션한다.

### Phase 3 — 교차검증 (적대적)

- **중복·동일주장 묶기:** 수집기가 URL 기준 1차 dedup을 했지만, 의미상 같은 브랜드/주제를 가리키는 항목들을 손으로 묶는다.
- **R2 적용:** 각 후보를 "≥2개 독립 출처에서 등장하는가?"로 가른다. 통과=트렌드 후보, 실패=`미검증`.
- **최신성·신호 점검:** `age_hours`로 최근인지, `score`/`comments`로 실제 관심도가 있는지 본다. 점수 없는 소스(Reddit RSS, arXiv)는 "관심도 미상"으로 다룬다.
- **반례 탐색:** 강하게 보이는 주장일수록 반대/회의 의견이 있는지 한 번 더 찾는다(특히 X/Reddit).

### Phase 4 — 합성 (인용 리포트)

아래 템플릿으로 한국어 리포트를 쓴다(사용자 언어에 맞춘다). 모든 항목에 출처 링크.

```markdown
# 리서치: <주제>
_수집: <소스들> · 최신성 <since> · 생성 <UTC>_

## TL;DR
- <검증된 핵심 발견 3~5개, 각 줄 끝에 출처 링크>

## 떠오르는 <브랜드/도구/주제>
| # | 이름 | 왜 뜨는가 | 신호 | 출처 |
|---|------|-----------|------|------|
| 1 | … | … | ▲점수·💬댓글·N시간 | [링크](url), [링크](url) |

## 소스별 하이라이트
### X · Reddit · Hacker News · …
- <항목 — 한 줄 요약> ([출처](url))

## 미검증 / 단일 출처 (주의)
- <1건만 나온 것들 — 참고용> ([출처](url))

## 한계
- <막힌 소스, 최신성 한계, 점수 미상 소스 등 정직하게>
```

---

## 소스 가이드 — 무엇을 언제

| 소스 | 잘 잡는 것 | 신호 | 설치 |
|------|-----------|------|------|
| **x** | 소비자 브랜드·바이럴·실시간 여론·인플루언서 화제 | (점수 없음) | 엔진(curl_cffi) |
| **bluesky** | **스크립트 가능한 소셜 키워드 검색**(X가 못 함) — 브랜드·여론·실시간 | ♥likes·💬replies | curl_cffi |
| **reddit** | 커뮤니티 반응·솔직한 후기·니치 트렌드 | RSS엔 점수 없음 | curl_cffi |
| **hn** | 개발/AI/스타트업/테크 트렌드, 신제품 | ▲points·💬comments | 불필요 |
| **lobsters** | 큐레이션된 테크 글(품질 높음, 양 적음) | ▲score·💬 | 불필요 |
| **devto** | 개발자 글·튜토리얼·도구 화제 | ▲reactions·💬 | 불필요 |
| **github** | 떠오르는 OSS 도구/레포(스타순·최근 푸시) | ★stars | 불필요 |
| **arxiv** | AI/ML 학술 신규 논문(트렌드 아님, 깊이용) | (날짜순) | 불필요 |

기본 소스(`--sources` 생략 시): **hn, reddit, bluesky, devto, github**. `--sources all`=전체.

- **소비자/브랜드/여론 리서치** → **bluesky + x + reddit** 중심. (예: 화장품, 패션, 식음료) bluesky는 X와 달리 키워드 검색이 스크립트로 되니 1차 신호로 먼저 돌리고, X는 WebSearch로 보강한다.
  - ⚠ **bluesky 공개 검색은 NSFW가 섞인다** — 수집기가 self-label/태그 기반으로 1차 필터하지만 완벽하지 않다. 브랜드 리포트엔 명백한 성인 항목을 손으로 한 번 더 거른다.
- **개발/AI 트렌드 리서치** → hn + reddit + github + lobsters + devto. arxiv는 깊이 보강용.
- 한국어 주제는 영문 검색 각도를 같이 돌려 HN/Reddit/Bluesky 커버리지를 확보한다. (한국 커뮤니티 신규 콘텐츠는 WebSearch 경유가 보강책)

---

## 경계 (insane-search에서 상속)

- **공개 콘텐츠 리더다.** 공개 페이지·공개 API·피드로 닿는 것만 가져온다.
- **로그인·페이월에서 멈춘다** — 뚫지 않고 "authentication required"로 보고한다.
- 자격 증명을 저장·전송하지 않는다. 사용자를 대신해 로그인하지 않는다.
- **트렌드를 지어내지 않는다.** 수집 근거가 약하면 약하다고 말한다(R2). 빈 결과는 빈 결과로 보고한다.

## 참조 (references/) — 필요할 때만 읽는다

| 파일 | 언제 |
|------|------|
| [`references/sources.md`](references/sources.md) | 각 소스의 정확한 엔드포인트·파라미터·필드 매핑을 손볼 때, 새 소스를 추가할 때 |
| [`references/x-discovery.md`](references/x-discovery.md) | X 수집이 잘 안 될 때 — WebSearch 쿼리 패턴, 트윗 URL→엔진 회수, 핸들 타임라인, rate-limit 우회 |
| `engine/` 내부 문서 | 엔진 자체를 깊게 다뤄야 할 때 — 번들된 insane-search의 `SKILL.md`/`references/`는 제거됨. 원본은 github.com/fivetaku/insane-search 참고 |
