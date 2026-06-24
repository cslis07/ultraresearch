# 소스 엔드포인트 레퍼런스

`research/collectors.py`가 실제로 호출하는 엔드포인트와 필드 매핑. 새 소스를 추가하거나 파싱을 손볼 때 본다. 모든 수집기는 **예외를 삼키고** `diagnostics`에 상태만 남긴다(한 소스가 죽어도 전체 수집은 계속된다).

## 정규화 스키마 (`normalize.Item`)

| 필드 | 의미 |
|------|------|
| `source` | hn / reddit / devto / github / arxiv |
| `title`, `url` | 제목, 원본 링크 |
| `author` | 작성자/소유자 |
| `score` | 소스 고유 인기 신호 (HN points · GitHub stars · dev.to reactions). 없으면 null |
| `comments` | 댓글/이슈 수 (가능할 때) |
| `created_at` | ISO-8601 UTC |
| `age_hours`, `hotness` | 파생값. `hotness`는 **소스 내** 정렬용 — 소스 간 비교 불가 |
| `query`, `route` | 이 항목을 발굴한 검색 각도 / 수집 경로(출처 추적) |

`hotness = (score+1) / (age_hours+2)^1.5` — HN식 중력. 점수가 다른 소스끼리 절대 비교하지 말 것.

## Hacker News — Algolia (무인증 JSON)

```
https://hn.algolia.com/api/v1/search?query={q}&tags=story&hitsPerPage={n}
  &numericFilters=created_at_i>{cutoff_unix}      # --since 있을 때만
```
- 관련도순(인기 가중). 최신순이 필요하면 `search_by_date`로 교체 가능.
- 필드: `hits[].title|url|author|points|num_comments|created_at_i|objectID`.
- `url`이 null(Ask/Show HN 본문글)이면 `https://news.ycombinator.com/item?id={objectID}`로 대체.
- WAF 없음 → stdlib `urllib`로 충분.

## Reddit — search.rss (TLS 임퍼소네이션 필요)

```
https://www.reddit.com/search.rss?q={q}&sort=top&t={window}&limit={n}
```
- `window`: `--since`→`day|week|month|year|all` 매핑(`normalize.reddit_time_window`).
- 평문 요청은 403 → `curl_cffi`(impersonate=safari)로 회수, 실패 시 번들 엔진(`engine.fetch`) 폴백.
- Atom 파싱(`{http://www.w3.org/2005/Atom}`). `/comments/` 포함 링크만 = 실제 포스트(서브레딧/유저 행 제외).
- **RSS엔 점수·댓글수가 없다.** 점수가 꼭 필요하면 개별 포스트를 `engine`으로 `.json` 회수해야 하지만(OAuth/WAF 변동) MVP 범위 밖.

## dev.to — Articles API (무인증 JSON)

```
https://dev.to/api/articles?per_page={3n..30}&top={days}
```
- 자유 텍스트 검색 API가 빈약 → 상위글을 받아 **클라이언트에서 질의어 필터**(title+description+tags).
- `days`: `--since`→일 환산(기본 30, 상한 365).
- 필드: `title|url|user.name|positive_reactions_count|comments_count|published_at|description|tag_list`.

## GitHub — Search Repositories (무인증, rate-limit 10/분)

```
https://api.github.com/search/repositories?q={q}+pushed:>{YYYY-MM-DD}&sort=stars&order=desc&per_page={n}
```
- "떠오르는 OSS" 프록시: 스타순 + 최근 푸시. `--since`로 `pushed:>` 컷오프.
- 헤더: `Accept: application/vnd.github+json`. `GITHUB_TOKEN`/`GH_TOKEN` 환경변수 있으면 `Authorization: Bearer`로 한도 상향.
- 403이면 rate-limit → 토큰 권유. 필드: `items[].full_name|html_url|owner.login|stargazers_count|open_issues_count|pushed_at|description`.
- 주의: `comments`에 `open_issues_count`를 매핑(근사 신호). 정밀 지표 아님.

## arXiv — Atom API (무인증)

```
http://export.arxiv.org/api/query?search_query=all:{q}&sortBy=submittedDate&sortOrder=descending&max_results={n}
```
- 신규 논문 최신순. **트렌드가 아니라 학술 깊이** 보강용 — 느슨한 매칭이라 노이즈가 섞인다.
- 필드: entry `title|id(url)|author/name(다수)|published|summary`.

## Bluesky — AT Protocol searchPosts (스크립트 가능한 소셜 검색)

```
https://api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={q}&limit={n}&sort=top&since={ISO}
```
- **`api.bsky.app`를 쓴다 — `public.api.bsky.app`는 일부 지역(예: 한국, BunnyCDN-KR 엣지)에서 403**. AppView가 TLS로 게이팅하므로 `curl_cffi`(impersonate=safari), 실패 시 엔진 폴백.
- X가 못 하는 **무인증 키워드 검색 + engagement 지표**가 핵심 가치. 필드: `posts[].record.text|createdAt`, `author.handle`, `likeCount`(→score), `replyCount`(→comments), `uri`.
- 웹 URL 구성: `uri`(`at://did/app.bsky.feed.post/{rkey}`)의 rkey + handle → `https://bsky.app/profile/{handle}/post/{rkey}`.
- **NSFW 주의:** 공개 검색은 성인 콘텐츠가 많다. 수집기가 self-label(`porn|sexual|nudity|graphic-media`)과 명백한 해시태그로 1차 필터하고 `diagnostics`에 `dropped N NSFW`를 남긴다. 라벨 없는 케이스는 통과할 수 있으니 브랜드 리포트엔 손검수 권장.

## Lobsters — hottest.json + 쿼리 필터

```
https://lobste.rs/hottest.json        # search.json은 400 — 없음. hottest 받아 클라이언트 필터
```
- 검색 API가 없어 dev.to 방식: 현재 hottest 목록을 받아 `title`+`tags`로 질의어 필터. **시간 창(`--since`) 미적용**(hottest는 현재 인기 스냅샷).
- WAF 없음 → stdlib. 필드: `title|url|comments_url|score|comment_count|created_at|submitter_user.username|tags`.
- `url`(외부 링크) 우선, 없으면 `comments_url`(Ask Lobsters 등). 테크 큐레이션 품질이 높지만 양은 적다.

## Naver — search.naver.com 통합검색 (한국어 블로그·뉴스)

```
https://search.naver.com/search.naver?where=blog&query={q}&sort=1   # 최신순, 블로그 탭
https://search.naver.com/search.naver?where=news&query={q}&sort=1   # 최신순, 뉴스 탭
```
- 무인증 HTML 스크래핑. `curl_cffi`(impersonate=safari) + `beautifulsoup4` 필요. 막히면 번들 엔진(`_engine_text`) 폴백.
- **마크업 안정성**: 네이버는 검색 결과 마크업을 자주 갈아 CSS 셀렉터가 깨진다. 대신 **포스트 URL 정규식**으로 앵커를 잡고(`blog.naver.com/{handle}/{post_id}` 또는 `n.news.naver.com/.../article/...`), 거기서 카드 부모로 **올라가서** 헤드라인을 뽑는다(`_naver_title_for`). 뉴스는 앵커 텍스트가 `"네이버뉴스"`라 무용지물 → `stripped_strings`에서 junk 필터(`네이버뉴스`/`Keep에 바로가기` 등) 후 첫 12~140자 헤드라인을 잡는다.
- **점수·날짜 미회수**: 검색 페이지에는 좋아요·댓글 수가 없고 발행시각도 카드별로 일관되지 않다. score/comments/created_at은 `None`. 정밀 신호가 필요하면 각 포스트 URL을 엔진으로 회수해 본문에서 추출(MVP 범위 밖).
- 한국어 검색은 `quote_plus`로 URL 인코딩. `Accept-Language: ko-KR` 헤더 권장.

## 새 소스 추가 체크리스트

1. `collectors.py`에 `collect_<name>(query, *, since, limit, diag) -> list[Item]` 추가. 예외 삼키고 `diag.append({...})`.
2. 무WAF JSON이면 `_http_json`, 차단 소스면 `_cffi_text`→`_engine_text` 폴백 체인.
3. `COLLECTORS` 레지스트리에 등록. 기본 노출이면 `DEFAULT_SOURCES`에도.
4. 필드를 `Item`으로 정규화(특히 `created_at`은 `to_iso`로). 점수 없으면 `score=None`.
5. `references/sources.md`(이 파일)와 `SKILL.md` 소스 가이드 표 갱신.
6. 검색 API가 없으면(예: X) 수집기에 넣지 말고 `__main__._x_route`처럼 `agent_routes`로 에이전트에 위임.
