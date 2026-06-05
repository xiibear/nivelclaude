# nivelclaude — 니벨아레나 Claude 어시스턴트

「니벨아레나」(Nivel Arena, 원작 「승리의 여신: 니케」 TCG)의 **(1) 룰 설명 (2) 덱 설계 (3) 대전 시뮬레이션**을 Claude 로 직접 수행하는 **인터랙티브 어시스턴트 프로젝트**.

> **nivelllm 과의 차이**: 자매 프로젝트 `E:\github\nivelllm` 은 Gemma 를 **파인튜닝**하는 ML 데이터 파이프라인이다. 이 프로젝트(nivelclaude)는 모델을 학습하지 않고 **Claude + 결정론 헬퍼 + 룰/카드 grounding** 으로 같은 도메인 과제를 즉시 해결한다. 데이터(`card/`, `rule/`)는 nivelllm 정본과 **해시 동일**한 사본이며 이 프로젝트 안에서 자급자족한다.

---

## 1. 핵심 원칙 — Grounding First (환각 금지)

이 프로젝트의 품질은 **모델이 카드/룰을 지어내지 않는 것**에 달려 있다.

1. **카드 사실은 절대 자의로 회상하지 않는다.** 파워·히트·코스트·텍스트·키워드·소속은 항상 `tools/cardsdb.py` 또는 `card/cards_table.csv` 에서 조회한 값을 인용한다.
2. **룰 판단은 조항 번호를 인용한다.** `rule/rules.md` 의 `§5.1.2` 같은 조항을 근거로 댄다 (grep 가능한 포맷).
3. **수치·합법성·시뮬레이션은 결정론 헬퍼로 검증한다.** 덱 합법성·사이즈/전투 계산·레이스 클럭은 `tools/` 스크립트로 돌려 수치를 보장한다 (말로 때우지 않는다).
4. **카드 텍스트가 일반 룰을 덮어쓴다** (§1.3.1). 예외는 카드 텍스트 우선.
5. 불확실하면 추정하지 말고 조회·계산한다.

---

## 2. 데이터 (1차 원천, 수정 금지)

| 경로 | 내용 |
|---|---|
| `card/cards_table.csv` | 카드 DB — 813 고유 ID(variant 포함 1,072행) × 16컬럼 |
| `card/images/` | 카드 이미지 1,072장 |
| `rule/rules.md` | 종합 룰 Ver.2.0 (11섹션, 조항 번호 포맷) |
| `rule/*.png` | 룰 도해(영역·레벨존·속성) |

CSV 컬럼: `ID,카드명,이미지파일명,코스트,카드설명,유형,속성,레어도,파워,히트,레벨,소속,키워드,제품명,IP,variant_key`
유형: `LEADER/UNIT/SKILL/ITEM`. 속성(색): 화염/대지/폭풍/파도/번개.
⚠ CSV 는 BOM 포함 → 항상 `encoding="utf-8-sig"` 로 읽는다.

---

## 3. 도메인 핵심 룰 (자주 쓰는 근거)

- **덱 구성** (§5.1.2): 리더 1장 + 정확히 40장 / 서약색 / 동일 ID ≤3 / 트리거 ≤8.
- **승패** (§1.2.2.1): 대미지 존 10장 → 패배.
- **턴**: 레벨업 → 드로우 → 메인 → 어택 → 엔드 (§6.1.2). 매 턴 레벨 +1 (§6.2.1).
- **사이즈** (§4.7.2) = 리더 레벨 + 대미지 존 수. **필드 카드 코스트 합 ≤ 사이즈** 라야 플레이 (§6.4.1.1.2) → 고코스트는 레벨이 올라야 깔림.
- **보드**: 유닛 존 3개 (§3.1.3) → 최대 3유닛, 3레인 전투.
- **전투**: 무방어=히트 대미지(§7.4.2), 파워 비교로 트래시(§7.4.3), 관통=추가 본체타(§10.2.3.2). **소환 멀미 없음**(§7.2.1).
- 키워드 15종 + 서브키워드는 §10.

---

## 4. 에이전트 로스터 (도메인 워커 4 + 대전 진행 4)

정의: `.claude/agents/<name>.md`. 모두 한국어로 답하고 §3 원칙을 따른다.

**도메인 워커 (사실·룰·설계·분석)**

| 에이전트 | 역할 | 주 도구 |
|---|---|---|
| `card-oracle` | 카드 사실 조회·검색 (환각 방지의 1차 방벽). 다른 에이전트의 사실 근거를 공급 | `tools/cardsdb.py`, `card/cards_table.csv` |
| `rule-expert` | 룰·키워드·상호작용 해설 (조항 인용) | `rule/rules.md` |
| `deck-architect` | 덱 설계·합법성 검증·아키타입/시너지/커브 | `tools/deck_validator.py`, card-oracle |
| `match-simulator` | 레이스 클럭·매치업 **확률 추정**(몬테카를로) | `tools/battle_calc.py`, `tools/race_sim.py`, rule-expert |

**대전 진행 (턴 바이 턴 결정론 실제 대전)** — `match-simulator` 가 *통계적 추정*이라면, 이 그룹은 *실제 한 판*을 룰대로 둔다.

| 에이전트 | 역할 | 주 도구 |
|---|---|---|
| `game-master` | 심판·진행자·**상태 권위**. 행동 합법성 검증·상태 전이·승패·정보 통제(클로즈드 핸드) | `tools/game_state.py`, rule-expert, card-oracle, `tools/battle_calc.py` |
| `player-one` | 플레이어 1(P1) 의사결정 — 자기 가시 상태만 보고 행동 선언 | GM 제공 `show --view P1`, card-oracle, rule-expert |
| `player-two` | 플레이어 2(P2) 의사결정 — 자기 가시 상태만 보고 행동 선언 | GM 제공 `show --view P2`, card-oracle, rule-expert |
| `play-logger` | 확정 행동을 서술 로그(`.md`)+구조화 타임라인(`.json`)으로 기록 | GM 의 `history`/`show`, `Saved/PlayLog/` |

호출 원칙: 덱/시뮬 작업은 먼저 `card-oracle` 로 카드 사실을 확정한 뒤 진행한다. 메인 에이전트가 직접 답해도 되지만, 같은 grounding 원칙(§1)을 동일하게 적용한다.

**대전 진행 오케스트레이션**: `game-master` 가 게임을 셋업·심판하고, `player-one`/`player-two` 에게 **각자 시점**(클로즈드 핸드)을 중계해 행동을 받는다. GM 이 합법성을 검증·반영하면 `play-logger` 가 그 확정 사실을 기록한다. 플레이어는 상태를 직접 못 바꾸고, GM 만 `game_state.py` 로 권위 상태를 전이한다.

---

## 5. 결정론 헬퍼 (`tools/`, stdlib only)

| 스크립트 | 용도 | 예시 |
|---|---|---|
| `cardsdb.py` | 카드 조회/검색/필터 | `python tools/cardsdb.py find "레이븐 적우"` |
| `deck_validator.py` | 덱 합법성 (§5.1.2) + 커브 | `python tools/deck_validator.py Saved/Deck/raven-movement.json` |
| `battle_calc.py` | 사이즈/전투/플레이가능 계산 | `python tools/battle_calc.py combat 5000 4000` |
| `race_sim.py` | 레이스 클럭 몬테카를로(시드 고정) | `python tools/race_sim.py` |
| `game_state.py` | **대전 상태 권위 엔진**(GM 전용) — 셋업·합법성·전투/대미지·페이즈·승패·무결성 | `python tools/game_state.py init <P1덱> <P2덱> --match m1` (→ 매치 폴더 `Saved/PlayLog/<년월일-시분초>_m1/` 자동 생성) |

원칙: 외부 의존 0 (Python 3.11 stdlib). 시뮬레이션은 **재현성을 위해 시드 고정**. `race_sim.py` 는 룰 정확 엔진이 아닌 *클럭 추정 모델*임을 항상 명시한다. `game_state.py` 는 **일반 룰·상태 무결성**을 강제하되, 카드별 효과(엔트리/각성/관통 등)는 자동 구현하지 않는다 — GM 이 룰·카드텍스트로 판정해 기본 연산(`play`/`attack`/`damage`/`set`)으로 반영한다.

---

## 6. 디렉터리 구조

```
nivelclaude/
├── CLAUDE.md              # 본 문서
├── .claude/agents/        # 도메인 워커 4 + 대전 진행 4 (GM/P1/P2/로거)
├── card/                  # 카드 DB + 이미지 (1차 원천)
├── rule/                  # 룰북 + 도해 (1차 원천)
├── tools/                 # 결정론 헬퍼 (cardsdb/deck_validator/battle_calc/race_sim/game_state)
└── Saved/                 # 사용자 데이터 (gitignore 전체)
    ├── Deck/              #   덱 JSON
    └── PlayLog/           #   매치별 폴더 <년월일-시분초>_<매치명>/ → <매치명>.md·.json·.state.json
```

덱 JSON 포맷: `{"name","leader":"<ID>","cards":{"<ID>":<count>},"notes"}`.

---

## 7. 응답 규약

- **모든 문서·주석·사용자 대화는 한국어.** 코드 식별자는 영문 + 주석에 한글 병기.
- 카드 인용 시 `[ID] 카드명` 형식, 룰 인용 시 `§조항` 형식.
- 덱을 제안하면 **반드시 `deck_validator.py` 로 합법성을 검증**하고 결과(PASS/FAIL)를 보인다.
- 시뮬레이션 결론은 가정과 한계를 명시한다.
