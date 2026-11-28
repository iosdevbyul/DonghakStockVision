# R6 failure-guided diagnostic research

On 2026-11-28 both prespecified candidates completed on the retained KRX development folds. H1 Hist15 Weekly lost 33 correct predictions against LR C=10 Weekly. H5 LR C=10 Legacy regained 11 correct predictions against the same LR with Daily+Weekly, while missing RF Legacy by eight. Both candidates retained the RF control under the existing qualification rules.

## Registration and provenance

The existing selector scores available catalog candidates against experiment memory. The opt-in preregistration verified what that selector would choose and pinned these two substitutions at 22:24:11 KST under `failure_guided_diagnostic_v1`. Their results entered experiment memory as prespecified diagnostics. The default ranking policy applies beyond that prefix and to plans without this option.

| Horizon | Original choice and catalog ID | Prespecified choice and catalog ID |
| --- | --- | --- |
| H1 | LR C=0.1 Weekly `7a0a57680bb157bb6d6ff3a0cef7c0bd5ee5ebfc812cdd913780754037f9a0af` | Hist15 Weekly `5b686d9d04e4f74b09b8e1b09bd9dca3faf2285e25c655e4fedc82ff117727c0` |
| H5 | Hist7 Monthly `182cc1eb3013240f4b30fec35a4ad18ab918c636ed60471180c6f7bef25f939e` | LR C=10 Legacy `1279ef21ebb0bfbd9357fdc60e1d172dee1e702e03449cd6654b078ef8f0bd58` |

The plan binds R2 feedback `884c46b73ade394f7bf52f40c83bdc68911646dea4dbc472b457108973a7f887`. H1 deferred another regularization setting because Weekly LR C=1 and C=10 agreed on all 680 directions while all 680 probabilities differed, with mean absolute difference 0.00001130. The tree tested transfer of the Weekly performance and its false-positive cost. H5 held Legacy features fixed because six previous candidates with other masks lost recall compared with RF Legacy and worsened Brier. The same C=10 LR with Daily+Weekly supplies the direct mask comparison.

- Pinned H1 evidence experiments: `917c19b517f9cdc334531612010ff2121d37d9e2c12bf88544c49dd16845df89`, `a87cb44a706a1ed68f402f76a42f114e207b6880be1da83e482d3c45d1846d3e`, `c2e36f1f86d1dce8739a36c499f692bdf48a34b956a3ad79737a46a51c7eb7ff`, `605579247660e5748c8da73731cba7ccc58fbb55cb54d3e94f0e552fd35f874d`.
- Pinned H5 evidence experiments: `f9d87f32baae6d732c71fa66c7f320954f9809e431a9e79f5648a934f0593a3f`, `3b83ccb33b78312e5a4de12d613b74262d0d14c79825d38c025afc26dfbb15b8`, `67752089068274da0850c30f5dd9b934946a95376a97fa3fa7ffc797599cfc12`, `adcdac523e70b95a64dc6dbb606e2c4918d37994f10d350dd9bae4e0320db975`, `34f871c8a53d1838762fcefcb829e76676768bb6de079948d3c4b009595181bc`, `ad41096bf8e92c980f9f9c92bc08824d4673e62d6476f3e8c5cdeb04dcde3d7f`, `8b919116f3e89d2e5226498ee1952d8484fd920e588a10a48f071465abc5a521`.
- R6 window `5ee3763122a9a0cda22647390a1218223fb6be58c24c78be0e303ae07bf4098f`. Plan `6f883427fea370f85239c597b9f8a87db994776059d9709a5ce436d1b0ecebbd`. The plan retains the R4 result, the preceding approved R6 plan and database binding, and both original Dataset, Source and Plan bindings.
- Execution revision `f43168b9f7fd140a550859c707da3479499b90a6` from the feature branch, installed from an isolated 2.0.0a3 wheel. Implementation hash `44251e50952512e0ad1eb629512de6ee349518c87f275be9d2ef42547da1989d`, seed 42, one worker thread.
- Retained KRX dataset IDs `42a1610b054ae80818b74e15ef9d784fb533a345332d726e2101e73e54f5173d` and `fa7565b73275b558773bfd19c4488bc8a7daa29c3793f02d2de1d6d05092629e`.

The registered training window covers 2022-01-03 through 2023-06-30 on 16 tickers. Direct comparison uses the existing 8 held-out development tickers for 2023-08-01 through 2023-12-28. The row comparison verified the population hashes, sample IDs, tickers, dates, reference and outcome prices, labels, and regimes. The label keeps the 20 bps neutral exclusion and the 0.5 classification threshold.

| Horizon | Experiment ID | Model ID | Training rows | Development rows | Fit seconds | Worker seconds |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| H1 | `e740938f85d1529a6fa6e89444cc25202e0d81a2568ec57207e658c6f81f2d6f` | `dee62a6c450fa9caa9d8b63d194437abea0671d1e97f66b9150ae9d5dc796c10` | 4,245 | 680 | 7.95 | 29.12 |
| H5 | `8ee225dc5b6839bd86696f763140f68b3e13f345c6254e6df9297dd090aea693` | `35a5dad4376f248518a7cf2d5df433d92a0d4aacbc21a0bec84a508991fdf84d` | 4,680 | 720 | 4.65 | 27.88 |

The retained R3, R4 and R6 configurations and R2 cache inventory contained no candidate matching the exact reuse contract. A configuration mismatch sufficed to reject those artifacts as exact matches. The offline run made two fits and two evaluations, with zero source-cache hits, checkpoint reuse, replays, failures, recoveries, KRX requests or Gemma calls. It ended at `requested_steps_complete` after 57.71 of 300 seconds, peaking at 243,820 KiB RSS. The ledger reported 2,974,723 bytes before archiving. Nominal remaining capacity was 242.29 seconds and twelve slots or at most twelve fits subject to that time. The pinned authorization is exhausted. Any continuation needs separate approval and a new pinned authorization.

## H1 paired evaluation

| Model | Correct/incorrect | Accuracy | BA | AP | Brier | FPR | FDR | Up recall | Down recall | TP/FP/TN/FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RF Legacy | 327/353 | 0.4809 | 0.4805 | 0.5065 | 0.2497 | 0.4810 | 0.5255 | 0.4421 | 0.5190 | 149/165/178/188 |
| LR C=10 Legacy | 355/325 | 0.5221 | 0.5226 | 0.5092 | 0.2492 | 0.5364 | 0.4842 | 0.5816 | 0.4636 | 196/184/159/141 |
| LR C=10 Weekly | 367/313 | 0.5397 | 0.5405 | 0.5610 | 0.2487 | 0.5481 | 0.4700 | 0.6291 | 0.4519 | 212/188/155/125 |
| R3 v2_hist7_0.03 v2_legacy | 337/343 | 0.4956 | 0.4965 | 0.4981 | 0.2517 | 0.6093 | 0.5073 | 0.6024 | 0.3907 | 203/209/134/134 |
| R4 v2_hist15_0.03 v2_daily_weekly | 364/316 | 0.5353 | 0.5350 | 0.5182 | 0.2510 | 0.4344 | 0.4671 | 0.5045 | 0.5656 | 170/149/194/167 |
| Hist15 Weekly (new) | 334/346 | 0.4912 | 0.4916 | 0.4869 | 0.2639 | 0.5569 | 0.5121 | 0.5401 | 0.4431 | 182/191/152/155 |

Hist15 Weekly gained 167 and lost 160 correct predictions against RF Legacy, with 186 shared errors. Against LR C=10 Weekly it gained 118 and lost 151, with 195 shared errors. It added three false positives and lost 30 true positives over the full fold relative to Weekly LR. In November alone it reduced false positives from 42 to 26 on 156 rows, while losing 13 net correct predictions. RF Legacy had 24 November false positives. Relative to RF, high volatility added 17 false positives and lost four net correct predictions, while normal volatility gained 13 net correct predictions. Sideways gained ten and down trend lost three.

The tested Hist15 Weekly candidate failed to reproduce the strong Weekly LR scores or reduce its full-fold FPR. The retained Hist7 Legacy and Hist15 Daily+Weekly comparators use other hyperparameters. Attribution to the Weekly features remains unresolved pending a matched tree config.

## H5 paired evaluation

| Model | Correct/incorrect | Accuracy | BA | AP | Brier | FPR | FDR | Up recall | Down recall | TP/FP/TN/FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| RF Legacy | 388/332 | 0.5389 | 0.5331 | 0.5842 | 0.2482 | 0.5364 | 0.4296 | 0.6026 | 0.4636 | 235/177/153/155 |
| LR C=10 Daily+Weekly | 369/351 | 0.5125 | 0.5155 | 0.5678 | 0.2596 | 0.4485 | 0.4418 | 0.4795 | 0.5515 | 187/148/182/203 |
| Hist7 Daily | 362/358 | 0.5028 | 0.5030 | 0.5240 | 0.2771 | 0.4939 | 0.4553 | 0.5000 | 0.5061 | 195/163/167/195 |
| LR C=1 Monthly | 322/398 | 0.4472 | 0.4524 | 0.4855 | 0.2576 | 0.4848 | 0.5128 | 0.3897 | 0.5152 | 152/160/170/238 |
| Hist15 Multi | 332/388 | 0.4611 | 0.4664 | 0.5166 | 0.3063 | 0.4697 | 0.4968 | 0.4026 | 0.5303 | 157/155/175/233 |
| LR C=1 Daily+Monthly | 349/371 | 0.4847 | 0.4901 | 0.5397 | 0.2587 | 0.4455 | 0.4696 | 0.4256 | 0.5545 | 166/147/183/224 |
| RF Weekly | 357/363 | 0.4958 | 0.4892 | 0.5423 | 0.2518 | 0.5909 | 0.4676 | 0.5692 | 0.4091 | 222/195/135/168 |
| Training prior | 330/390 | 0.4583 | 0.5000 | 0.5417 | 0.2526 | 0.0000 | — | 0.0000 | 1.0000 | 0/0/330/390 |
| Momentum | 338/382 | 0.4694 | 0.4648 | 0.5218 | 0.3801 | 0.5909 | 0.4899 | 0.5205 | 0.4091 | 203/195/135/187 |
| LR C=10 Legacy (new) | 380/340 | 0.5278 | 0.5203 | 0.5604 | 0.2487 | 0.5697 | 0.4413 | 0.6103 | 0.4303 | 238/188/142/152 |

Keeping Legacy features for the same C=10 LR raised recall by 13.08 percentage points and improved BA by 0.48 points relative to Daily+Weekly. It added 51 true positives and 40 false positives, raised FPR by 12.12 points, and lowered AP. Relative to RF Legacy, the new LR gained 91 and lost 99 correct predictions. It added three true positives and eleven false positives and had lower BA and AP with a higher Brier score. The Legacy mask partly recovered the recall cost of Daily+Weekly. The same-mask LR did not improve on RF under the existing indicators.

| Prior-error group | Rows | RF correct | New LR correct | RF-wrong → LR-correct | RF-correct → LR-wrong |
| --- | ---: | ---: | ---: | ---: | ---: |
| All six prior candidates wrong | 86 | 39 | 35 | 6 | 10 |
| All six and RF wrong | 47 | 0 | 6 | 6 | 0 |
| Outside the prior six-candidate common errors | 634 | 349 | 345 | 85 | 89 |
| High volatility, all rows | 132 | 63 | 62 | 15 | 16 |
| High volatility, all six wrong | 21 | 9 | 8 | 1 | 2 |
| Low volatility, all six wrong | 6 | 5 | 6 | 1 | 0 |

Eighty rows remain wrong for all seven candidates, including 41 also missed by RF. The six-candidate error groups were defined from the exposed development fold. The 47-row recovery included four up and two down outcomes.

## Ticker, trend, volatility and stability

| Group | H1 rows | H1 RF correct → Hist15 correct | H5 rows | H5 RF correct → LR correct |
| --- | ---: | ---: | ---: | ---: |
| 025530 | 86 | 45 → 37 | 91 | 57 → 54 |
| 036530 | 74 | 32 → 30 | 89 | 49 → 48 |
| 097950 | 79 | 44 → 44 | 88 | 40 → 47 |
| 105560 | 85 | 30 → 44 | 86 | 46 → 39 |
| 112610 | 91 | 44 → 49 | 93 | 49 → 54 |
| 138930 | 82 | 41 → 35 | 89 | 50 → 47 |
| 241590 | 92 | 48 → 47 | 91 | 49 → 50 |
| 402340 | 91 | 43 → 48 | 93 | 48 → 41 |
| up trend | 154 | 75 → 75 | 164 | 78 → 84 |
| sideways | 378 | 179 → 189 | 404 | 224 → 213 |
| down trend | 148 | 73 → 70 | 152 | 86 → 83 |
| high volatility | 126 | 67 → 63 | 132 | 63 → 62 |
| normal volatility | 451 | 213 → 226 | 469 | 252 → 238 |
| low volatility | 103 | 47 → 45 | 119 | 73 → 80 |

| Horizon | Panel | Correct/incorrect | BA | BA delta vs RF | Anchors | Minimum evidence |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| H1 | ticker group 1 | 177/171 | 0.5120 | -0.0044 | 33 | Met |
| H1 | ticker group 2 | 157/175 | 0.4666 | +0.0238 | 33 | Met |
| H1 | time block 1 | 144/162 | 0.4745 | -0.0070 | 16 | Below minimum |
| H1 | time block 2 | 183/177 | 0.5056 | +0.0169 | 17 | Below minimum |
| H5 | ticker group 1 | 205/158 | 0.5640 | +0.0264 | 14 | Below minimum |
| H5 | ticker group 2 | 175/182 | 0.4460 | -0.0687 | 14 | Below minimum |
| H5 | time block 1 | 177/145 | 0.5591 | -0.0111 | 6 | Below minimum |
| H5 | time block 2 | 184/170 | 0.4925 | -0.0250 | 7 | Below minimum |

Both time blocks for each horizon fall short of the existing minimum evidence. Neither candidate qualifies. Final Test and Q3 remained sealed. The expanded cohort and independent future evaluation were outside this run. PIT and Performance Approval remain false.

## Retained results and follow-up

The private execution bundle freezes the exact source revision and wheel, report, research store, paired analysis and model checksums. Its separate manifest PIN verified successfully. The existing Research Lab R6 projector read the pilot, prior approved run and diagnostic run as distinct sources in an isolated projection and verified the new plan, model and row bindings. The public visibility request currently admits two fixed R6 inputs. Adding a third requires an explicit input extension and a new approved publication. The current API and publication were untouched.

Read-only ranking returned H1 LR C=0.1 Weekly and H5 Hist7 Monthly for the next registered slots. The H1 prior 680/680 direction agreement still makes a matched Hist15 Legacy control more informative for the mask question. The existing catalog has that candidate at `a768a08bdecc941086b799a86c581e3e0dcff1c2264e001965d4a1c173ee4254`. H5's recovery and collateral errors support comparing recall and FPR jointly on the same paired rows before allocating more expanded-mask trials. These are proposals. No third slot was started.
