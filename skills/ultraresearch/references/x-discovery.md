# X/Twitter 수집 — 에이전트 레시피

X에는 **공개 키워드 검색 API가 없다.** 그래서 수집기(`research/`)는 X를 직접 긁지 않고, `agent_routes.x`로 발굴 레시피만 내려준다. 실제 수집은 에이전트가 이 문서대로 한다: **WebSearch로 트윗 URL을 찾고 → 번들 엔진으로 본문을 회수**.

## 1) 트윗 URL 발굴 (WebSearch)

```
WebSearch: <주제> (site:x.com OR site:twitter.com)
WebSearch: <주제> when:7d            # 최근성
WebSearch: <주제> 후기 OR 추천 site:x.com   # 여론/추천 톤
WebSearch: "<브랜드명>" site:x.com    # 특정 브랜드 고정
```
- 한국어 주제는 **영문 각도도** 같이: 글로벌 X 담론은 영어가 많다.
- 검색 결과에서 `x.com/<handle>/status/<id>` 형태의 개별 트윗 URL과 자주 등장하는 핸들을 추린다.

## 2) 본문 회수 (번들 엔진, 무인증)

개별 트윗 — 가장 안정적(Phase 0 `tweet-result`/`oEmbed`):
```bash
<PY> -m engine "https://x.com/<handle>/status/<id>"
```
핸들 타임라인 — 최근 게시물 묶음(syndication; rate-limit 변동):
```bash
<PY> -m engine "https://x.com/<handle>"
```
- 엔진은 자동으로 Phase 0(공식 공개 엔드포인트)부터 시도한다. 단건 트윗은 거의 항상 회수된다.
- `--json`을 붙이면 trace/route를 구조화해 받는다(어느 경로로 뚫렸는지 확인).

## 3) rate-limit / 차단 시 (R5)

- 타임라인 syndication은 429가 잦다 → 짧게 백오프 후 재시도(엔진이 1회 재시도 내장). 그래도 막히면 **개별 트윗 URL**로 우회(단건은 보호가 얕다).
- 엔진이 `untried_routes`/`must_invoke_playwright_mcp`를 주면 그 경로를 마저 시도(insane-search R6). **429는 terminal이 아니다** — 포기 금지.
- 정말 안 되면 WebSearch 스니펫(검색 결과 요약)만으로 잠정 신호를 잡되, 리포트에 "본문 미회수, 검색 스니펫 기반"이라고 명시한다.

## 4) 트렌드 판정 (R2)

- 한 트윗 = 일화. **여러 독립 트윗/핸들**에서 같은 브랜드/주제가 반복돼야 "X에서 뜨는"이라고 말한다.
- 신호 보강: 검색 결과 다수 노출, 서로 다른 핸들의 언급, Reddit/HN 등 **다른 소스와의 교차 일치**.
- X는 점수 메타가 약하므로(공개 경로에 좋아요/RT 수가 일관되지 않음), **언급 빈도·교차출처**를 주 신호로 쓴다.

## 5) 경계

- 공개 트윗/타임라인/oEmbed만. 로그인 전용·보호된 계정·검색 내부 API는 건드리지 않는다.
- 막히면 "authentication required" 또는 "회수 실패"로 정직하게 보고하고, 추정으로 메우지 않는다.
