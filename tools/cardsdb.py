"""니벨아레나 카드 DB 조회 (card-oracle 의 결정론 도구, stdlib only).

`card/cards_table.csv` 를 카드 사실(fact)의 단일 원천(ground truth)으로 한다.
Claude 는 카드 수치·텍스트를 절대 자의로 회상하지 않고 이 도구의 출력을 인용한다.

컬럼: ID,카드명,이미지파일명,코스트,카드설명,유형,속성,레어도,파워,히트,레벨,소속,키워드,제품명,IP,variant_key

CLI:
  python tools/cardsdb.py get BT07-034
  python tools/cardsdb.py find "레이븐 적우"
  python tools/cardsdb.py filter --color 대지 --type UNIT --cost 2 --keyword 어태커
  python tools/cardsdb.py leaders
"""
import csv
import os
import re
import sys

# 속성(색) — rules.md §2.3.2
COLORS = {"화염": "Fire/red", "대지": "Earth/green", "폭풍": "Storm/purple",
          "파도": "Wave/blue", "번개": "Thunder/yellow"}


def _csv_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "card", "cards_table.csv")


def load(path=None):
    """ID 기준으로 정규화된 카드 dict 반환 (variant/레어도 중복은 첫 행 유지)."""
    path = path or _csv_path()
    with open(path, encoding="utf-8-sig") as f:  # utf-8-sig: BOM 처리 필수
        rows = list(csv.DictReader(f))
    by_id = {}
    for r in rows:
        by_id.setdefault(r["ID"], r)
    return by_id


def normalize(s):
    """카드명 매칭용 정규화 — 구분자(공백/하이픈/대괄호/점/콜론) 제거 후 소문자."""
    return re.sub(r"[\s\-\[\]()·:.,]", "", (s or "")).lower()


def get(card_id, db=None):
    db = db if db is not None else load()
    return db.get(card_id)


def find_by_name(name, db=None):
    """정규화 완전일치 우선, 없으면 부분일치(짧은 이름순 = 더 구체적 먼저)."""
    db = db if db is not None else load()
    n = normalize(name)
    exact = [r for r in db.values() if normalize(r["카드명"]) == n]
    if exact:
        return exact
    subs = [r for r in db.values() if n and n in normalize(r["카드명"])]
    return sorted(subs, key=lambda r: len(r["카드명"]))


def is_trigger(r):
    return "트리거" in (r.get("키워드") or "")


def has_keyword(r, kw):
    return kw in (r.get("키워드") or "") or kw in (r.get("카드설명") or "")


def to_int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def fmt(r, full=True):
    """사람이 읽는 카드 사실 블록."""
    if r is None:
        return "(해당 카드 없음)"
    color = r["속성"]
    head = (f"[{r['ID']}] {r['카드명']}  | {r['유형']} | {color}"
            f"({COLORS.get(color, '')}) | 코스트 {r['코스트'] or '-'} "
            f"| 파워 {r['파워'] or '-'} | 히트 {r['히트'] or '-'}")
    lines = [head,
             f"  소속: {r['소속'] or '-'}   키워드: {r['키워드'] or '-'}"
             f"   트리거: {'O' if is_trigger(r) else 'X'}",
             f"  제품: {r['제품명']}"]
    if full:
        lines.append(f"  텍스트: {r['카드설명'] or '(없음)'}")
    return "\n".join(lines)


def _cli(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    db = load()
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "get":
        for cid in rest:
            print(fmt(get(cid, db)))
            print()
    elif cmd == "find":
        name = " ".join(rest)
        hits = find_by_name(name, db)
        if not hits:
            print(f"'{name}' 일치 카드 없음")
        for r in hits[:15]:
            print(fmt(r))
            print()
        if len(hits) > 15:
            print(f"... 외 {len(hits) - 15}건")
    elif cmd == "filter":
        opts = {}
        i = 0
        while i < len(rest):
            if rest[i].startswith("--"):
                opts[rest[i][2:]] = rest[i + 1]
                i += 2
            else:
                i += 1
        out = list(db.values())
        if "color" in opts:
            out = [r for r in out if r["속성"] == opts["color"]]
        if "type" in opts:
            out = [r for r in out if r["유형"] == opts["type"].upper()]
        if "cost" in opts:
            out = [r for r in out if r["코스트"] == opts["cost"]]
        if "soul" in opts:
            out = [r for r in out if opts["soul"] in (r["소속"] or "")]
        if "keyword" in opts:
            out = [r for r in out if has_keyword(r, opts["keyword"])]
        out.sort(key=lambda r: (to_int(r["코스트"]), r["ID"]))
        for r in out:
            print(fmt(r, full=False))
        print(f"\n총 {len(out)}건")
    elif cmd == "leaders":
        ld = sorted([r for r in db.values() if r["유형"] == "LEADER"],
                    key=lambda r: r["ID"])
        for r in ld:
            print(f"[{r['ID']}] {r['카드명']} [{r['속성']}] 소속={r['소속']}")
        print(f"\n총 {len(ld)} 리더")
    else:
        print(f"알 수 없는 명령: {cmd}")
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
