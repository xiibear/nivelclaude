"""전투·사이즈 결정론 계산 (match-simulator 의 grounding 도구).

룰 근거 (data/rule/rules.md):
  - §4.7.2     사이즈 = 리더 레벨 + 대미지 존 카드 수
  - §6.4.1.1.2 플레이 가능: (새 카드 코스트 + 필드 카드 코스트 합) ≤ 사이즈
  - §7.4.2     무방어 공격은 히트만큼 본체 대미지
  - §7.4.3     전투: 공격 파워 ≥ 방어 파워 → 방어 트래시 / 아니면 공격 트래시
  - §10.2.3.2  관통: 전투로 상대 유닛 트래시 시 수치만큼 본체 추가 대미지
  - §1.2.2.1   대미지 존 10장 → 패배
  - §3.1.3     유닛 존 3개 → 보드 최대 3유닛

CLI:
  python tools/battle_calc.py size 5 2          # 레벨5 + 대미지2 = 사이즈
  python tools/battle_calc.py canplay 4 3 6     # 새코스트4 + 필드합3 vs 사이즈6
  python tools/battle_calc.py combat 5000 4000  # 공격 vs 방어 파워
  python tools/battle_calc.py online 7          # 코스트7 카드가 사이즈로 깔리는 최소 레벨(무피해 가정)
"""
import sys

MAX_UNITS = 3       # §3.1.3
LETHAL = 10         # §1.2.2.1


def size(level, damage_zone):
    """§4.7.2"""
    return level + damage_zone


def can_play(new_cost, field_cost_sum, current_size):
    """§6.4.1.1.2 — True 면 플레이 가능."""
    return new_cost + field_cost_sum <= current_size


def combat(atk_power, def_power):
    """§7.4.3 — 트래시되는 쪽 반환."""
    return "defender_trashed" if atk_power >= def_power else "attacker_trashed"


def face_damage(hit, blocked, won_combat, penetration=0):
    """한 공격이 본체에 주는 대미지.
    blocked=False → 히트(§7.4.2). blocked=True 면 전투 승리 시 관통만(§10.2.3.2)."""
    if not blocked:
        return hit
    return penetration if won_combat else 0


def earliest_online_level(cost, damage_zone=0, others_cost_sum=0):
    """무피해(또는 지정 피해)·다른 유닛 코스트합 가정에서 해당 코스트가 깔리는 최소 리더 레벨."""
    # size = level + damage >= cost + others  ->  level >= cost + others - damage
    return max(1, cost + others_cost_sum - damage_zone)


def _cli(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd, a = argv[0], [int(x) for x in argv[1:]]
    if cmd == "size":
        print(f"사이즈 = 레벨{a[0]} + 대미지{a[1]} = {size(a[0], a[1])}  (§4.7.2)")
    elif cmd == "canplay":
        ok = can_play(a[0], a[1], a[2])
        print(f"새코스트{a[0]} + 필드합{a[1]} = {a[0]+a[1]} vs 사이즈{a[2]} "
              f"→ {'플레이 가능 ✅' if ok else '불가 ❌'}  (§6.4.1.1.2)")
    elif cmd == "combat":
        res = combat(a[0], a[1])
        ko = "방어 유닛 트래시" if res == "defender_trashed" else "공격 유닛 트래시"
        print(f"공격 파워{a[0]} vs 방어 파워{a[1]} → {ko}  (§7.4.3)")
    elif cmd == "online":
        dz = a[1] if len(a) > 1 else 0
        print(f"코스트{a[0]} 카드: 최소 리더 레벨 {earliest_online_level(a[0], dz)} "
              f"(대미지존 {dz} 가정)  (§4.7.2/§6.4.1.1.2)")
    else:
        print(f"알 수 없는 명령: {cmd}")
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(_cli(sys.argv[1:]))
