import json, hashlib, datetime, re
import xml.etree.ElementTree as ET
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# -----------------------------
# Filters / Scoring (B方式: 間引き)
# -----------------------------
FORCE_EXCLUDE = [
  "採用のお知らせ", "非常勤職員", "任期付職員", "期間業務職員"
]

ADD_3 = ["教育課程","学習指導要領","総則","評価","部会","ワーキンググループ","中央教育審議会","中教審"]
ADD_3_SCHOOL = ["学校","児童生徒","初等中等","高等学校","教員","外国人児童生徒"]
ADD_4 = ["情報","ICT","GIGA","DX","情報セキュリティ","生成AI","AI","情報Ⅰ","情報Ⅱ"]
ADD_2 = ["フォーラム","研修","魅力化","SSH"]

SUB_4 = ["原子力"]
SUB_3 = ["ライフサイエンス","病院"]
SUB_2 = ["大学","研究力","研究開発"]

def score_title(title: str) -> int:
    s = 0
    for k in ADD_3:
        if k in title: s += 3
    for k in ADD_3_SCHOOL:
        if k in title: s += 3
    for k in ADD_4:
        if k in title: s += 4
    for k in ADD_2:
        if k in title: s += 2
    for k in SUB_4:
        if k in title: s -= 4
    for k in SUB_3:
        if k in title: s -= 3
    for k in SUB_2:
        if k in title: s -= 2
    return s

def should_keep(title: str, category: str) -> bool:
    # AIはノイズ少ない想定 → 間引き弱め（ただし採用系は除外）
    if any(x in title for x in FORCE_EXCLUDE):
        return False

    if category == "AI":
        return True

    if "公募" in title:
        if any(x in title for x in ["学校","SSH","教育"]):
            return True
        return False

    return score_title(title) >= 3

def important_hint(title: str) -> bool:
    return any(k in title for k in [
        "教育課程","学習指導要領","評価","情報","ICT","生成AI","GIGA","DX","情報Ⅰ","情報Ⅱ"
    ])

# -----------------------------
# Auto hashtagging (精度高めの固定ルール)
# -----------------------------
TAG_RULES = [
    ("#生成AI", ["生成AI","生成ＡＩ","GenAI","AI活用","LLM"]),
    ("#情報Ⅰ", ["情報Ⅰ","情報I","情報1"]),
    ("#情報Ⅱ", ["情報Ⅱ","情報II","情報2"]),
    ("#ICT", ["ICT","GIGA","端末","BYOD","MDM","Chromebook","iPad","M365","Google Workspace"]),
    ("#評価", ["評価","観点別","ルーブリック","評定","パフォーマンス課題"]),
    ("#学習指導要領", ["学習指導要領","指導要領","総則","教育課程"]),
    ("#セキュリティ", ["情報セキュリティ","セキュリティ","個人情報","著作権","不正アクセス"]),
    ("#探究", ["探究","総合的な探究","PBL","プロジェクト学習"]),
    ("#研修", ["研修","セミナー","フォーラム","講習"]),
]

def make_tags(title: str, source: str, category: str) -> list[str]:
    text = f"{title} {source} {category}"
    tags = []
    for tag, keys in TAG_RULES:
        if any(k in text for k in keys):
            tags.append(tag)
    # 何も付かないときの保険
    if category == "AI" and "#生成AI" not in tags:
        tags.append("#生成AI")
    return sorted(set(tags))

# -----------------------------
# Minimal YAML parser (feeds.yml)
# -----------------------------
def load_feeds_yml(path="data/feeds.yml"):
    feeds = []
    cur = None
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("- "):
                if cur:
                    feeds.append(cur)
                cur = {}
                line = line[2:].strip()
                if ":" in line:
                    k, v = line.split(":", 1)
                    cur[k.strip()] = v.strip()
            elif ":" in line and cur is not None:
                k, v = line.split(":", 1)
                cur[k.strip()] = v.strip()
    if cur:
        feeds.append(cur)
    for x in feeds:
        x.setdefault("id", "")
        x.setdefault("name", "")
        x.setdefault("url", "")
        x.setdefault("category", "MISC")
    return feeds

# -----------------------------
# Fetch (UAつき、失敗しても落とさない)
# -----------------------------
def fetch(url: str) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; info-teacher-dashboard/1.0)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
        }
    )
    with urlopen(req, timeout=30) as r:
        return r.read()

# -----------------------------
# XML helper: namespace無視で拾う
# -----------------------------
def _tag_endswith(el, name: str) -> bool:
    # {namespace}name でも name でもOK
    return (el.tag or "").lower().endswith(name.lower())

def _find_first_text(parent, child_names: list[str]) -> str:
    for el in parent.iter():
        for n in child_names:
            if _tag_endswith(el, n):
                txt = (el.text or "").strip()
                if txt:
                    return txt
    return ""

def _find_link_rss2(item) -> str:
    # RSS2: <link>text</link> or <link href="..."/>
    for el in item:
        if _tag_endswith(el, "link"):
            if el.text and el.text.strip():
                return el.text.strip()
            href = el.attrib.get("href", "").strip()
            if href:
                return href
    return ""

def parse_any_feed(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    items = []

    # RSS2/ RDF っぽい: channel/item
    channel = None
    for el in root.iter():
        if _tag_endswith(el, "channel"):
            channel = el
            break

    if channel is not None:
        for it in channel.iter():
            if _tag_endswith(it, "item"):
                title = _find_first_text(it, ["title"])
                link  = _find_link_rss2(it) or _find_first_text(it, ["link"])
                date  = _find_first_text(it, ["pubDate", "date", "updated"])
                items.append((title, link, date))
        return items

    # Atom: entry
    for ent in root.iter():
        if _tag_endswith(ent, "entry"):
            title = _find_first_text(ent, ["title"])
            link = ""
            # Atom link: <link rel="alternate" href="..."/>
            for lk in ent.iter():
                if _tag_endswith(lk, "link"):
                    href = lk.attrib.get("href", "").strip()
                    if not href:
                        continue
                    rel = (lk.attrib.get("rel", "") or "").strip()
                    if rel == "alternate" or rel == "":
                        link = href
                        break
            date = _find_first_text(ent, ["updated", "published"])
            items.append((title, link, date))
    return items

def main():
    feeds = load_feeds_yml()
    all_items = []

    for f in feeds:
        url = f["url"]
        name = f["name"]
        cat  = f["category"]

        try:
            xml_bytes = fetch(url)
        except (HTTPError, URLError, TimeoutError) as e:
            print(f"[WARN] fetch failed: {name} ({url}) -> {e}")
            continue
        except Exception as e:
            print(f"[WARN] fetch failed: {name} ({url}) -> {e}")
            continue

        try:
            parsed = parse_any_feed(xml_bytes)
        except Exception as e:
            print(f"[WARN] parse failed: {name} ({url}) -> {e}")
            continue

        kept = 0
        for title, link, date in parsed:
            if not title or not link:
                continue
            if not should_keep(title, cat):
                continue

            hid = hashlib.sha1(link.encode("utf-8")).hexdigest()[:12]
            all_items.append({
                "id": hid,
                "title": title,
                "url": link,
                "date": date,
                "source": name,
                "category": cat,
                "important_hint": important_hint(title),
                "tags": make_tags(title, name, cat),
            })
            kept += 1

        print(f"[INFO] {name} ({cat}): parsed {kept} items")

    # 日付は文字列ソート（現状の仕様に合わせる）
    all_items.sort(key=lambda x: x.get("date",""), reverse=True)

    out = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "items": all_items[:600]
    }

    with open("data/items.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
