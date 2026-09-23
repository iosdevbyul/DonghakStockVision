# Phase 2 승인 설계 v2: 상승·하락 전환 학습 엔진

상태: **수정안 v2 사용자 최종 승인 완료 · 이 계약에 따라 구현**. 작성일: 2026-09-23.

수정 요지: Historical Research/PIT 분리, 문맥별 조건부 모집단, 예측 사건의 범위 고정, 검증 통과와 운영 등록 분리. 라벨·표본량·평가·운영 등록 기준은 승인 후 완화하지 않는다.

## 1. 설계 승인 전 확인한 저장소 상태 (기록)

- 최신 확인 `origin/main`: `eb4470e` (Phase 1 PR #1 병합).
- 원격 `chore/bootstrap-market-data` 삭제 확인.
- 원래 작업 디렉터리에는 사용자가 수정한 `.github/workflows/ci.yml`이 미커밋 상태로 존재한다. `pull_request: main`, `push: main`, `workflow_dispatch`가 `on` 직속에 있어 요청한 구조가 맞다.
- 이번 재확인에서도 원격 main에는 이전 push 트리거가 남아 있고, 열린 PR은 없다. CI 변경은 로컬에만 존재한다. 사용자 파일은 수정·커밋·stash하지 않았다.
- 깨끗한 origin/main에서 `feat/market-signal-learning` 브랜치와 `/private/tmp/dsv-phase2` worktree를 생성했다. 이 초안만 추가한다.
- 이번에는 CI 파일도 변경하지 않고 별도 CI PR도 만들지 않는다. 최종 설계 승인 후 다시 fetch/PR 조회한다. 별도 CI PR이 생기면 그 변경 경로를 우선하며 같은 내용을 복제하지 않는다. 이미 main에 병합됐다면 해당 main을 통합한다. 여전히 로컬 변경뿐이면 Phase 2 PR에 동일한 최소 트리거 변경을 한 번만 포함하는 방안을 기본 계획으로 둔다. 원래 작업 디렉터리의 사용자 수정은 그대로 보존하고, 사용자 CI 커밋을 임의로 만들거나 이전 브랜치를 다시 push하지 않는다.
- Phase 1 인터페이스는 `MarketDataStore.read(ticker,start,end) -> list[DailyBar]`. 원본/CLI 재파싱 없이 이 경계를 사용한다. 현재 `bars`는 최신 값만 유지하며 과거 revision 조회 인터페이스는 없다.

## 2. 승인된 핵심 선택

1. **Historical Research와 Point-in-Time(PIT)을 명시적으로 분리**한다. 전자는 현재 확보한 과거 데이터 스냅샷으로 연구 모델을 학습하고, 후자는 실제 수신 시각까지 준수한다. 모델·평가·추론·조회에 mode를 필수로 기록하며 서로 대체하거나 자동 전환하지 않는다.
2. 상승 모델은 **r5<0인 직전 하락 문맥**, 하락 모델은 **r5>0인 직전 상승 문맥**에서만 조건부 학습·평가한다. 문맥 밖 표본은 음성이 아니라 모집단 밖이다.
3. `reversal_barrier_v1`의 사건은 **5개 미래 세션 내 단기 역방향 장벽 최초 도달**이다. 지속적인 추세 반전·수익·보유 기간을 예측한다고 표현하지 않는다. H=5, 최소 폭 2%, 변동성 배수 2는 검증 전 초기 가설로 고정한다.
4. 공통 날짜순 train/validation/test와 라벨 범위 purge, 별도 상승·하락 선형 모델 및 기준 모델 비교를 유지한다.
5. **validation_qualified는 검증 지표 상태일 뿐 운영 등록이 아니다.** 모든 새 모델의 운영 등록 상태는 unregistered다. 연구 모드는 research_only, 합성 데이터는 언제나 synthetic_test_only이며 운영 등록 불가다. 실제 데이터 품질·최종 테스트·PIT 검증과 명시적 운영 검토가 완료되기 전 최신 운영 신호 목록은 비어 있을 수 있다.

아래 최소 표본량·선택 기준도 사전 정책값이며 실전 유효성이 입증된 값이 아니다. 최종 테스트를 확인한 후 같은 실험의 가설·임계값을 바꾸지 않는다.

## 3. 전환 사건 후보와 선택 근거

| 후보 | 장점 | 한계 | 선택 |
| --- | --- | --- | --- |
| 미래 N일 종가 수익률 부호/임계치 | 단순, 재현 쉬움 | 중간 경로 무시, 추세 지속과 전환 혼동 | 미선택; 기존 20일 Target 재사용 안 함 |
| 미래 이동평균 교차/국소 고점·저점 | 추세 전환에 가까운 정의 | 지연, 평활화 선택 의존, 국소 극값의 미래 확인 범위 모호 | 추후 별도 버전 후보 |
| 상·하 가격 장벽 중 최초 도달 + 과거 추세 조건 | 사건과 관측 종료 명확, 변동성 반영, 경로를 사용 | 일봉 내부 선후관계 모호, 장벽 설정 의존 | **v1 제안** |

장벽 방식의 경로/수직 제한 개념은 [mlfinpy의 라벨링 문서](https://mlfinpy.readthedocs.io/en/latest/Labelling.html)를 참고한다. 본 프로젝트의 추세 조건·가용 시각·일봉 모호성 정책은 별도로 명시하는 설계 선택이다. 기존 레거시 코드를 읽어 Target/모델을 가져오지 않는다.

### 사건 정의: `reversal_barrier_v1`

고정 상수는 **H=5, min_barrier=0.02, volatility_multiplier=2**다. 최종 테스트를 보기 전에 manifest에 기록하고 v1의 CLI 튜닝 인자로 노출하지 않는다. 변경이 필요하면 새 label version과 새 실험을 만들며, 이미 본 최종 테스트를 새 가설의 독립 검증으로 재사용하지 않는다.

특징의 마지막 일봉 날짜를 t, 마지막 5관측 구간 로그수익률을 r5, 최근 10개 1관측 로그수익률의 표본 표준편차를 sigma10이라 한다.

| 모델 | 조건부 모집단 | 양성 1 | 음성 0 | 모집단 밖 |
| --- | --- | --- | --- | --- |
| 상승 | r5<0 | 상단 장벽이 먼저 도달 | 하단 먼저 도달 또는 H 내 미도달 | r5>=0 |
| 하락 | r5>0 | 하단 장벽이 먼저 도달 | 상단 먼저 도달 또는 H 내 미도달 | r5<=0 |

- 모집단 밖에는 해당 방향 label=null, status=context_mismatch를 부여하며 fit·스케일러·가중치·기준 모델·평가 분모에서 제외한다. r5=0이면 두 방향 모두 모집단 밖이다.
- 미래 관측 첫 세션 s는 해당 모드의 anchor_at 서울 날짜보다 엄격히 뒤인 첫 적격 세션이다. anchor 정책과 세션 확인 수준은 아래 모드 계약을 따른다.
- 기준 가격 P는 s의 시가이며 라벨 계산 전용이다. b=max(0.02, 2×sigma10), U=P×(1+b), L=P×(1-b). b>=1, sigma10=0, 비정상 값은 제외한다.
- s부터 H개 세션에서 high>=U 또는 low<=L의 최초 도달을 찾는다. 직전 추세는 t까지의 데이터로만 판단한다.
- 앞선 도달 없이 같은 일봉에서 양 장벽이 모두 충족되면 선후관계를 알 수 없어 해당 조건부 라벨을 ambiguous로 제외한다. 임의의 유리한 순서를 만들지 않는다.
- H가 전부 확보되고 장벽 미도달이면 해당 방향 음성이다. H 미성숙, 거래정지·알려진 행사·의심 불연속·필수 행 누락은 censored/excluded이며 음성으로 바꾸지 않는다.
- 최초 도달이 빨라도 H 전체 유효성·성숙을 요구하고 label_end를 H번째 날짜로 기록한다. purge에도 이 보수적인 종료일을 사용한다.

**출력의 event_description은 “직전 반대 추세 문맥에서 5개 미래 세션 내 단기 역방향 장벽 최초 도달”로 고정한다.** 이는 지속적인 추세 반전, 이후 상승/하락 유지, 실제 체결, 매매 수익을 뜻하지 않는다. 첫 세션의 시가 체결이나 H만큼 보유를 지시하지 않는다. 두 방향 점수는 서로 다른 조건부 모집단을 대상으로 하므로 합이 1이 아니며 공통 확률로 비교하지 않는다.

## 4. 두 분석 모드와 시간 계약

### 공통 입력·시각

가격은 두 모드 모두 `MarketDataStore.read` 경계에서 얻은 DailyBar만 사용한다. trading_date는 서울 거래일, collected_at은 실제 수신 시각으로 보존한다. 모델/학습 실행과 analyzed_at은 실제 UTC 시각이다. mode는 `historical_research` 또는 `point_in_time`이며 생성 후 바꾸지 않는다.

| 항목 | Historical Research | Point-in-Time |
| --- | --- | --- |
| 목적 | 일괄 수집된 과거 스냅샷으로 조건부 사건 연구 | 당시 실제 이용 가능한 자료에 따른 분석 |
| 과거 특징 | 고정 snapshot 안에서 trading_date<=t만 참조 | trading_date와 collected_at cutoff 모두 준수 |
| 과거 수신 지연 | 모사하지 않음, 그 한계를 필수 표시 | 실제 수신 지연을 반영 |
| anchor_at | t의 서울 16:00라는 연구용 EOD 표식 | 실제 분석 가능 수신 시각 |
| 첫 라벨 세션 | t 다음 적격 세션 | 실제 anchor_at 서울 날짜 다음 적격 세션 |
| revision 위험 | 사후 정정이 섞일 수 있음, PIT 주장 금지 | cutoff 이후 revision 제외, 복원 불가 시 차단 |
| 품질 미확인 | 명시적 동의 후 제한된 연구 가능 | 검증 자료 미비 시 차단 |
| 운영 등록 | 영구 불가, research_only | 별도 운영 검토 이전에는 unregistered |

EOD 16:00는 시간 정렬용 연구 가정일 뿐 거래소의 정확한 일봉 배포 시각이나 실제 매매 가능 시각이 아니다. 두 모드의 anchor 차이로 같은 거래일도 라벨이 달라질 수 있다. mode와 anchor_policy를 artifact identity에 넣고 모드 간 지표를 같은 실험처럼 합치지 않는다.

### Historical Research: 실행 가능한 과거 연구 경로

1. `train --mode historical-research --snapshot-as-of ...`로 실제 수신된 과거 일봉을 읽는다. snapshot_as_of는 실제 데이터 확보 cutoff이며 모든 포함 행의 collected_at<=snapshot_as_of를 요구한다. 이 timestamp를 과거 가상 분석 시각과 혼동하지 않는다.
2. 입력 값·메타데이터·hash를 불변 snapshot으로 보관한다. t의 feature는 그 snapshot 중 t까지의 가격만으로 계산한다. t 이후 가격·label 값은 feature에 접근할 수 없다. collected_at을 수정하거나 일괄 수집 시각을 과거로 꾸미지 않는다.
3. nominal anchor_at=t 16:00 Asia/Seoul을 사용한다. 당시 이 데이터가 실제 이용 가능했다는 주장은 하지 않는다. label 구간이 분할 경계를 넘는 표본은 제거하지만, 과거 파티션 경계에 현대의 collected_at을 대입해 모든 연구 표본을 제거하지는 않는다.
4. 품질 manifest가 있으면 알려진 기업행사/세션 검증을 사용한다. 없거나 불완전하면 `--acknowledge-research-limitations`를 명시해야 연구를 허용하며 corporate_actions_unverified, revision_history_unavailable, calendar_unverified, universe_incomplete 등 실제 해당 flag를 남긴다. 동의는 품질 확인이나 운영 승인으로 간주하지 않는다.
5. 알려진 행사/검증 실패/탐지한 불연속/무거래/공백 의심 구간은 동의 여부와 무관하게 제외한다. 달력 미확인 연구에서는 날짜순 관측 일봉을 세션 대용으로 사용하되 연속 관측 간 7달력일 초과는 공백 의심으로 끊는다. 짧은 누락은 검출할 수 없다는 한계를 명시한다.
6. 이 경우 `session_basis=observed_bars_unverified`이고 H는 다음 5개 적격 관측 일봉을 뜻한다. 출력에도 “거래일 달력 미확인: 5개 관측 일봉 기준”을 event_scope_note로 반드시 동반 표시한다. 검증된 거래소 5세션이라는 주장을 하지 않는다. 검증된 달력이 있으면 `verified_sessions`로 기록한다. 서로 다른 session_basis의 결과는 별도 집계하고 한 실험 안에서 혼합하지 않는다.
7. 충분한 실제 과거 표본으로 학습·시간순 연구 평가·연구 추론·연구 결과 조회까지 가능하다. 결과는 research_only이고 역사적 운영 신호로 저장하지 않는다. 적은 합성 fixture로 산출한 점수를 실제 성능으로 설명하지 않는다.

같은 고정 snapshot에서 미래 행을 바꿔도 t의 특징은 불변이어야 한다. 이후 새로운 snapshot에서 t 이하의 수정 값이 달라지면 feature도 달라질 수 있으며 이것이 이 모드의 명시적 한계다. 원래 평가 snapshot은 덮어쓰지 않는다. 기존 최신-only DB에서 요청 snapshot cutoff보다 뒤의 revision만 남았다면 그 행을 제외하며 이전 값을 복원했다고 주장하지 않는다.

### Point-in-Time: 엄격한 가용성 유지

- `as_of`는 timezone-aware 실제 정보 차단 시각이다. collected_at>as_of 또는 trading_date가 as_of의 서울 날짜 뒤인 행은 사용할 수 없다.
- 학습 후보 anchor_at은 t의 저장 revision 수신 시각으로 잡는다. 필요한 lookback 행이 그때 모두 수신됐어야 한다. 그때 더 최신 거래일이 이미 이용 가능하면 t에 과거 신호를 새로 만들지 않는다.
- 최신 revision이 cutoff 뒤에 들어왔다면 그 행은 제외한다. 과거 revision이 덮어써져 lookback이 불완전해지면 insufficient_point_in_time_data를 반환한다. raw JSON에서 revision을 추측해 복원하지 않는다.
- 일괄 backfill로 과거 시점의 가용성이 확보됐다고 간주하지 않는다. 자료가 부족해도 Historical Research로 자동 fallback하지 않는다.
- t의 종가 직후 신호가 이용 가능하다고 가정하지 않는다. 실제 수신 뒤 첫 거래 가능 시각은 거래소 개장·주문 조건에 달려 있다. 본 Phase는 정확한 최초 매매 시각/체결을 반환하지 않으며 라벨은 anchor_at 다음 날짜의 첫 완전한 적격 세션부터 관측한다.
- live/PIT 추론 시 모델 생성 시각도 as_of 이하여야 한다. 뒤늦게 만든 모델의 과거 holdout 예측은 평가 기록이지 당시 존재한 운영 신호가 아니다.
- 저장된 결과·학습 스냅샷은 불변이다. cutoff 이후 값 변경으로 과거 특징값을 바꾸지 않고, 복원이 불가능하면 사용불가 상태로 바꾼다.

### 모드 간 격리

모델·평가·분석 레코드에는 mode, data_origin(real/synthetic), usage_restriction, anchor_policy, session_basis를 필수 기록하고 버전 hash와 조회 키에 포함한다. 연구 모델을 PIT infer에 전달하거나 연구 결과를 운영 registry에 등록하면 mode_mismatch/registration_forbidden으로 실패한다. 단순 flag 변경으로 승격하지 않는다. 운영용 모델은 PIT 데이터로 별도 학습·검증한 새 산출물이어야 한다.

연구 조회와 운영 조회는 별도 서비스 메서드/CLI scope로 나누며 운영 조회가 비었다고 연구 데이터를 대신 반환하지 않는다. 미래 정보 누출 방지는 두 모드 모두 feature/label 분리와 시간순 purge에 적용되지만, Historical Research가 과거 revision·수신 가능성까지 입증한다고 표현하지 않는다.

## 5. 데이터 품질과 투자 가능 종목군

`MarketDataStore.read`로 읽은 DailyBar에 Phase 1 검증을 적용한 후 Phase 2 학습 적합성 검증을 추가한다. Phase 1이 허용한 무거래 봉을 삭제하거나 수정하지 않고 학습 입력에서만 제외한다.

- provider/market/adjustment가 일관된 시계열만 허용한다. `unknown` 수정 상태는 기본 차단한다. 서로 다른 가격 기준을 한 모델에 섞지 않는다.
- 기업행사 조정 기준/버전이 없는 `adjusted` 자료도 그 값만 믿지 않는다. 검증된 시점별 조정 메타데이터가 없으면 차단한다.
- 현재 KRX `unadjusted` 입력에는 기업행사 마스터가 없다. 외부에서 확인한 **품질 manifest**로 사용 가능한 종목·기간, 확인 출처, 확인 시각, 제외 사건일, 확인된 세션 목록을 명시하도록 제안한다. CLI에 `--quality-manifest`로 전달하는 작은 JSON 계약이다. 이는 원본 가격 우회 입력이 아니며 가격은 반드시 Store에서 읽는다.
- PIT에서 manifest가 없거나 포괄하지 않는 구간은 `quality_unverified`로 차단한다. Historical Research에서는 4절의 명시적 제한 동의와 flags 아래 그 불확실성을 연구에 한정하여 허용한다. 어느 모드에서도 “기업행사 없음”을 OHLC만으로 확정하거나 manifest를 자동 생성해 검증된 것으로 표시하지 않는다. 배당락처럼 작은 불연속은 탐지되지 않을 수 있고 임의 보정하지 않는다.
- 추가 방어로 절대 로그 종가수익률 >0.20, 0가격 OHLC, 0거래량/대금, 중복/역순/비정상 수치는 해당 연속 구간을 끊는다. 0.20은 행사 판정이 아닌 이상치 감시값이며 임의 가격 수정에 사용하지 않는다.
- 특징 lookback이 사건일/불확실 공백을 가로지르면 제외하고 이후 필요한 정상 관측 개수를 다시 쌓는다. 라벨 구간이 가로지르면 라벨 미확정으로 제외한다.
- 검증된 세션 목록에서 필요한 행이 없으면 결측이다. 단순 주중일을 거래일로 간주하지 않고 알려진 정지를 건너뛰지 않는다. 달력이 미확인인 Historical Research만 관측 일봉 기준을 허용하며, 미탐지 결측/정지로 H가 실제 5세션보다 길어질 수 있음을 session_basis와 한계 보고에 표시한다.
- 신규 상장 후 warm-up 미달은 데이터 부족. 상장폐지 뒤 부족한 미래 구간은 검열(censoring)로 기록하고 0 라벨로 만들지 않는다.
- 전체 상장/폐지 이력·투자 가능 종목군을 복원할 수 없으므로 성능은 명시된 입력 종목과 검증 구간의 적격 표본에 한정된다. 생존편향·사후 제외 편향·변동성 큰 사건 제외에 따른 선택 편향을 보고한다. 시장 전체 추천 성능으로 확대하지 않는다.
- manifest도 source/as_of/hash를 보존하며 표본 시점 뒤에 확인한 범위를 사용할 경우 사후 품질 필터임을 평가 보고서에 명시한다. 가격의 가용 시각 준수와 품질 검토의 사후성은 별개다.
- `provider=fake` 및 합성 fixture는 모드와 지표에 관계없이 항상 `synthetic_test_only`. 명시적 테스트 설정에서만 학습하고 실제 데이터와 혼합하지 않으며 운영 등록은 영구 차단한다.

## 6. 최소 특징 집합: `ohlcv_value_v1`

C,V,A는 각각 종가·거래량·실제 거래대금이다. 모든 특징은 t까지의 자료만 사용하고 스케일러는 학습 파티션에서만 fit한다. 특징 순서를 모델 manifest에 고정한다.

| 특징 | 정의 | 필요한 유효 일봉 수 |
| --- | --- | --- |
| return_1 | log(Ct/Ct-1) | 2 |
| return_3 | log(Ct/Ct-3) | 4 |
| return_5 | log(Ct/Ct-5) | 6 |
| close_to_sma10 | Ct/mean(Ct-9..Ct)-1 | 10 |
| volatility_10 | 최근 10개 1관측 로그수익률의 표본 표준편차(ddof=1) | 11 |
| range_to_close | (Ht-Lt)/Ct | 1 |
| volume_change_10 | log(Vt/mean(Vt-10..Vt-1)) | 11 |
| trading_value_change_10 | log(At/mean(At-10..At-1)) | 11 |

전체 벡터에는 선택한 session_basis에서 최소 **11개 연속 적격 일봉**이 필요하다. PIT는 검증된 세션을 요구하며 연구 모드의 관측 일봉 연속성은 실제 거래소 세션 연속성을 보장하지 않는다. 0 분모·NaN·Inf·미확인 공백은 불가 상태로 반환하며 imputation/forward-fill하지 않는다. 대금은 close×volume으로 대체하지 않는다. 부동소수점은 특징/모델에서만 사용하고 원천 정수 데이터는 보존한다.

시가·OHLC는 라벨/품질에도 활용하지만 라벨 구간의 시가·고가·저가를 특징에 추가하지 않는다. 특징 후보를 테스트 성능을 보고 늘리지 않는다.

## 7. 학습 방식·최소량·기준 모델

### 통합 학습을 먼저 선택

종목별 학습은 개별 유동성/행동에 맞출 수 있지만 전환 사건 수가 적고 종목별 튜닝·검증 실패가 늘어난다. 통합 모델은 사례 수를 확보하고 신규 적격 종목에 적용하기 쉽지만 시장 공통 요인·종목 상관·종목별 차이가 남는다. 초기에는 스케일 없는 위 특징을 사용한 통합 모델을 택하고 ticker 자체를 특징으로 넣지 않는다. 종목별 평가·포함 종목 목록·날짜별 표본 수도 보고한다. 개별 종목 모델은 이번 범위에서 제외한다.

상승/하락의 문맥 필터를 적용한 각각의 train 모집단에서 스케일러·클래스 빈도·가중치를 독립 계산한다. 같은 날짜 해당 문맥 종목 수의 역수와 종목 내 겹치는 label 구간의 평균 uniqueness를 곱해 표본 가중치를 정규화한다. 날짜별 특정 시장 국면이 종목 수만큼 독립 증거처럼 증폭되는 것을 줄이는 조치이며 독립성 보장은 아니다. 클래스 가중치는 train에 대해서만 balanced 방식으로 계산한다. 검증/테스트도 각 방향의 해당 문맥만 평가하되 그 조건부 모집단 안에서는 리샘플링하지 않는다. r5=0 및 문맥 밖 개수는 제외 통계로 따로 보고한다. 조건부 지표를 전체 종목·전체 날짜의 무조건부 성능으로 표현하지 않는다.

### 최소 요구량: 초기 안전 기준

- 전체: 적격 종목 5개 이상, 문맥 필터 전 공통 표본 날짜 504개 이상. 두 방향에 같은 절대 split 경계를 먼저 고정한다.
- **방향별 문맥 필터와 purge 후** train: 각 방향 1,000행·252개 날짜 이상, 양성 50개·음성 50개 이상.
- validation와 test 각각의 **방향별 조건부 모집단**: 200행·63개 날짜 이상, 양성 20개·음성 20개 이상.
- 미충족 방향은 필요한 값/실제 값/제외 사유별 수와 insufficient_data를 반환한다. 다른 방향이 충분하면 그 방향 연구 산출물만 보존할 수 있으나 부족한 모델을 음성 상수로 대체하지 않는다. 기존 모델은 덮어쓰지 않는다. 이 최소량은 두 모드에 적용하며 연구 가능성과 운영 적격성은 별개다.
- CI에서는 별도의 synthetic 설정으로 작은 fixture를 허용하되 그 산출물은 `synthetic_test_only`로 강제한다. 실제 학습의 최소량을 몰래 낮추지 않는다.

이 기준은 통계적 유의성·실전 충분성을 보장하지 않는다. 전환이 드물면 504개 날짜보다 훨씬 더 많은 자료가 필요할 수 있다.

### 초기 모델과 의존성

- 모델: `StandardScaler` + `LogisticRegression` (L2, lbfgs, 충분한 max_iter, 수렴 실패는 학습 실패). 상승·하락 각각 학습한다.
- C 후보는 `[0.1, 1.0, 10.0]`만 사전 고정한다. seed=42와 실제 라이브러리 버전을 기록한다. CPU 실행, 재현성 허용오차와 thread 설정을 명시한다.
- 제안 범위: `scikit-learn>=1.9.1,<1.10`, `numpy>=2.3,<3`; 실제 설치 버전과 전이 의존성을 artifact에 기록하고 CI에서 3.12/3.13/3.14 wheel 설치를 검증한다. 최신 공식 배포의 Python 3.14 지원을 확인했으나 이 프로젝트 환경에서의 설치는 승인 후 검증한다.
- 학습은 선택 extra `.[learning]`로 분리하고 Phase 1 CLI는 모델 라이브러리 없이 계속 작동하게 lazy import한다. CI 개발 설치에는 learning extra를 포함한다.
- XGBoost는 초기 8특징·선형 근거 출력에 비해 의존성과 조정 범위를 늘리므로 우선 채택하지 않는다.
- 기준 1: 각 방향의 조건부 train 양성 빈도만 출력하는 상수 점수. 문맥 밖 0 라벨을 빈도 계산에 넣지 않는다.
- 기준 2: 변동성으로 나눈 직전 5관측 역추세 강도를 sigmoid로 변환한 고정 휴리스틱. 상승 `sigmoid(-r5/sigma10)`, 하락 `sigmoid(r5/sigma10)`; 학습·튜닝 없음. 각 모델과 동일한 조건부 validation/test 표본에서 비교한다. 이 값도 확률이 아니다.

## 8. 시간순 검증·선택·평가

1. 선택한 모드의 anchor_at 서울 날짜로 전 종목 공통 날짜축을 만든다. 방향별 문맥 필터 전에 경계를 고정하고 같은 날짜의 모든 종목/두 방향에 동일한 파티션을 적용한다. 모드별 실험은 독립이다.
2. 정렬한 날짜를 처음 60% train, 다음 20% validation, 마지막 20% final test로 나눈다. 실제 절대 경계·표본 목록/hash를 저장한다. 요청 시 명시 날짜 경계를 사용할 수 있으나 실행 후 변경하지 않는다.
3. 두 모드 모두 train label_end가 validation 시작 날짜 이상이면 purge하고 validation에도 test 경계에 동일하게 적용한다. PIT는 추가로 라벨 자료의 실제 수신 시각이 다음 파티션 시작 cutoff 이상인 표본을 purge한다. Historical Research는 snapshot cutoff 내 자료만 허용하되 과거 nominal 경계에 현재 수신 시각을 적용하지 않는다. 이 차이는 retrospective_revisions_possible로 보고한다. 경계 라벨을 음성으로 바꾸지 않는다.
4. 최대 H를 행 개수로만 빼는 방식은 종목별 결측에 틀릴 수 있어 실제 날짜·수신 시각으로 비교한다. forward-only 분할이므로 미래 train 파티션은 없으며 임의의 양방향 K-fold/랜덤 분할은 하지 않는다. 과거 특징 lookback의 train 구간 참조는 허용하되 PIT는 실제 가용성을, 연구는 snapshot 안의 과거 날짜만 사용했는지를 각각 검증한다.
5. 방향별 문맥 내 전처리·클래스 가중치·학습 관련 가중치는 그 방향 train에서만 계산한다. holdout 데이터를 학습 fit에 전달하지 않는 테스트를 둔다.
6. 두 모델은 validation AP가 가장 높은 C를 각각 선택한다. 동점은 더 작은 C. 신호 판정 임계값 0.5는 v1에서 고정하며 테스트로 튜닝하지 않는다.
7. 각 조건부 validation 모집단에서 AP가 두 기준 모델 AP 최댓값보다 최소 0.02 높고 평가/수렴/표본 요건을 충족하면 evaluation_status=validation_qualified로 기록한다. 0.02는 실험 정책값이며 유의성 검정이나 운영 허가가 아니다. 미달은 baseline_not_beaten이다. 어느 결과도 operational_status를 자동 변경하지 않는다.
8. 선택을 확정한 뒤 **동일한 train-fit 모델**을 final test에서 평가한다. test를 포함해 재학습하거나 test 성능으로 C/특징/라벨/threshold를 다시 고르지 않는다. 테스트가 나빠도 다른 후보를 다시 고르지 않고 결과를 공개한다. final test 성능만으로 자동 운영 적격 판정을 내리지 않는다.
9. 데이터 스냅샷·분할 hash와 결과를 저장한다. 같은 평가를 재조회할 때 새 데이터로 테스트를 재정의하지 않는다. 재실험은 새 run으로 기록하며 반복 실험에 따른 holdout 오염을 경고한다.

scikit-learn의 [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)은 시간순과 gap 개념의 참고다. 패널·불규칙 관측·가변 수신 지연 때문에 여기서는 공통 날짜축과 실제 라벨 범위를 직접 검사한다.

### 각 방향별 보고 항목

- mode·모집단 조건·표본/양성/음성 수·조건부 양성 비율·문맥 밖/r5=0/제외/검열 수·기간·종목 범위·session_basis·품질 flags.
- threshold=0.5의 Precision, Recall, TN/FP/FN/TP 혼동행렬.
- PR-AUC는 **Average Precision(AP)** 정의를 주 지표로 사용한다고 이름을 명시한다. 사다리꼴 PR 적분과 혼용하지 않는다.
- Brier score와 구간별 점수/실제 양성 비율을 진단용으로 보고한다. calibration=`none`이며 점수를 검증된 상승 확률로 부르지 않는다.
- 기준 모델의 동일 파티션 지표와 차이, 날짜별·종목별 표본 수/평가 가능한 요약. 작은 하위집단에는 계산 불가 이유를 표시한다.
- 분모 0 또는 단일 클래스 등 해석 불가능한 지표는 `null`+reason. 0으로 숨기거나 NaN JSON을 만들지 않는다.
- 표본 상관이 있으므로 단순 iid 신뢰구간·정확도 한 개로 우수성을 주장하지 않는다. 거래비용·체결·포지션이 없으므로 수익률/백테스트 성과를 보고하지 않는다.

[AP 정의](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html), [점수 보정 문서](https://scikit-learn.org/stable/modules/calibration.html)를 참고한다.

## 9. 모델·신호 저장 및 조회 계약

Phase 1 시장 DB는 읽기 경계로 유지하고 별도 analysis SQLite 저장소를 사용한다. 시장 데이터 테이블에 학습 결과를 섞지 않는다. 분석 DB에서도 mode·data_origin·usage_restriction을 필수 저장하고 운영 registry는 별도 테이블/조회 경계로 분리한다. 단순히 가장 최근 학습된 모델을 운영 모델로 간주하지 않는다.

### 모델 산출물

- `model_version`: 버전 지정 canonical manifest+모델 파라미터의 SHA-256. run_id/created_at은 별도 메타데이터여서 동일 파라미터 결과의 정체성과 실행 시각을 구분한다.
- mode, data_origin, usage_restriction, population_up(r5<0), population_down(r5>0), event_description, H=5/min_barrier=0.02/volatility_multiplier=2, anchor_policy, session_basis, feature_version, label_version, artifact_schema_version, seed, 실제 의존성 버전, git SHA, 품질 manifest hash(없으면 null), quality_flags, snapshot cutoff/hash, 종목·기간·분할·purge 목록, weighting/C/threshold, calibration, 방향별 evaluation_status/test_status/operational_status를 기록한다. 이 중 분석 의미를 바꾸는 필드는 version hash에 포함한다.
- 두 방향은 조건부 모집단에서 별도로 fit한 scaler mean/scale과 coefficient/intercept, feature order, class order를 검증된 JSON 수치로 저장한다. 임의 pickle/joblib 로드를 공개 인터페이스로 받지 않는다.
- 추론은 검증된 JSON 파라미터로 동일 선형 점수와 안정적인 sigmoid를 계산한다. scikit-learn 예측과의 수치 일치 및 round-trip을 테스트한다. checksum·버전·차원·finite 검증 실패는 `invalid_model`.
- 학습에 사용한 최소 특징/라벨/시각/행 hash 스냅샷과 평가 예측을 분석 DB에 저장하여 변경된 시장 DB와 분리된 평가 재현성을 확보한다. 실제 데이터 artifact는 Git에서 제외한다.
- artifact와 평가 등록은 한 DB 트랜잭션으로 원자적으로 처리한다. 실패한 학습은 기존 모델을 변경하지 않는다. 모델·평가는 불변, 새 모델은 새 version/run.

[scikit-learn persistence 문서](https://scikit-learn.org/stable/model_persistence.html)의 임의 객체 로딩·버전 호환 제약을 고려하여 초기 선형 모델만의 작은 JSON 계약을 택한다.

### 검증 상태와 운영 등록 상태

상태는 한 개의 “좋은 모델” flag로 합치지 않고 별도 축으로 저장한다.

| 축 | 예시 | 의미 |
| --- | --- | --- |
| mode | historical_research / point_in_time | 입력 시간 정책 |
| usage_restriction | research_only / pit_review_required / synthetic_test_only | 사용 범위; synthetic가 최우선 |
| evaluation_status (방향별) | validation_qualified / baseline_not_beaten / insufficient_data | 조건부 validation 비교 결과 |
| test_status (방향별) | evaluated / unavailable / failed | 불변 최종 테스트 보고 상태; 성능 우수성 flag 아님 |
| operational_status (방향별) | unregistered / registered / revoked | 별도 운영 등록 이력 |

모든 새 모델은 unregistered다. train/evaluate/infer는 운영 registry를 쓰지 않는다. AP 통과, test 계산 성공 또는 실제 데이터라는 사실 하나만으로 자동 승격하지 않는다.

운영 등록 검토에는 모두 다음 증거가 필요하다.

1. 실제 데이터이며 point_in_time로 학습된 새 artifact. research_only/synthetic_test_only는 등록 불가이고 메타데이터 변경만으로 전환할 수 없다.
2. 데이터 출처·기업행사/수정주가·세션·종목군 품질 확인 및 남은 제한 검토. 단순한 research 위험 동의는 증거가 아니다.
3. feature/label 가용 시각·purge·revision 누출 방지와 실제 시점별 데이터 검증 결과. PIT라는 mode 이름이나 단위 테스트 통과만으로 충족하지 않는다.
4. validation 비교 및 **고정된 모델의 최종 테스트 수치·기준 대비 차이·계산 불가 사유·국면별 취약점** 검토 완료. 불리한 test를 숨기거나 다른 모델을 test로 재선택하지 않는다. AP 하나로 최종 검토를 대체하지 않는다.
5. 별도로 명시적인 운영 검토 승인 기록(reviewer, reviewed_at, artifact/evidence hashes, 범위, 승인/거부 사유). final test를 본 뒤 임의 숫자 기준을 만들어 합격시키지 않는다. 정량 운영 정책이 추가된다면 새 평가 전에 버전을 고정한다.

이번 Phase의 핵심은 상태·증거·registry 분리와 조회 차단이다. 자동 승격이나 사용자용 우회 `--force-register`는 만들지 않는다. 실제 증거·별도 등록 승인이 없으면 registry는 비어 있고 operational 조회는 빈 목록+no_registered_model 사유를 반환한다. 합성 CI 자료로 운영 등록을 대신하지 않는다. 향후 명시적 등록 절차도 위 불변 제한을 지켜야 한다.

### 추론 결과

공통 필드: `ticker, mode, data_origin, usage_restriction, model_version, feature_version, label_version, event_description, event_scope_note, horizon_sessions, min_barrier, volatility_multiplier, anchor_policy, session_basis, anchor_at, snapshot_as_of, data_as_of, analyzed_at, last_trading_date, latest_collected_at, input_hash, input_status, quality_flags, context, up_score, down_score, up_status, down_status, evaluation_status, operational_status, calibration, signal_state, reasons`.

- research의 anchor_at은 가상 과거 EOD, snapshot_as_of는 실제 스냅샷 cutoff다. PIT의 data_as_of는 실제 정보 cutoff이며 모드에 무관한 as_of 한 필드로 두 의미를 섞지 않는다. 해당하지 않는 필드는 null로 명시한다.
- event_description과 고정된 라벨 파라미터를 모든 평가·분석 출력에 포함한다. 지속 반전·상승 확률·수익 예측으로 이름을 바꾸지 않는다.
- r5<0이면 상승 모델만 실행하고 down_score=null/down_status=context_mismatch. r5>0이면 반대로 처리한다. r5=0이면 두 점수 null, signal_state=no_context다. 문맥 밖 점수를 0으로 넣거나 억지로 계산하지 않는다.
- 문맥에 맞는 점수만 0..1의 **미보정 모델 점수**로 반환한다. 동일 모드·snapshot/input hash·model version에서 점수·근거는 같고 실제 실행 시각은 달라질 수 있다.
- threshold=0.5 이상인 적용 방향은 up/down, 미만이면 neutral이다. v1의 배타적 문맥에서는 정상적으로 두 방향이 동시에 적용되지 않는다. 호환되지 않는 결과 조합 등으로 두 방향이 동시에 활성화되면 contract_conflict로 격리하고 운영 목록에서 제외한다. 서로 다른 시각/모델/모드의 점수를 합쳐 가짜 conflict를 만들지 않는다.
- reasons는 적용 방향의 특징 원값·표준화 값·선형 기여와 intercept 및 문맥이다. 모델 내부 설명이며 인과 근거는 아니다. 문맥 밖에는 기여도도 반환하지 않는다.
- 연구 모델의 validation 미달은 점수의 존재를 숨기지 않고 baseline_not_beaten과 research_only로 표시하여 비교 가능하게 한다. PIT 미등록 모델도 명시적인 분석 조회에서는 진단 점수를 저장할 수 있지만 운영 조회에는 나타나지 않는다. 데이터/모델 자체가 부족·손상·모드 불일치이면 점수=null이다.
- 부족·품질 미확인·stale·모델 미존재·미래 모델·모드 불일치 등의 복수 사유를 status/quality_flags로 보존한다. 합성 결과의 usage_restriction은 어떤 경우에도 synthetic_test_only다.
- stale은 PIT의 data_as_of 대비 마지막 거래일 7달력일 초과를 기본으로 하며 검증 세션의 누락도 검사한다. 연구는 snapshot 수집 날짜와 역사적 t의 차이를 stale로 간주하지 않고 연구 anchor에 대한 관측 공백으로 판정한다. 오래된 과거 연구이기 때문에 자동으로 데이터 부족이 되는 경로를 만들지 않는다.
- PIT 모델 created_at>data_as_of이면 model_not_available_as_of. 연구의 과거 holdout 재계산은 실제 모델 생성 시각·snapshot 시각을 명시한 retrospective 분석이며 당시 존재한 신호로 표현하지 않는다.
- 분석의 멱등 키에 mode·usage_restriction·model version·input hash·정보 cutoff·anchor를 넣고 실제 실행 감사 시각은 따로 저장한다.

### 내부 서비스와 조회 격리

`TrainingService`, `EvaluationService`, `SignalService`, `SignalQueryService`를 Python 호출 경계로 제공한다. HTTP 서버는 없다.

- `get_research_analyses(model_version, snapshot_id, anchor_range, direction, ticker, limit)`: research_only/synthetic_test_only를 명시한 연구 조회. 모델 버전 필수, 실제/합성 결과는 분리.
- `get_point_in_time_analyses(model_version, as_of, ticker)`: 미등록 PIT 진단 분석도 조회 가능하되 등록 상태를 표시한다.
- `get_latest_up_signals(as_of, model_version, limit)` / `get_latest_down_signals(...)`: **운영 전용**. 해당 방향에 등록된 실제 PIT 모델·적격 입력·문맥 일치만 허용한다.
- `get_latest_analysis(ticker, as_of, model_version)`: 운영 상세 조회. 연구/PIT 진단 조회로 자동 fallback하지 않는다.

운영 registry는 등록된 방향·model version·registered_at·evidence를 지정한다. 조회 as_of 이후 등록되었거나 철회된 모델은 당시 등록된 것으로 취급하지 않는다. 모델별 결과와 등록 이력은 독립적으로 보존한다.

최신 운영 목록은 허용된 모델과 cutoff 내에서 종목별 가장 최근 분석을 **먼저** 선택한 뒤 상태·신선도·문맥을 검사한다. 최신 분석이 부적격하다고 이전 유효 신호로 되돌아가지 않는다. query 시각에도 stale을 다시 검사한다. 미등록·연구·합성 모델을 명시적으로 요청하면 거부하며, 기본 registry가 비었을 때 최신 연구 모델을 대신 선택하지 않는다. 동일 방향·동일 model version 안에서만 점수 내림차순/ticker 오름차순으로 정렬한다. BUY/SELL 또는 주문 필드는 없다.

## 10. CLI 초안 및 구현 순서

기존 `dsv collect/update/query/validate`의 인자·stdout JSON·종료 코드를 유지한다. 새 명령에서만 별도 분석 저장소 옵션을 사용한다.

```text
# 실제 과거 일봉 스냅샷을 이용하는 연구
dsv --db MARKET_DB train --mode historical-research --tickers ...
     --start DATE --end DATE --snapshot-as-of TIMESTAMP
     [--quality-manifest PATH] [--acknowledge-research-limitations] --analysis-db PATH

# 엄격한 가용 시각 검증
dsv --db MARKET_DB train --mode point-in-time --tickers ...
     --start DATE --end DATE --as-of TIMESTAMP --quality-manifest PATH --analysis-db PATH

dsv evaluate --mode historical-research|point-in-time
     --model-version VERSION --analysis-db PATH

dsv --db MARKET_DB infer --mode historical-research
     --model-version VERSION --snapshot-id ID --anchor-date DATE --tickers ... --analysis-db PATH
dsv --db MARKET_DB infer --mode point-in-time
     --model-version VERSION --as-of TIMESTAMP --quality-manifest PATH --tickers ... --analysis-db PATH

dsv signals --scope research --model-version VERSION --snapshot-id ID
     --direction up|down|all [--ticker CODE] --analysis-db PATH
dsv signals --scope analysis --mode point-in-time --model-version VERSION
     --as-of TIMESTAMP [--ticker CODE] --analysis-db PATH
dsv signals --scope operational --direction up|down|all --as-of TIMESTAMP
     [--ticker CODE] [--model-version VERSION] [--limit N] --analysis-db PATH
```

새 train/infer/evaluate의 mode는 필수이고 artifact와 불일치하면 오류다. signals의 기본 scope는 operational이며 research/analysis는 명시적으로 요청해야 한다. 연구용 snapshot-id는 train이 만든 불변 입력을 가리킨다. 연구 risk 동의는 알려진 불량 구간의 강제 허용 옵션이 아니다.

`evaluate`는 저장된 불변 holdout 결과를 조회/재계산하며 최적화하거나 운영 등록하지 않는다. 데이터 부족은 JSON 상태와 nonzero 종료 코드, 정상 빈 조회는 0과 사유를 반환한다. 모델 mode/조회 scope 위반은 명시적 오류다. 기존 Phase 1 오류 코드 1/2와 충돌 없이 세부 매핑을 문서화한다. 실제 cutoff timestamp에는 timezone을 요구한다.

승인 후 순서:

1. 모드 격리·품질 manifest·시각·조건부 모집단·고정 라벨 계약과 fixture 테스트부터 구현.
2. 공통 날짜 분할, label_end/수신시각 purge, 표본량·가중치 검증.
3. 기준 모델·선형 두 모델·선택·평가·JSON 저장/로드.
4. 문맥별 추론·상태·근거 저장, 연구/PIT 진단/운영 조회 분리, registry 차단·CLI.
5. Phase 1 회귀 검사, README/아키텍처/계약 갱신, CI learning extra와 wheel smoke 보강.
6. 로컬 검증 후 변경 파일만 커밋·push·PR, CI 결과 확인. 사용자 승인 없이 main 병합하지 않음.

## 11. 필수 자동 검증 계획

- 같은 과거 일괄수집 fixture를 Historical Research에서는 학습 후보로 받아들이고 PIT에서는 과거 신호 생성에 사용하지 않는 대조 테스트.
- 연구 snapshot cutoff와 가상 anchor 구별, snapshot 고정, 수신시각 원본 보존, 사후 revision flags. PIT cutoff 뒤 revision은 제외/차단.
- 수동 계산과 8특징 일치, warm-up/0분모/실제 대금 독립성. 미래 가격·label 변경은 두 모드의 과거 feature에 영향 없음.
- 상승 모집단 r5<0, 하락 r5>0, 0/문맥 밖은 null; 문맥 밖 표본 추가가 모델 fit·표준화·가중치·평가 수치에 영향 없음(고정 split 사용).
- 상/하/미도달/동일 봉 양 장벽/미성숙/알려진 행사·정지·공백의 라벨 정책. H=5·0.02·2 고정 및 출력 event_description 검증.
- 공통 날짜 경계 고정 후 조건부 필터, label_end 경계 접촉 purge. PIT는 늦은 label 수신도 purge, 연구는 snapshot cutoff와 관측 기간 purge를 분리. scaler의 holdout 접근 금지.
- 조건부 표본 부족·단일 클래스·수렴 실패·기준 미달 상태 및 동일 모집단 기준 모델 비교.
- 저장/로드·checksum·차원·finite, sklearn와 JSON 추론 수치 일치, 동일 모델/데이터 재현.
- 문맥 밖 점수/근거 null, r5=0 no_context, 비정상 양방향 조합 contract_conflict, 서로 다른 시각/모드 점수 혼합 금지.
- AP를 통과한 연구/합성 모델도 운영 등록·운영 조회 거부. PIT validation_qualified이나 증거/최종검토/등록이 없으면 운영 조회 제외. train/evaluate/infer가 registry를 변경하지 않음.
- 연구/PIT/운영 scope 분리, 잘못된 mode·명시적 미등록 모델 오류, 운영 목록이 비어도 연구 fallback 없음, synthetic_test_only 불변.
- 미래 모델/미래 등록/as_of 누출/stale/최신 부적격 결과가 과거 유효 신호를 대체하는 처리.
- CLI 및 Phase 1 회귀. 네트워크 차단 pytest, Ruff, mypy, build, 독립 wheel 설치·실행, Python 3.12/3.13/3.14 유지.

위 항목은 구현의 자동 검증 계약이다. 실제 실행 결과는 PR의 검증 기록에 별도로 남긴다.

## 12. 승인 후 구현 기록

사용자는 수정안 v2를 최종 승인하고 불변 snapshot 재현, 방향별 조건부 최소량/기준 비교/null, 사용자 CI 원본 보존을 추가 회귀 조건으로 지정했다. 구현은 본 계약의 고정 라벨·수치 정책을 유지한다.

`MarketDataStore.read` → 불변 snapshot → 특징/라벨/분할/모델/평가와 연구/PIT 추론·조회 경계를 구현했다. 운영 등록 쓰기 기능은 없으며 모든 새 모델은 unregistered다. 내부 연구 조회의 선택적 anchor_range를 지원하고 CLI는 기본 최신 anchor를 조회한다. 모델 생성 시각과 실제 분석 실행 시각은 출력에 구분한다.

승인 후 origin/main과 열린 PR을 다시 확인한 결과 CI 변경은 여전히 로컬에만 있다. 원래 사용자 파일을 그대로 보존하고 별도 worktree의 Phase 2 PR에 동일한 최소 트리거 변경을 포함한다. 학습 extra와 독립 wheel smoke를 CI에 추가하며 Python 3.12/3.13/3.14를 유지한다.

실제 KRX 호출·충분한 실제 시장 표본·PIT 품질 증거·최종 성능 검토는 미완료다. 합성 테스트는 구현 검증만 수행하며 실제 예측 성능이나 추천을 주장하지 않는다. 테스트를 본 뒤 라벨/임계값/운영 등록 조건을 변경하지 않았다.
