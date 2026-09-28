# Phase 4 PR-B: 고정 입력과 시간 경계

[전체 설계](phase4-design.md)와 [PR-A 계약](backtesting-contracts.md)을 재사용한다. 이번 구현은 소규모 메모리 tape·명시적 cursor·정보 공개 gate다. runner, 시뮬레이션 DB, 전략 실행, 주문·체결·원장·CLI는 없다. P1/P2/P3/P9/P10을 승인하거나 거래 정책 기본값을 정하지 않는다.

## 실제 API 조사와 지원 경계

| 기존 경계 | 확인한 사실 | PR-B 처리 |
| --- | --- | --- |
| `MarketDataStore.read` / `SQLiteStore.read` | 종목·거래일별 최신 정제 DailyBar만 반환. 원천 revision archive/시점별 전체 종목군/원본 HTTP body 조회 계약 없음 | historical에서 read로 복사·검증한다. 이 경로의 PIT는 읽기 전 `pit_history_unavailable`. raw 테이블이나 비공개 메서드를 조회하지 않음 |
| `AnalysisStore.get`, `created_at`, `load_model` | 정확한 content ID와 원본 artifact 생성 시각을 조회 가능. 불변 모델 선택·공개 이력과 전체 revision 증거는 없음 | 정확한 ID의 payload·생성 시각·의존 dataset/training snapshot을 한 번 보존. PIT 시간 검사 이후에도 선택/revision 증거 부족이면 blocked |
| `SignalService.research` | 모델과 분석 snapshot 동일성 필수. anchor는 연구용 16시; 실제 생성 시각과 다름 | 연구 artifact를 읽기만 하며 같은 snapshot을 강제. 재추론·다른 snapshot 결합 없음 |
| PIT inference | 모델 실제 생성 시각 검사, A1 전일 이하 일봉. `SignalQueryService`의 anchor cutoff만으로 artifact 공개 시각을 입증하지 못함 | latest 조회를 사용하지 않음. 이미 생성된 artifact의 시각을 구별. 당일 일봉 정책 변경 없음 |
| `strategy.adapter.ReadOnlyAnalysisStore` | 공개된 읽기 전용 AnalysisStore 구현 | caller가 이 reader를 전달 가능. 기존 어댑터 및 Phase 3 판단 로직은 수정하지 않음 |
| PR-A `MarketEvent` | 사건별 available_at과 공개 필드는 있으나 필드별 공개 증거 없음. revision은 opaque 문자열 | PR-A 변경 없이 별도 불변 release plan을 추가. 사건 계약을 재설계하지 않음 |

기존 Phase 1~3 및 PR-A 파일은 변경하지 않는다. 설계의 미래 컴포넌트 이름을 현재 구현된 API로 가정하지 않았다. 실제 데이터 PIT가 구현 완료되었다는 주장은 하지 않는다.

## 불변 입력과 공개 시각 자료

[data.py](../src/donghak_stock_vision/backtest/data.py)의 `FrozenJSON`은 canonical JSON만 보관하고 detached dict를 반환한다. 중복 JSON 키·비유한 JSON 수치를 거부한다. 원천/정책 자료의 hash는 내용 일치 확인이며 외부 사실의 인증 서명이 아니다.

`capture_market(store, tickers, start, end, information_mode=..., data_origin=..., captured_at=..., quality=...)`는 공개 `read`만 호출한다. 날짜·정렬·중복·ticker·수신 시각, 원래 DailyBar 유효성, 출처, provider/market/adjustment 일관성, 품질 coverage/sessions/excluded dates, 조정 방법의 존재를 검사한다. 품질 확인 시각이 capture 시각 이후면 거부한다. quality 원본도 별도로 고정해야 한다. 자료가 없으면 가격을 만들지 않는다.

캡처 산출물의 `kind=latest_daily`, `original`은 실제 DailyBar dict다. `revision=digest(original)`은 **정제 행의 내용 식별자**이며 KRX 공식 revision이나 원본 HTTP hash라고 주장하지 않는다. 공개 API가 제공하지 않는 원본 body 증거는 만들지 않는다. `source_received_at`은 원래 collected_at의 UTC 동치이며 소급 변경하지 않는다. 캡처의 실제 실행 시각을 identity에 자동 삽입하지 않는다.

`FrozenTape(manifest, policy, events, sources, release_plan, quality)`는 다음을 검증한다.

- PR-A `validate_run`: run/policy ID, 세 모드 축의 출처, 기간, sequence 증가 및 UTC available_at 비감소.
- 명시적 source payload hash가 manifest의 market_snapshots와 사건 source에 결합되는지, 공개 필드 값과 수신/수정 상태 등 metadata가 일치하는지 검사한다.
- 전체 품질 payload의 hash를 manifest 및 사건 증거 ID와 대조한다. 단순 verified 플래그만으로 통과하지 않는다.
- 같은 ticker/거래일의 새 revision은 직전 revision을 명시하고 실제 수신 시각이 증가해야 한다. revision 재사용·동일 사건 중복·sequence 중복·역행을 거부한다. 서로 다른 날의 사건 발생 시각이 늦게 도착하는 것은 허용하며 **공개 순서**는 되돌리지 않는다. opaque revision 문자열을 숫자/사전식 순서로 비교하지 않는다.
- `synthetic_event`는 합성 원천·품질 fixture에만 허용한다. real로 재표기할 수 없다. 실데이터 PIT receipt/archive 입력 계약은 이번에 발명하지 않는다.

release plan은 아래 키를 모두 명시한다. 순간은 timezone-aware ISO 문자열이며 UTC로 비교한다. 시각·정책을 자동 생성하는 helper는 제품 코드에 없다.

```text
information_mode, data_origin, usage_restriction
releases: [
  {sequence, previous_revision: null 또는 직전 원천 revision,
   field_times: {공개 필드명: 그 필드의 공개 가능 시각}}
]
analysis_releases: [
  {model_id, snapshot_id, analysis_id, available_at, sequence}
]
```

market releases는 events와 일대일이며 field_times는 각 사건의 public_fields와 정확히 일치해야 한다. 필드 시각은 사건 available_at보다 이를 수 없다. historical에서는 plan hash가 manifest.availability_assumption_id와 같아야 한다. tape는 모든 모드에서 policy.availability_policy.content_hash에도 결합한다. 사용자가 제공한 시간 가정은 기능 입력이며 P2/P3의 운영/체결 정책 승인을 대체하지 않는다. 자료 전체가 없으면 부분적으로 추정하지 않고 거부한다.

시장 gate는 어떤 시각을 실제 시가·장 마감·배포 시각으로 선택하지 않는다. 특히 일봉 전체 OHLCV를 시가 시점에 공개하는 기본 가정은 없다. source/quality·plan의 실체를 확인할 수 없는 경우 real PIT를 허용하지 않는다. historical 가정으로 과거 공개 시간을 지정해도 원본 receipt 및 품질 verified_at은 별도로 그대로 남는다.

## Clock과 공개 view

[clock.py](../src/donghak_stock_vision/backtest/clock.py)의 `VirtualClock.at(cutoff, sequence)`는 UTC cursor를 명시적으로 만든다. `advance`는 새 객체를 반환하며 sequence 엄격 증가·시각 비감소를 요구한다. 동일 시각의 다른 sequence는 허용하며 같은 사건 sequence 재처리, 시각 역행은 거부한다. 숫자를 자동 발급하거나 시스템 now를 읽지 않는다. `trading_date`는 같은 순간의 Asia/Seoul 날짜다. stateless `at`은 재생/조회용이며 runner/checkpoint를 대신하지 않는다.

`tape.view(clock)`는 사건 sequence≤cursor.sequence 및 available_at≤cutoff인 사건 중 field_times≤cutoff인 필드만 반환한다. 미래 field가 있다는 사실로 현재 값의 노출을 앞당기지 않는다. 값이 하나도 공개되지 않은 사건은 items에 넣지 않는다. 수정 사건은 공개 후 별도 observation으로 추가하며 이전 사건을 덮어쓰지 않는다. 전략이 필요로 하는 최신값 합성·feature 계산은 여기서 수행하지 않는다.

모든 view는 execution_mode/information_mode/data_origin/usage_restriction과 `scope=research`, `operational_eligible=false`, `executable=false`를 유지한다. 시장 상태는 `not_execution_evidence`로 표시한다. volume=0 일봉·조정 가격·품질 문서만으로 tradable=true, session=open 또는 체결 가능성을 만들지 않는다. 합성 PIT gate의 시간 검증 성공은 실제 시장 PIT 인증이 아니다.

`PublicView.to_dict()`/`identifier`는 전략에 전달할 내용이다. `lineage`는 별도의 감사 메타데이터로 run/tape/event/revision ID를 보존한다. runner가 생기면 strategy에는 **to_dict만** 전달해야 한다. 이 구분은 API 경계이며 프로세스 보안 sandbox가 아니다.

전체 tape hash에는 manifest·정책·모든 사건·원천·공개 가정·품질이 들어간다. 반면 공개 view hash에는 전체 tape/run/event/source hash 및 opaque revision을 넣지 않는다. 그것들이 아직 숨겨진 가격·라벨까지 transitively hash할 수 있기 때문이다. 공개 내용·cutoff·sequence가 같으면 미래 OHLCV/라벨/정정 추가로 전체 tape·manifest가 바뀌어도 공개 view hash는 같다. lineage hash/내용은 바뀔 수 있고 이를 경제적 view 동일성과 혼동하지 않는다.

원천/사건/정책은 불변 사본이며 `FrozenTape.to_json()/from_json()`으로 전체 입력을 보존·재검증할 수 있다. 원본 DB 변경/삭제 이후에도 이 사본만으로 재생한다. 새 영구 저장소를 구현한 것은 아니다. 완전한 JSON을 보존하지 않고 hash만 남기면 재생 가능하다고 주장하지 않는다.

## Phase 2 artifact 가용성

[availability.py](../src/donghak_stock_vision/backtest/availability.py)의 `FrozenAnalysis.capture`는 명시 model_id/snapshot_id/analysis_id만 받는다. `load_model`, `get`, `created_at`만 사용하여 모델·학습 snapshot·dataset·추론 snapshot·signal·최초 생성 시각을 고정한다. 저장·재학습·비공개 `_infer`·직접 SQL·registry 접근은 없다.

내용 hash와 manifest의 ID를 대조하고 signal→model/snapshot, model→dataset/training snapshot, split hash, provenance, event contract, 모델 원본 생성 시각 및 snapshot cutoff를 검사한다. historical은 기존 동일 snapshot 규칙을 강제한다. capture는 payload를 보존하며 분석 anchor를 생성 시각으로 덮어쓰지 않는다. 이후 view/replay는 DB를 읽지 않는다.

- historical: 명시 plan의 해당 ID release 시각/sequence 및 anchor 이후에만 연구 signal projection을 공개한다. 사후 학습·수집·품질·생존편향 한계를 지닌 탐색적 연구이며 clean OOS가 아니다.
- PIT: model.created_at≤anchor(특징 cutoff), signal.created_at≤step cutoff 및 의존 artifact 시각과 미래 label_available_at을 확인한다. anchor는 분석 요청 기준이고 artifact 완료/공개 시각과 별개다.
- 현재 Phase 2 공개 API에는 검증된 모델 선택/공개 이력과 revision archive 증거가 없으므로 위 검사에 통과해도 `pit_selection_and_revision_evidence_unavailable`로 **항상 blocked**다. 다른 시간 위반이 먼저 발견되면 구체 사유를 반환한다. 승인 플래그나 SQL로 해제할 경로가 없다.
- blocked view에는 점수/라벨을 넣지 않으며 information_mode를 historical로 바꾸지 않는다. synthetic_test_only는 차단 진단에도 전파한다. model/snapshot/dataset 및 미래 labels는 lineage/보존 bundle에만 있고 전략 projection에는 없다.

생성 시각을 입증하지 못하는 artifact는 `capture` 오류로 거부한다. 원본 생성 시각이 없는 fixture에 현재 시각을 채워 넣지 않는다. 원본 payload 체크섬은 관리자에 의한 created_at 조작이나 외부 증거 진실성을 인증하지 못한다.

## 미지원 및 후속 승인

- 실데이터 PIT tape: 공개 revision archive, 시점별 수신·필드 공개·품질·기업행사·종목군 증거 계약 필요(P3). latest-only DB에서 복원 불가.
- PIT 분석 공개: 모델 선택 및 모든 학습/검증/test gating 의존 시점의 공개 증거 계약 필요(P9). timestamp 검사만으로 이를 승인하지 않음.
- 독립 walk-forward/frozen inference: Phase 2 동일 snapshot 계약 확장은 별도 승인 작업. 본 단계는 기존 모델/분석만 읽음.
- 실제 시각 가정·필드 공개 schedule·동일 timestamp 순서·세션의 실무 정책은 P2/P3에서 승인 필요. clock은 제공된 순서를 검증할 뿐 결정하지 않음.
- P1/P10은 여전히 pending. 별도 SimulationStore, atomic step, 주문/체결 및 성과 계산을 추가하지 않음.

[clock 테스트](../tests/backtest/test_clock.py), [tape 테스트](../tests/backtest/test_data.py), [artifact 테스트](../tests/backtest/test_availability.py)는 합성 기능 검증이다. 실제 KRX 호출이나 시장 성능 주장의 근거로 사용하지 않는다.
