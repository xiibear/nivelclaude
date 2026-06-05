"""덱 합법성 검증 (deck-architect 의 결정론 도구).

룰 §5.1.2 덱 구성 조건 (data/rule/rules.md):
  - §5.1.2  : 리더 1장 + 정확히 40장
  - §5.1.2.1: 모든 카드가 리더 [서약] 색 조건 충족
  - §5.1.2.2: 같은 식별번호(ID) 최대 3장
  - §5.1.2.3: 트리거 카드 최대 8장

덱 JSON 포맷:
  {"name": "...", "leader": "BT07-001", "cards": {"BT07-003": 3, ...}}
덱은 `Saved/Deck/` 에 저장된다(Saved/ 는 gitignore — 로컬 보관).

CLI:
  python tools/deck_validator.py Saved/Deck/raven-movement.json
"""
import json
import sys

import cardsdb


def parse_oath(leader_row):
    """리더 서약색 추출. 표준 단색 리더는 리더 자신의 속성 = 서약색.
    복합 서약(예: BT05-001 카티야)은 raw 텍스트를 함께 반환해 수동 확인 유도."""
    own = leader_row["속성"]
    txt = leader_row["카드설명"] or ""
    complex_oath = ("이외의" in txt and "서약" in txt) or txt.count("[서약]") == 0
    return own, complex_oath, txt


def validate(deck, db=None):
    """deck dict -> 검증 결과 dict."""
    db = db if db is not None else cardsdb.load()
    errs, warns = [], []

    leader = db.get(deck.get("leader"))
    if leader is None:
        errs.append(f"리더 ID '{deck.get('leader')}' 가 카드 DB에 없음")
        oath = None
    elif leader["유형"] != "LEADER":
        errs.append(f"{leader['ID']} {leader['카드명']} 은 LEADER 카드가 아님")
        oath = None
    else:
        oath, complex_oath, _ = parse_oath(leader)
        if complex_oath:
            warns.append(f"복합 서약 리더 — 서약 텍스트 수동 확인 필요: {leader['카드설명']}")

    cards = deck.get("cards", {})
    total = sum(cards.values())
    triggers = 0
    for cid, n in cards.items():
        r = db.get(cid)
        if r is None:
            errs.append(f"존재하지 않는 ID: {cid}")
            continue
        if r["유형"] == "LEADER":
            errs.append(f"{cid} {r['카드명']} 은 리더 카드 — 덱 40장에 포함 불가")
        if n > 3:
            errs.append(f"{cid} {r['카드명']} {n}장 (§5.1.2.2 위반: ≤3)")
        if n < 1:
            errs.append(f"{cid} 매수 {n} 비정상")
        if oath and r["유형"] != "LEADER" and r["속성"] != oath:
            errs.append(f"{cid} {r['카드명']} 속성={r['속성']} ≠ 서약색 {oath} (§5.1.2.1)")
        if cardsdb.is_trigger(r):
            triggers += n

    if total != 40:
        errs.append(f"덱 장수 {total} (§5.1.2 위반: 정확히 40)")
    if triggers > 8:
        errs.append(f"트리거 카드 {triggers}장 (§5.1.2.3 위반: ≤8)")

    return {
        "name": deck.get("name", "(이름없음)"),
        "leader": f"{leader['ID']} {leader['카드명']} [{oath}]" if leader else None,
        "total": total,
        "triggers": triggers,
        "errors": errs,
        "warnings": warns,
        "legal": not errs,
    }


def curve(deck, db=None):
    db = db if db is not None else cardsdb.load()
    units, skills, items = {}, 0, 0
    for cid, n in deck.get("cards", {}).items():
        r = db.get(cid)
        if not r:
            continue
        if r["유형"] == "UNIT":
            units[cardsdb.to_int(r["코스트"])] = units.get(cardsdb.to_int(r["코스트"]), 0) + n
        elif r["유형"] == "SKILL":
            skills += n
        elif r["유형"] == "ITEM":
            items += n
    return units, skills, items


def _print_report(deck, db):
    res = validate(deck, db)
    print(f"=== 덱 검증: {res['name']} ===")
    print(f"리더: {res['leader']}")
    print(f"총 {res['total']}/40   트리거 {res['triggers']}/8")
    for w in res["warnings"]:
        print(f"  ⚠ {w}")
    if res["errors"]:
        for e in res["errors"]:
            print(f"  ✗ {e}")
    print(f"판정: {'PASS ✅' if res['legal'] else 'FAIL ❌'}")
    units, skills, items = curve(deck, db)
    print("\n코스트 커브(유닛):")
    for c in sorted(units):
        print(f"  {c}코: {'■' * units[c]} ({units[c]})")
    print(f"유닛 {sum(units.values())} / 스킬 {skills} / 아이템 {items}")
    return res["legal"]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        deck = json.load(f)
    db = cardsdb.load()
    ok = _print_report(deck, db)
    raise SystemExit(0 if ok else 2)
