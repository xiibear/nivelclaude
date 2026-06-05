"""니벨아레나 대전 상태 엔진 (game-master 의 권위 도구, stdlib only).

GM(game-master) 이 게임 상태를 **권위적으로(authoritative) 관리**하고 각 플레이어
행동의 합법성을 룰로 검증하는 결정론 코어다. 카드 사실은 `cardsdb` 에서 가져오고,
수치 판정은 모두 이 도구가 §조항에 근거해 계산한다 (CLAUDE.md §1).

⚠ 이 도구는 *모든 카드 효과를 자동 구현하지 않는다*. 엔트리/관통/각성 같은 카드별
효과는 GM 이 rule-expert·card-oracle 로 판정한 뒤 그 결과를 기본 연산
(play/attack/damage/set)으로 상태에 반영한다. 도구는 **상태 무결성과 일반 룰**을 강제한다.

룰 근거 (rule/rules.md):
  - §5.1.4~7   준비: 리더 레벨1 / 5장 드로우 / 선공부터 멀리건 1회 / 선공이 첫 턴
  - §6.1.2     턴: 레벨업→드로우→메인→어택→엔드
  - §6.2.1     레벨업 = 리더 +1 (§6.2.1.1/§4.6.3: 10 상한)
  - §6.3.1     드로우 1장 (§6.3.1.1: 선공 첫 턴은 생략)
  - §6.6.1.4   엔드: 패 8장 이상이면 7장으로 버림
  - §4.7.2     사이즈 = 리더 레벨 + 대미지 존 수
  - §6.4.1.1.2 플레이 가능: 새 코스트 + 필드 유닛 코스트 합 ≤ 사이즈
  - §3.1.3     유닛 존 3 → 보드 최대 3유닛
  - §3.5.5.1   업그레이드: 더 높은 코스트 유닛으로 기존 유닛 교체
  - §7.4.2/3   전투: 무방어=히트 본체 / 파워 비교로 트래시
  - §10.2.3.2  관통: 전투로 상대 유닛 트래시 시 수치만큼 본체 추가
  - §1.2.2.1   대미지 존 10 → 패배 / §1.2.2.4 덱 소진 드로우 → 패배

상태/로그 산출물은 매치별 폴더 `Saved/PlayLog/<년월일-시분초>_<match>/` 에 저장된다.
init 이 이 폴더를 **자동 생성**하고 `<match>.state.json` 을 그 안에 둔다(폴더 경로는 출력 및
state 의 `match_dir` 필드로 확인). play-logger 도 같은 폴더에 `.md`/`.json` 을 쓴다.
Saved/ 는 gitignore — 로컬 보관. 덱은 `Saved/Deck/`.

CLI (init 후 출력된 state 경로를 이후 명령에 사용):
  python tools/game_state.py init Saved/Deck/redhood-aggro.json Saved/Deck/raven-movement.json --seed 42 --match m1
    → Saved/PlayLog/20260605-153615_m1/m1.state.json 생성
  python tools/game_state.py show   <state.json> --view P1
  python tools/game_state.py canplay <state.json> P1 BT01-004
  python tools/game_state.py play    <state.json> P1 BT01-004
  python tools/game_state.py attack  <state.json> 0 --target face
  python tools/game_state.py damage  <state.json> P2 --n 2 --reason "BT01-004 무방어 히트2"
  python tools/game_state.py check   <state.json>
"""
import argparse
import datetime
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cardsdb  # noqa: E402

PHASES = ["level", "draw", "main", "attack", "end"]
MAX_UNITS = 3   # §3.1.3
LETHAL = 10     # §1.2.2.1
HAND_CAP = 7    # §6.6.1.4 (8장 이상이면 7장으로)
MAX_LEVEL = 10  # §4.6.3


# ── 상태 입출력 ──────────────────────────────────────────────
def load_state(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_state(st, path):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)   # Saved/PlayLog 등 산출 폴더 자동 생성
    with open(path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


# 산출물 기본 폴더 (Saved/ 는 gitignore — 로컬 보관)
PLAYLOG_DIR = os.path.join("Saved", "PlayLog")


def log(st, act, detail):
    st.setdefault("history", []).append(
        {"t": st["turn"], "by": st["active"], "phase": st["phase"],
         "act": act, "detail": detail})


def opp(p):
    return "P2" if p == "P1" else "P1"


# ── 파생 수치 (룰 인용) ──────────────────────────────────────
def size(st, p):
    """§4.7.2 = 리더 레벨 + 대미지 존 수."""
    pl = st["players"][p]
    return pl["level"] + pl["damage_zone"]


def field_cost(st, p):
    """필드(유닛 존) 카드 코스트 합 — §6.4.1.1.2 게이팅 입력."""
    return sum(cardsdb.to_int(u["cost"]) for u in st["players"][p]["board"])


# ── 덱 펼치기 / 초기화 ───────────────────────────────────────
def _expand(deck, db):
    out = []
    for cid, n in deck.get("cards", {}).items():
        out.extend([cid] * n)
    return out


def _unit(cid, db):
    r = db[cid]
    return {"id": cid, "name": r["카드명"], "cost": r["코스트"] or "0",
            "power": cardsdb.to_int(r["파워"]), "hit": cardsdb.to_int(r["히트"]),
            "rested": False, "items": []}


def cmd_init(a):
    db = cardsdb.load()
    with open(a.p1, encoding="utf-8") as f:
        d1 = json.load(f)
    with open(a.p2, encoding="utf-8") as f:
        d2 = json.load(f)

    def mk(pkey, deck):
        ld = db[deck["leader"]]
        cards = _expand(deck, db)
        rnd = random.Random(a.seed + (0 if pkey == "P1" else 1))
        rnd.shuffle(cards)
        hand = cards[:5]            # §5.1.6
        rest = cards[5:]
        return {"name": deck.get("name", pkey), "deck_file": None,
                "leader": deck["leader"], "leader_name": ld["카드명"],
                "oath": ld["속성"], "level": 1, "damage_zone": 0,  # §5.1.4
                "deck": rest, "hand": hand, "board": [], "trash": [],
                "skill_zone": []}

    first = a.first
    match_name = a.match or "match"
    # 매치 폴더: Saved/PlayLog/<년월일-시분초>_<match>/ (init 이 자동 생성)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    match_dir = os.path.join(PLAYLOG_DIR, f"{ts}_{match_name}")
    st = {"match": match_name, "match_dir": match_dir, "seed": a.seed, "turn": 1,
          "active": first, "first": first, "phase": "main", "winner": None,
          "players": {"P1": mk("P1", d1), "P2": mk("P2", d2)}, "history": []}
    log(st, "init", f"선공={first} 시드={a.seed} (§5.1.5~7). 각 5장 드로우(§5.1.6).")
    out = a.out or os.path.join(match_dir, f"{match_name}.state.json")
    save_state(st, out)   # save_state 가 match_dir 자동 생성
    print(f"게임 생성: {out}")
    print(f"  매치 폴더: {match_dir}  (로그·상태를 모두 여기에 저장)")
    print(f"  P1 {st['players']['P1']['name']} [{st['players']['P1']['oath']}]"
          f" / P2 {st['players']['P2']['name']} [{st['players']['P2']['oath']}]")
    print(f"  선공 {first} (§5.1.7). 멀리건은 'mulligan' 으로(§5.1.6.1).")
    return 0


def cmd_mulligan(a):
    """§5.1.6.1 — 패 전부 덱으로, 섞고 5장 재드로우."""
    st = load_state(a.state)
    pl = st["players"][a.player]
    rnd = random.Random(st["seed"] + 99 + (0 if a.player == "P1" else 1))
    pool = pl["hand"] + pl["deck"]
    rnd.shuffle(pool)
    pl["hand"], pl["deck"] = pool[:5], pool[5:]
    log(st, "mulligan", f"{a.player} 멀리건 — 5장 재드로우 (§5.1.6.1)")
    save_state(st, a.state)
    print(f"{a.player} 멀리건 완료. 새 패 {len(pl['hand'])}장.")
    return 0


# ── 페이즈 행동 ──────────────────────────────────────────────
def cmd_levelup(a):
    st = load_state(a.state)
    p = a.player or st["active"]
    pl = st["players"][p]
    if pl["level"] >= MAX_LEVEL:
        print(f"{p} 레벨 {pl['level']} — 이미 상한(§4.6.3/§6.2.1.1). 변화 없음.")
        return 0
    pl["level"] += 1
    st["phase"] = "level"
    log(st, "levelup", f"{p} 리더 레벨 → {pl['level']} (§6.2.1)")
    save_state(st, a.state)
    print(f"{p} 레벨 {pl['level']}  | 사이즈={size(st, p)} (§4.7.2)")
    return 0


def _draw_n(st, p, n):
    pl = st["players"][p]
    drawn = []
    for _ in range(n):
        if not pl["deck"]:
            st["winner"] = opp(p)                       # §1.2.2.4
            log(st, "deckout", f"{p} 덱 소진 드로우 → 패배 (§1.2.2.4)")
            return drawn, True
        drawn.append(pl["deck"].pop(0))
    pl["hand"].extend(drawn)
    return drawn, False


def cmd_draw(a):
    st = load_state(a.state)
    p = a.player or st["active"]
    # §6.3.1.1 선공 첫 턴 드로우 생략 (turn==1 이고 선공이면)
    if not a.force and st["turn"] == 1 and p == st["first"]:
        print(f"{p} 선공 첫 턴 — 드로우 생략 (§6.3.1.1). 강제하려면 --force.")
        return 0
    n = a.n
    drawn, dead = _draw_n(st, p, n)
    st["phase"] = "draw"
    log(st, "draw", f"{p} {len(drawn)}장 드로우" + (" → 덱아웃 패배" if dead else ""))
    save_state(st, a.state)
    if dead:
        print(f"{p} 덱 소진 — 패배(§1.2.2.4). 승자: {st['winner']}")
    else:
        print(f"{p} {len(drawn)}장 드로우. 패 {len(st['players'][p]['hand'])}장 / 덱 "
              f"{len(st['players'][p]['deck'])}장.")
    return 0


# ── 플레이 검증/실행 ─────────────────────────────────────────
def check_play(st, p, cid, db, upgrade_zone=None):
    """플레이 합법성 판정 → (ok: bool, reasons: list[str])."""
    pl = st["players"][p]
    r = db.get(cid)
    rs = []
    if r is None:
        return False, [f"{cid}: 카드 DB에 없음"]
    if cid not in pl["hand"]:
        rs.append(f"{cid} {r['카드명']}: 패에 없음")
    if r["유형"] != "LEADER" and r["속성"] != pl["oath"]:
        rs.append(f"{r['카드명']} 속성={r['속성']} ≠ 서약색 {pl['oath']} (§5.1.2.1)")
    new_cost = cardsdb.to_int(r["코스트"])
    sz, fc = size(st, p), field_cost(st, p)
    if new_cost + fc > sz:
        rs.append(f"코스트합 {new_cost}+{fc}={new_cost+fc} > 사이즈 {sz} "
                  f"(§6.4.1.1.2 불가)")
    if r["유형"] == "UNIT":
        if upgrade_zone is None and len(pl["board"]) >= MAX_UNITS:
            rs.append(f"유닛 존 가득 {len(pl['board'])}/3 (§3.1.3) — 업그레이드 필요")
        if upgrade_zone is not None:
            if not (0 <= upgrade_zone < len(pl["board"])):
                rs.append(f"업그레이드 대상 존 {upgrade_zone} 없음")
            elif new_cost <= cardsdb.to_int(pl["board"][upgrade_zone]["cost"]):
                rs.append(f"업그레이드는 더 높은 코스트라야 함 (§3.5.5.1)")
    return (not rs), rs


def cmd_canplay(a):
    st, db = load_state(a.state), cardsdb.load()
    ok, rs = check_play(st, a.player, a.card, db, a.upgrade)
    print(cardsdb.fmt(db.get(a.card), full=False))
    print(f"→ {'플레이 가능 ✅' if ok else '불가 ❌'}")
    for x in rs:
        print(f"   - {x}")
    return 0


def cmd_play(a):
    st, db = load_state(a.state), cardsdb.load()
    ok, rs = check_play(st, a.player, a.card, db, a.upgrade)
    if not ok and not a.force:
        print("플레이 불가 ❌ (강제하려면 --force):")
        for x in rs:
            print(f"   - {x}")
        return 2
    pl, r = st["players"][a.player], db[a.card]
    pl["hand"].remove(a.card)
    typ = r["유형"]
    if typ == "UNIT":
        u = _unit(a.card, db)
        if a.upgrade is not None and 0 <= a.upgrade < len(pl["board"]):
            old = pl["board"][a.upgrade]
            pl["trash"].append(old["id"])               # §3.5.5.1
            pl["board"][a.upgrade] = u
            note = f"업그레이드(존{a.upgrade}: {old['name']}→{u['name']})"
        else:
            pl["board"].append(u)
            note = f"유닛 배치(존{len(pl['board'])-1})"
    elif typ == "SKILL":
        pl["skill_zone"].append(a.card)                 # 엔드에 트래시(§6.6.1.3)
        note = "스킬 발동 → 스킬 존"
    else:  # ITEM 등
        pl["trash"].append(a.card)
        note = f"{typ} 플레이(장착/효과는 GM 판정)"
    st["phase"] = "main"
    log(st, "play", f"{a.player} [{a.card}] {r['카드명']} — {note}"
                    + (" [강제]" if not ok else ""))
    save_state(st, a.state)
    print(f"{a.player} [{a.card}] {r['카드명']} 플레이 — {note}")
    print(f"  필드 코스트합 {field_cost(st, a.player)} / 사이즈 {size(st, a.player)}")
    return 0


# ── 전투 / 대미지 ────────────────────────────────────────────
def _apply_damage(st, p, n, reason):
    pl = st["players"][p]
    pl["damage_zone"] += n
    dead = pl["damage_zone"] >= LETHAL                  # §1.2.2.1
    if dead and st["winner"] is None:
        st["winner"] = opp(p)
    log(st, "damage", f"{p} 대미지 +{n} → {pl['damage_zone']}/10 ({reason})"
                      + (" → 패배" if dead else ""))
    return dead


def cmd_damage(a):
    st = load_state(a.state)
    dead = _apply_damage(st, a.player, a.n, a.reason or "직접")
    save_state(st, a.state)
    pl = st["players"][a.player]
    print(f"{a.player} 대미지 존 {pl['damage_zone']}/10  | 사이즈={size(st, a.player)}")
    if dead:
        print(f"💀 {a.player} 패배 (§1.2.2.1). 승자: {st['winner']}")
    return 0


def cmd_attack(a):
    st, db = load_state(a.state), cardsdb.load()
    atk_p = st["active"]
    pl = st["players"][atk_p]
    if not (0 <= a.attacker < len(pl["board"])):
        print(f"공격 유닛 존 {a.attacker} 없음")
        return 2
    u = pl["board"][a.attacker]
    if u["rested"] and not a.force:
        print(f"{u['name']} 는 레스트 상태 — 공격 불가(강제 --force)")
        return 2
    atk_pow = a.atk_power if a.atk_power is not None else u["power"]
    u["rested"] = True
    dp = st["players"][opp(atk_p)]
    if a.target == "face":                              # §7.4.2 무방어
        dead = _apply_damage(st, opp(atk_p), u["hit"], f"{u['name']} 무방어 히트{u['hit']}")
        st["phase"] = "attack"
        log(st, "attack", f"{atk_p} {u['name']} → 본체 히트{u['hit']}")
        save_state(st, a.state)
        print(f"{u['name']} 무방어 공격 → {opp(atk_p)} 본체 히트 {u['hit']} "
              f"({dp['damage_zone']}/10)")
        if dead:
            print(f"💀 {opp(atk_p)} 패배. 승자: {st['winner']}")
        return 0
    # 유닛 대 유닛 (§7.4.3)
    dz = int(a.target)
    if not (0 <= dz < len(dp["board"])):
        print(f"방어 유닛 존 {dz} 없음")
        return 2
    d = dp["board"][dz]
    def_pow = a.def_power if a.def_power is not None else d["power"]
    if atk_pow >= def_pow:                              # 방어 트래시
        dp["trash"].append(d["id"])
        dp["board"].pop(dz)
        res = f"방어 {d['name']} 트래시(파워 {atk_pow}≥{def_pow})"
        face = 0
        if a.pen:                                        # §10.2.3.2 관통
            _apply_damage(st, opp(atk_p), a.pen, f"{u['name']} 관통{a.pen}")
            face = a.pen
        msg = res + (f" + 관통 본체 {face}" if face else "")
    else:
        pl["trash"].append(u["id"])
        pl["board"].pop(a.attacker)
        msg = f"공격 {u['name']} 트래시(파워 {atk_pow}<{def_pow})"
    st["phase"] = "attack"
    log(st, "attack", f"{atk_p} {u['name']}({atk_pow}) → {d['name']}({def_pow}): {msg}")
    save_state(st, a.state)
    print(f"전투: {u['name']}({atk_pow}) vs {d['name']}({def_pow}) → {msg} (§7.4.3)")
    if st["winner"]:
        print(f"💀 승자: {st['winner']}")
    return 0


# ── 엔드 / 턴 전환 ───────────────────────────────────────────
def cmd_endturn(a):
    st = load_state(a.state)
    cur = st["active"]
    pl = st["players"][cur]
    pl["skill_zone"] = []                                # §6.6.1.3
    over = max(0, len(pl["hand"]) - HAND_CAP)            # §6.6.1.4
    nxt = opp(cur)
    # 다음 턴 플레이어 유닛 언레스트(관례) + 턴 진행
    for u in st["players"][nxt]["board"]:
        u["rested"] = False
    st["active"], st["turn"], st["phase"] = nxt, st["turn"] + 1, "level"
    log(st, "endturn", f"{cur} 엔드 → {nxt} 턴{st['turn']}"
                       + (f" / {cur} 손패 {over}장 초과(버려야 함 §6.6.1.4)" if over else ""))
    save_state(st, a.state)
    print(f"{cur} 엔드 페이즈 완료 → {nxt} 의 턴 {st['turn']} 시작 (레벨업부터).")
    if over:
        print(f"  ⚠ {cur} 손패 {len(pl['hand'])}장 → 7장으로 {over}장 버려야 함 (§6.6.1.4)")
    return 0


# ── 범용 편집 (GM 의 카드효과 반영구) ────────────────────────
def cmd_set(a):
    """카드 효과·예외를 상태에 직접 반영(버프/언레스트/특수). GM 전용."""
    st = load_state(a.state)
    pl = st["players"][a.player]
    val = a.value
    try:
        val = int(val)
    except ValueError:
        pass
    if a.zone is not None:                               # 보드 유닛 속성
        u = pl["board"][a.zone]
        u[a.field] = val
        tgt = f"{a.player} 존{a.zone} {u['name']}.{a.field}={val}"
    else:                                                # 플레이어 속성
        pl[a.field] = val
        tgt = f"{a.player}.{a.field}={val}"
    log(st, "set", f"{tgt} ({a.reason or 'GM 효과 반영'})")
    save_state(st, a.state)
    print(f"설정: {tgt}")
    return 0


# ── 표시 / 무결성 ────────────────────────────────────────────
def _fmt_board(board):
    if not board:
        return "(비어있음)"
    return " | ".join(f"존{i}:{u['name']}(P{u['power']}/H{u['hit']}"
                      f"{',R' if u['rested'] else ''})" for i, u in enumerate(board))


def cmd_show(a):
    st = load_state(a.state)
    view = a.view
    print(f"=== {st['match']} | 턴 {st['turn']} | 현재 {st['active']} | "
          f"페이즈 {st['phase']} ===")
    if st["winner"]:
        print(f"  🏁 승자: {st['winner']}")
    for p in ("P1", "P2"):
        pl = st["players"][p]
        mark = "▶" if p == st["active"] else " "
        print(f"{mark} {p} {pl['name']} [{pl['oath']}] Lv{pl['level']} "
              f"사이즈={size(st, p)}  대미지 {pl['damage_zone']}/10")
        print(f"    보드: {_fmt_board(pl['board'])}  (필드코스트 {field_cost(st, p)})")
        # 클로즈드 핸드: 자기 뷰/풀 뷰만 패 공개, 상대는 매수만
        if view == "full" or view == p:
            print(f"    패({len(pl['hand'])}): {', '.join(pl['hand']) or '-'}")
        else:
            print(f"    패: {len(pl['hand'])}장 (비공개)")
        print(f"    덱 {len(pl['deck'])} / 트래시 {len(pl['trash'])} / "
              f"스킬존 {len(pl['skill_zone'])}")
    return 0


def cmd_check(a):
    """상태 무결성 검증 — 룰 위반·이상치 탐지."""
    st, db = load_state(a.state), cardsdb.load()
    errs, warns = [], []
    for p in ("P1", "P2"):
        pl = st["players"][p]
        if len(pl["board"]) > MAX_UNITS:
            errs.append(f"{p} 보드 {len(pl['board'])} > 3 (§3.1.3)")
        if pl["damage_zone"] > LETHAL and st["winner"] != opp(p):
            warns.append(f"{p} 대미지 {pl['damage_zone']}≥10 인데 패배 미반영 (§1.2.2.1)")
        if pl["level"] > MAX_LEVEL:
            errs.append(f"{p} 레벨 {pl['level']} > 10 (§4.6.3)")
        fc, sz = field_cost(st, p), size(st, p)
        if fc > sz:
            warns.append(f"{p} 필드코스트 {fc} > 사이즈 {sz} — 오버사이즈(§6.4.1.1.2)")
        for cid in pl["hand"] + pl["deck"] + pl["trash"]:
            if cid not in db:
                errs.append(f"{p} 미존재 카드 ID: {cid}")
        # 서약색 일치(필드/패의 비리더 카드)
        for cid in set(pl["hand"]):
            r = db.get(cid)
            if r and r["유형"] != "LEADER" and r["속성"] != pl["oath"]:
                errs.append(f"{p} 패의 {cid} 속성 {r['속성']} ≠ 서약 {pl['oath']} (§5.1.2.1)")
    print(f"=== 무결성 검사: {st['match']} 턴 {st['turn']} ===")
    for w in warns:
        print(f"  ⚠ {w}")
    for e in errs:
        print(f"  ✗ {e}")
    print(f"판정: {'OK ✅' if not errs else 'VIOLATION ❌'}")
    return 0 if not errs else 2


# ── CLI ──────────────────────────────────────────────────────
def _build_parser():
    ap = argparse.ArgumentParser(description="니벨아레나 대전 상태 엔진")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="게임 초기화 (§5.1)")
    s.add_argument("p1"); s.add_argument("p2")
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--first", choices=["P1", "P2"], default="P1")
    s.add_argument("--match", default=None)
    s.add_argument("--out", default=None)
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("mulligan", help="멀리건 (§5.1.6.1)")
    s.add_argument("state"); s.add_argument("player", choices=["P1", "P2"])
    s.set_defaults(fn=cmd_mulligan)

    s = sub.add_parser("show", help="상태 표시(클로즈드 핸드)")
    s.add_argument("state")
    s.add_argument("--view", choices=["P1", "P2", "full"], default="full")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("size", help="사이즈 (§4.7.2)")
    s.add_argument("state"); s.add_argument("player", choices=["P1", "P2"])
    s.set_defaults(fn=lambda a: (print(f"{a.player} 사이즈={size(load_state(a.state), a.player)} (§4.7.2)"), 0)[1])

    s = sub.add_parser("levelup", help="레벨업 페이즈 (§6.2.1)")
    s.add_argument("state"); s.add_argument("player", nargs="?", choices=["P1", "P2"])
    s.set_defaults(fn=cmd_levelup)

    s = sub.add_parser("draw", help="드로우 페이즈 (§6.3.1)")
    s.add_argument("state"); s.add_argument("player", nargs="?", choices=["P1", "P2"])
    s.add_argument("--n", type=int, default=1)
    s.add_argument("--force", action="store_true", help="선공 첫 턴에도 드로우")
    s.set_defaults(fn=cmd_draw)

    s = sub.add_parser("canplay", help="플레이 가능 검증 (§6.4.1.1.2)")
    s.add_argument("state"); s.add_argument("player", choices=["P1", "P2"])
    s.add_argument("card"); s.add_argument("--upgrade", type=int, default=None)
    s.set_defaults(fn=cmd_canplay)

    s = sub.add_parser("play", help="카드 플레이(검증 후 전이)")
    s.add_argument("state"); s.add_argument("player", choices=["P1", "P2"])
    s.add_argument("card"); s.add_argument("--upgrade", type=int, default=None)
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_play)

    s = sub.add_parser("attack", help="공격 선언/전투 (§7.4)")
    s.add_argument("state"); s.add_argument("attacker", type=int, help="공격 유닛 존")
    s.add_argument("--target", required=True, help="face 또는 방어 유닛 존 번호")
    s.add_argument("--pen", type=int, default=0, help="관통 수치 (§10.2.3.2)")
    s.add_argument("--atk-power", type=int, default=None, dest="atk_power")
    s.add_argument("--def-power", type=int, default=None, dest="def_power")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_attack)

    s = sub.add_parser("damage", help="본체 대미지 (§1.2.2.1)")
    s.add_argument("state"); s.add_argument("player", choices=["P1", "P2"])
    s.add_argument("--n", type=int, required=True)
    s.add_argument("--reason", default=None)
    s.set_defaults(fn=cmd_damage)

    s = sub.add_parser("endturn", help="엔드 페이즈/턴 전환 (§6.6.1)")
    s.add_argument("state")
    s.set_defaults(fn=cmd_endturn)

    s = sub.add_parser("set", help="GM: 카드효과·예외를 상태에 반영(버프/언레스트 등)")
    s.add_argument("state"); s.add_argument("player", choices=["P1", "P2"])
    s.add_argument("field"); s.add_argument("value")
    s.add_argument("--zone", type=int, default=None, help="지정 시 해당 보드 유닛 속성")
    s.add_argument("--reason", default=None)
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("check", help="상태 무결성 검사")
    s.add_argument("state")
    s.set_defaults(fn=cmd_check)
    return ap


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _build_parser().parse_args()
    raise SystemExit(args.fn(args))
