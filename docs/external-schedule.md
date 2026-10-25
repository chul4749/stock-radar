# 외부 예약으로 제시간에 수집하기

GitHub Actions의 예약 실행(schedule)은 몇 시간씩 늦거나 빠질 때가 있다.
외부 무료 예약 서비스(cron-job.org)가 정해진 시각에 GitHub에 "지금 실행해" 요청을 보내게 하면 제시간에 돈다.
GitHub 예약도 그대로 두고, 둘 다 `IF_MISSING`이라 먼저 도착한 쪽만 수집하고 나머지는 건너뛴다.

## 1. GitHub 토큰 만들기 (이 저장소 Actions 실행 권한만)

1. GitHub → 오른쪽 위 프로필 → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token
2. Token name: `stock-radar-cron`, Expiration: 1년
3. Repository access: **Only select repositories** → `chul4749/stock-radar`
4. Permissions → Repository permissions → **Actions: Read and write** (나머지는 건드리지 않음)
5. 만들어진 `github_pat_...` 값을 복사 (다시 볼 수 없음)

## 2. cron-job.org 작업 3개 만들기

cron-job.org 가입 → Create cronjob. 세 작업 모두 공통:

- Advanced → Request method: **POST**
- Headers:
  - `Authorization`: `Bearer github_pat_...` (1번에서 복사한 값)
  - `Accept`: `application/vnd.github+json`
  - `X-GitHub-Api-Version`: `2022-11-28`
- Time zone: **Asia/Seoul**, 요일: 월~금

| 작업 | URL | Request body | 시각 |
|---|---|---|---|
| 저녁 수집 | `https://api.github.com/repos/chul4749/stock-radar/actions/workflows/collect.yml/dispatches` | `{"ref":"main","inputs":{"if_missing":"1"}}` | 18:20 |
| 저녁 수집 예비 | (위와 같음) | (위와 같음) | 20:30 |
| 아침 요약 | `https://api.github.com/repos/chul4749/stock-radar/actions/workflows/morning.yml/dispatches` | `{"ref":"main"}` | 07:40 |

성공하면 응답 코드가 **204**다. 401/403이면 토큰 값이나 권한(Actions: Read and write)을 다시 확인한다.

## 참고

- 토큰은 이 저장소의 Actions 실행만 할 수 있어서, 새어 나가도 코드를 바꾸거나 읽을 수는 없다. 그래도 의심되면 GitHub에서 바로 삭제(Revoke)한다.
- 아침 요약은 저녁 수집과 같은 줄(concurrency `collect`)에 서서 서로 push가 엇갈리지 않는다.
