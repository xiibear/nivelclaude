"""레이스 클럭 몬테카를로 시뮬레이터 (match-simulator 의 정량 도구).

⚠ 룰 정확 엔진이 아니라 *클럭 추정 모델*이다. 두 덱의 턴별 '본체 타점 분포'와
'방어 경감 분포', 상대의 '안정화 턴'을 입력받아 누적 대미지가 10(§1.2.2.1)에
도달하는 턴 분포와 레이스 승률을 추정한다. 시드 고정으로 재현 가능.

핵심 메커니즘 반영:
  - 승리 = 대미지 10 (§1.2.2.1)
  - 보드 최대 3유닛 (§3.1.3), 소환멀미 없음(§7.2.1) → 선공·어그로 우대
  - 사이즈 게이팅(§6.4.1.1.2)으로 고코스트는 후반에만 → 타점 분포에 내재
  - 안정화 턴 이후 방어 경감 급증(벽·제거 카드)

config(JSON) 예시는 파일 하단 DEFAULT 참조. CLI:
  python tools/race_sim.py                       # 기본(레드후드 vs 레이븐) 데모
  python tools/race_sim.py my_config.json
"""
import json
import random
import statistics
import sys

# 기본 설정: 이 대화에서 분석한 레드후드 어그로 vs 레이븐 (선공 기준)
DEFAULT = {
    "attacker": "레드후드 어그로",
    "defender": "레이븐 콜로니",
    "seed": 42,
    "trials": 30000,
    "lethal": 10,
    # 턴별 본체 타점 후보(랜덤 선택). 초반 히트1 3레인, 후반 각성/관통 버스트
    "raw_on_play":  {"1": [1,1,1], "2": [1,2,2], "3": [1,2,2,3], "4": [2,2,3,3],
                     "5": [3,3,4], "6": [4,5,6], "7": [5,6,6], "8": [6,7]},
    # 방어 경감(블로커·적우 제거). 안정화 후 +2
    "prevent":      {"1": [0], "2": [0,1], "3": [1,1,2], "4": [1,2,2],
                     "5": [2,2,3], "6": [2,3,3], "7": [3,3,4], "8": [3,4,4]},
    "stabilize_turn_on_play": [6, 7, 7, 7, 8],
    "stabilize_turn_on_draw": [5, 6, 6, 7],
    "draw_tempo_penalty_turns": 2,   # 후공 시 초반 N턴 타점 -1
}


def _pick(d, t):
    return d[str(min(t, max(int(k) for k in d)))]


def simulate(cfg, on_play=True):
    rnd = random.Random(cfg["seed"] + (0 if on_play else 1))
    raw = cfg["raw_on_play"]
    prv = cfg["prevent"]
    stab_pool = cfg["stabilize_turn_on_play"] if on_play else cfg["stabilize_turn_on_draw"]
    pen = cfg["draw_tempo_penalty_turns"]
    lethal = cfg["lethal"]
    kills, wins = [], 0
    maxt = max(int(k) for k in raw)
    for _ in range(cfg["trials"]):
        stab = rnd.choice(stab_pool)
        dmg, kill = 0, None
        for t in range(1, maxt + 1):
            out = rnd.choice(_pick(raw, t))
            if not on_play and t <= pen:
                out = max(0, out - 1)
            pv = rnd.choice(_pick(prv, t)) + (2 if t > stab else 0)
            dmg += max(0, out - pv)
            if dmg >= lethal and kill is None:
                kill = t
        kills.append(kill if kill is not None else 99)
        if kill is not None and kill <= stab:
            wins += 1
    finite = [k for k in kills if k != 99]
    dist = {}
    for k in kills:
        key = "9턴+/실패" if k > 8 else f"{k}턴"
        dist[key] = dist.get(key, 0) + 1
    return {
        "win_rate": wins / cfg["trials"],
        "median_kill": statistics.median(finite) if finite else None,
        "fail_rate": kills.count(99) / cfg["trials"],
        "dist": dist,
    }


def cumulative_curve(cfg, on_play=True):
    rnd = random.Random(cfg["seed"] + 100)
    raw, prv = cfg["raw_on_play"], cfg["prevent"]
    maxt = max(int(k) for k in raw)
    acc = [0.0] * (maxt + 1)
    for _ in range(cfg["trials"]):
        d = 0
        for t in range(1, maxt + 1):
            d += max(0, rnd.choice(_pick(raw, t)) - rnd.choice(_pick(prv, t)))
            acc[t] += d
    return [acc[t] / cfg["trials"] for t in range(maxt + 1)]


def _report(cfg):
    print(f"=== 레이스 시뮬: {cfg['attacker']} (공격) vs {cfg['defender']} (방어) ===")
    print(f"시드 {cfg['seed']}, {cfg['trials']:,}회, 리썰 {cfg['lethal']}\n")
    for label, op in [("공격측 선공", True), ("공격측 후공", False)]:
        r = simulate(cfg, op)
        print(f"[{label}] 레이스 승률 {r['win_rate']*100:.0f}% / "
              f"중앙 처치 {r['median_kill']}턴 / 실패 {r['fail_rate']*100:.0f}%")
        for key in ["5턴", "6턴", "7턴", "8턴", "9턴+/실패"]:
            if key in r["dist"]:
                print(f"    {key}: {r['dist'][key]/cfg['trials']*100:4.0f}%")
        print()
    curve = cumulative_curve(cfg, True)
    print("선공 평균 누적 대미지:")
    for t in range(1, len(curve)):
        flag = " ← 10 도달" if curve[t] >= cfg["lethal"] else ""
        print(f"  {t}턴: {curve[t]:4.1f} {'█'*int(curve[t])}{flag}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = DEFAULT
    _report(cfg)
