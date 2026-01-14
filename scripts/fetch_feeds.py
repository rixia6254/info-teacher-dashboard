import json, hashlib, datetime
import xml.etree.ElementTree as ET
from urllib.request import urlopen

NS = {
  "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
  "rss": "http://purl.org/rss/1.0/",
  "dc": "http://purl.org/dc/elements/1.1/"
}

FORCE_EXCLUDE = [
  "採用のお知らせ", "非常勤職員", "任期付職員", "期間業務職員"
]

ADD_3 = ["教育課程","学習指導要領","総則","評価","部会","ワーキンググループ","中央教育審議会","中教審"]
ADD_3_SCHOOL = ["学校","児童生徒","初等中等","高等学校","教員","外国人児童生徒"]
ADD_4 = ["情報","ICT","GIGA","DX","情報セキュリティ","生成AI","AI"]
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

def should_keep(title: str) -> bool:
    if any(x in title for x in FORCE_EXCLUDE):
        return False

    if "公募" in title:
        if any(x in title for x in ["学校","SSH","教育"]):
            return True
        return False

    return score_title(title) >= 3

def parse_mext_rdf(xml_bytes: bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall("rss:item", NS):
        title = (item.findtext("rss:title", default="", namespaces=NS) or "").strip()
        link  = (item.findtext("rss:link", default="", namespaces=NS) or "").strip()
        date  = (item.findtext("dc:date", default="", namespaces=NS) or "").strip()

        if not title or not link:
            continue
        if not should_keep(title):
            continue

        hid = hashlib.sha1(link.encode("utf-8")).hexdigest()[:12]
        important_hint = any(k in title for k in ["教育課程","学習指導要領","評価","情報","ICT","生成AI","GIGA","DX"])

        items.append({
            "id": hid,
            "title": title,
            "url": link,
            "date": date,
            "source": "文部科学省",
            "category": "MEXT",
            "important_hint": important_hint
        })
    return items

def main():
    url = "https://www.mext.go.jp/b_menu/news/index.rdf"
    with urlopen(url) as r:
        xml_bytes = r.read()

    items = parse_mext_rdf(xml_bytes)
    items.sort(key=lambda x: x.get("date",""), reverse=True)

    out = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "items": items[:200]
    }

    with open("data/items.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
