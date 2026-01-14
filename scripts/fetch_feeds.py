import json, hashlib, datetime
import xml.etree.ElementTree as ET
from urllib.request import urlopen, Request

# ---- Filters (B方式: 間引き) ----
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
    # AIはノイズ少ないので間引き弱め
    if category == "AI":
        return True

    if any(x in title for x in FORCE_EXCLUDE):
        return False

    if "公募" in title:
        if any(x in title for x in ["学校","SSH","教育"]):
            return True
        return False

    return score_title(title) >= 3

def important_hint(title: str) -> bool:
    return any(k in title for k in [
        "教育課程","学習指導要領","評価","情報","ICT","生成AI","GIGA","DX","情報Ⅰ","情報Ⅱ"
    ])

# ---- Minimal YAML parser for our simple feeds.yml ----
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
    # normalize
    for x in feeds:
        x.setdefault("id", "")
        x.setdefault("name", "")
        x.setdefault("url", "")
        x.setdefault("category", "MISC")
    return feeds

# ---- RSS parsers (RDF 1.0 and RSS2/Atom) ----
NS_RDF = {
  "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
  "rss": "http://purl.org/rss/1.0/",
  "dc": "http://purl.org/dc/elements/1.1/"
}

def parse_rdf10(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.findall("rss:item", NS_RDF):
        title = (item.findtext("rss:title", default="", namespaces=NS_RDF) or "").strip()
        link  = (item.findtext("rss:link", default="", namespaces=NS_RDF) or "").strip()
        date  = (item.findtext("dc:date", default="", namespaces=NS_RDF) or "").strip()
        out.append((title, link, date))
    return out

def _text(el):
    return (el.text or "").strip() if el is not None else ""

def parse_rss2_or_atom(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    items = []

    # RSS2
    channel = root.find("channel")
    if channel is not None:
        for it in channel.findall("item"):
            title = _text(it.find("title"))
            link = _text(it.find("link"))
            pub = _text(it.find("pubDate")) or _text(it.find("{http://purl.org/dc/elements/1.1/}date"))
            items.append((title, link, pub))
        return items

    # Atom
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for ent in root.findall("a:entry", ns):
        title = _text(ent.find("a:title", ns))

        # ★修正点：Atomのlink取得を強化（rel="alternate"に依存しない）
        link = ""
        for le in ent.findall("a:link", ns):
            href = (le.get("href") or "").strip()
            if href:
                link = href
                break

        pub = _text(ent.find("a:updated", ns)) or _text(ent.find("a:published", ns))
        items.append((title, link, pub))
    return items

# ---- Fetch with User-Agent ----
def fetch(url: str) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; info-teacher-dashboard/1.0; +https://github.com/)",
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        }
    )
    with urlopen(req, timeout=30) as r:
        return r.read()

def main():
    feeds = load_feeds_yml()
    all_items = []

    for f in feeds:
        url = f.get("url", "")
        name = f.get("name", "")
        cat = f.get("category", "MISC")

        # ★B方式：1つ落ちても全体は止めない
        try:
            xml_bytes = fetch(url)

            # Try RDF1.0 first, then RSS/Atom
            try:
                parsed = parse_rdf10(xml_bytes)
            except Exception:
                parsed = parse_rss2_or_atom(xml_bytes)

            # ★デバッグ用：何件取れたかをログに出す（邪魔なら後で消せます）
            print(f"[INFO] {name} ({cat}): parsed {len(parsed)} items")

        except Exception as e:
            print(f"[WARN] fetch failed: {name} ({url}) -> {e}")
            continue

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
                "important_hint": important_hint(title)
            })

    # sort by date string (good enough for now)
    all_items.sort(key=lambda x: x.get("date",""), reverse=True)

    out = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "items": all_items[:400]
    }

    with open("data/items.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
