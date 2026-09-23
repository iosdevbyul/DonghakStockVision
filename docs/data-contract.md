# 일봉 데이터 계약 v1

## 스키마

| 필드 | 타입 / 단위 | 규칙 |
| --- | --- | --- |
| ticker | 문자열 | KRX 6자리 영문 대문자/숫자 단축코드, 선행 0 보존 |
| trading_date | date / ISO YYYY-MM-DD | Asia/Seoul의 거래일, 타임스탬프 아님 |
| open, high, low, close | int / KRW | 수정 상태와 함께 해석, 부동소수점 금지 |
| volume | int / 주 | 음수·소수·bool 금지 |
| trading_value | int / KRW | 제공업체의 실제 거래대금, close×volume으로 추정 금지 |
| provider | 비어 있지 않은 문자열 | 현재 krx 또는 fake |
| market | 문자열 | KOSPI, KOSDAQ, KONEX |
| adjustment | 문자열 enum | unadjusted, adjusted, unknown |
| collected_at | timezone-aware datetime | 응답 수신 시각, 저장/JSON 출력은 UTC offset 명시 |

가격·거래량·거래대금은 모두 signed 64-bit 비음수 정수 범위입니다. None/NaN/Infinity/빈 문자열/대시를 0으로 바꾸지 않습니다. 데이터 정규화 시점과 원본 수신 시점은 다르며 `collected_at`은 후자를 뜻합니다. KRX `BAS_DD`는 서울 날짜로 읽습니다. 로그는 UTC `Z`, 요청 일일 한도는 서울 날짜로 관리합니다. Fake의 수집 시각은 고정 fixture 메타데이터이고 raw envelope에는 실행 시각을 기록합니다.

이 계약은 역사적 최종 일봉만 받습니다. `trading_date`는 수신 당시 서울 날짜보다 이전이어야 하며 CLI 역시 당일/미래를 거부합니다. 토·일 봉은 오류입니다. 공휴일 캘린더 검증은 아직 없으므로 평일이라는 이유만으로 거래일을 보장하지 않습니다. KRX 배포 지연으로 전일 데이터가 아직 없을 수 있습니다.

## 값과 품질 검증

일반 봉은 `0 < low <= open <= high` 및 `low <= close <= high`, `close > 0`이어야 합니다. 거래량과 거래대금의 0 여부가 일치해야 합니다. 거래 없는 날에 원천이 **open=high=low=0, close>0, volume=trading_value=0**을 명시한 경우 그대로 허용합니다. 모든 가격이 양수인 무거래 봉도 OHLC 관계를 충족하면 허용합니다. 무거래만으로 거래정지를 단정하지 않습니다.

전 거래 관측치 대비 종가 비율이 1.35 초과 또는 0.65 미만이면 `price_jump` 경고를 남기지만 임의 삭제하지 않습니다. 액면분할·합병·신규 상장·긴 공백 등 정상 원인일 수 있기 때문입니다. 이 임계값은 거래소 상하한가 규정의 구현이 아니라 조사용 휴리스틱입니다. 거래대금은 장외/시간외 포함 범위 차이 때문에 `low×volume..high×volume` 강제 범위 검증을 하지 않습니다.

응답 내 ticker/기간/시장/제공업체 일치 여부를 검사합니다. 날짜를 오름차순으로 정렬하고 같은 날짜의 동일값 중복은 한 행으로 줄입니다. 수집 시각만 다른 중복은 동일값으로 봅니다. 값이 다른 중복은 해당 종목의 전체 배치를 거부합니다. 잘못된 행만 조용히 버리는 정책을 사용하지 않습니다.

## 원본·정제·감사 이력

- `raw_pages`: 응답 bytes(BLOB), SHA-256, 제공업체, 요청 종목, 응답 기준일, 수신 시각, run_id. 정제 전에 append-only 보관합니다. HTTP 비정상 상태 응답 본문은 보관하지 않으며 재시도/실패 기록을 남깁니다.
- `bars`: `(ticker,trading_date)` 유일 기본키와 JSON 계약 payload. 정제 결과이며 실제 수치 또는 명시된 fixture만 저장합니다.
- `bar_sources`: 정제행에서 원본 응답으로 연결. 정정 이력의 과거 원본 참조도 보존합니다.
- `collection_runs`: 종목·요청 범위·완료 UTC 시각·success/empty/failed·행 수·안전한 오류 분류. DB를 직접 조회해 실패 범위를 조사할 수 있습니다.

같은 값 재수집은 정제행을 변경하지 않습니다. 달라진 값은 더 나중의 `collected_at`인 동일 제공업체/시장/수정 상태일 때만 정정합니다. 오래되거나 동시각인 충돌 응답은 거부합니다. 새 값에 대한 원본을 보관하며 오류 중 기존 정상 데이터는 삭제하지 않습니다. Fake와 실제 데이터는 별도 DB를 권장하고 같은 종목 시계열에서 출처/수정 상태 혼합은 거부합니다.

정제 데이터 무결성 검사는 SQLite 구조·FK, 각 행의 모델 검증, PK와 payload 일치, 시계열 메타데이터, 원본 체크섬, raw 참조 존재를 확인합니다. 행이 하나도 없는 DB는 구조상 유효할 수 있습니다. 존재하지 않는 DB는 오류입니다. API 오류가 없는 빈 응답도 완전한 수집으로 단정하지 않습니다.

## 결측·거래정지·종목 생애주기

데이터가 없으면 행을 생성하지 않으며 forward-fill/back-fill/보간하지 않습니다. 빈 응답은 휴장·상장 전·상장폐지 후·종목 오입력·API 배포 지연 중 어느 것인지 구분되지 않을 수 있어 `empty`로 기록합니다. 거래일 마스터 없이는 빠진 거래일을 확정하지 않습니다.

거래정지 기간은 원천이 제공한 무거래 행만 보존합니다. 제공하지 않은 날짜를 생성하지 않습니다. 신규 상장 이전과 상장폐지 이후의 가격은 생성하지 않습니다. 과거 상장폐지 종목의 보유 데이터는 삭제하지 않습니다. 종목 목록을 오늘 기준으로 필터링하지 않으며 생존편향 없는 전체 종목집합 구축은 추후 별도 작업입니다. 시장 이전은 동일 시계열에 무단 혼합하지 않고 별도 DB로 수집하여 추후 종목 마스터와 함께 조정합니다.

## 수정주가

KRX 일별매매정보의 원천 일봉은 어댑터에서 `unadjusted`로 표기하고 어떤 기업행사 보정도 수행하지 않습니다. 수정계수·조정 기준일은 이 API 계약에 없으므로 임의의 adjusted close를 만들지 않습니다. 향후 수정주가 제공업체를 도입하면 기준일·기업행사 방법론·버전과 전체 역사 재수집을 설계해야 합니다. 이번 Phase에서 unknown 또는 adjusted라는 enum 값이 있다는 사실이 해당 데이터를 지원하거나 동등 비교할 수 있음을 뜻하지 않습니다.

증분 재조회 기본 7일은 최근 정정/지연 대응용입니다. 더 오래된 수정, 상장 이벤트, 미수집 구간은 명시적 전체/범위 재수집이 필요합니다. 관측 가격 변화에서 신호·Target을 생성하지 않습니다.

## Phase 2 분석 계약

일봉 v1 필드·정수 단위·수신 시각과 Phase 1 CLI는 그대로 유지합니다. 전체 상세 정책은 [승인 설계 v2](phase2-design.md)를 따릅니다. 분석에서는 0거래·알려진 기업행사·의심 불연속(abs 로그수익률>0.20)·미확인 7달력일 초과 공백을 구간 경계로 삼습니다. 조사 기준이지 기업행사의 완전한 탐지가 아닙니다. 잘못된 행을 음성 라벨로 만들지 않고 필요한 lookback/H 구간을 제외합니다. 수정주가/비수정주가/공급자가 섞인 학습을 거부하며 수정주가는 확인된 방법까지 같은 가격 기준을 요구합니다.

Historical Research의 `snapshot_as_of`는 실제 데이터 cutoff이고 `anchor_at`은 가상의 서울 16:00입니다. `snapshot_id`는 원천 OHLCV/거래대금·수신 시각·품질 자료·범위를 담은 불변 JSON 내용 hash입니다. 당시 존재한 정보라는 주장은 하지 않습니다. PIT는 수신 시각과 가용 모델을 검사하며 현재 DB에서 사라진 과거 revision을 임의 복원하지 않습니다. 최종 H개 일봉이 있어도 표본 생성 이후 수신된 특징 revision은 당시 특징으로 사용하지 않습니다. 과거 정보 cutoff 뒤 수정본만 남은 경우 누락 날짜로 보존하여 해당 구간을 차단합니다.

모든 분석 결과는 `mode`, `usage_restriction`, `model_version`, `model_created_at`, `feature_version`, `label_version`, `event_description`, `session_basis`, `snapshot_id`, `snapshot_as_of`, `data_as_of`, `anchor_at`, `analyzed_at`, 마지막 거래일·수신 시각·입력 hash·품질 flags·문맥·방향별 점수/상태·검증 상태·운영 상태·기여도를 반환합니다. 연구의 `data_as_of`는 null입니다. 같은 내용의 재실행은 같은 analysis_id를 사용하되 실행 감사 시각은 추가됩니다. 조회의 analyzed_at은 해당 불변 결과의 최초 저장 시각입니다.

점수는 미보정이며 상승 확률이라고 부르지 않습니다. r5<0에서는 down_score=null, r5>0에서는 up_score=null, r5=0에서는 둘 다 null입니다. 미래 H는 라벨 관측 구간이며 보유 기간이 아닙니다. 같은 봉에서 양 장벽 최초 도달은 ambiguous로 제외합니다. 검증된 캘린더가 없으면 H는 5개 관측 일봉이고 결과에 `observed_bars_unverified`와 한계 설명을 포함합니다.

## Phase 2 품질 manifest

`--quality-manifest PATH`의 JSON에는 다음 필드가 필요합니다. 이 예시는 **형식 설명 전용**이며 실제 검증 증거가 아닙니다. 실제로 확인한 거래소 세션·기업행사·가격 수정 기준을 기입해야 합니다. 시세에서 자동으로 "기업행사 없음"을 추정하지 않습니다.

```json
{
  "schema_version": 1,
  "source": "확인한 자료의 식별자/출처 및 검토 근거",
  "verified_at": "2026-09-23T09:00:00+09:00",
  "sessions": ["2024-01-02", "2024-01-03", "2024-01-04"],
  "coverage": {
    "005930": {
      "start": "2024-01-02",
      "end": "2024-01-04",
      "adjustment": "unadjusted",
      "excluded_dates": []
    }
  }
}
```

`sessions`는 확인한 전체 거래일을 오름차순·중복 없이 기록합니다. coverage의 start/end는 양끝 포함이며 요청 종목과 기간을 포괄해야 완전한 품질 자료로 취급합니다. `excluded_dates`에는 확인된 기업행사·정지 등으로 학습에서 제외할 날짜를 기록합니다. `adjustment=adjusted`이면 비어 있지 않은 `adjustment_method`가 추가로 필요합니다. `unknown`은 차단합니다. verified_at은 snapshot cutoff 이하여야 합니다. 과거 anchor 뒤 확인된 검토는 `quality_review_may_be_retrospective`로 표시하며 그 자체가 당시 운영 가능성 증거는 아닙니다.

PIT는 불완전 manifest를 거부합니다. 연구에서는 불완전/미제공 자료에 명시적 한계 동의를 요구하며 `corporate_actions_unverified`, `calendar_unverified`를 남깁니다. 모든 현재 입력에는 전체 종목 생애주기·revision 복원 한계에 따른 `universe_incomplete`, `revision_history_unavailable`가 남습니다. 최신 종목만 수집한 연구를 시장 전체의 역사적 성능으로 일반화할 수 없습니다.

## 재현성과 데이터 보관

분석 DB와 원본/정제 DB는 모두 Git에 포함하지 않습니다. JSON 모델을 별도 복사할 때 snapshot/dataset/모델의 hash 연결도 함께 보존해야 하며 임의 단독 파라미터 파일 로드는 제공하지 않습니다. 엄격한 재현은 같은 코드와 기록된 의존성 버전에서 수행합니다. 모델 파라미터를 이용한 추론의 수치 허용 오차는 1e-10입니다. 코드·라이브러리 변경은 새 모델 버전을 만들 수 있습니다. 모델 학습·평가·추론은 운영 registry를 변경하지 않습니다.
