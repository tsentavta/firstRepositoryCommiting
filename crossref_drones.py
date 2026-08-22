#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сбор статей о БПЛА из Crossref и тематическая статистика по их названиям.

Два режима:

    fetch  — опросить Crossref REST API по списку «дронных» терминов,
             отфильтровать по названию, снять дубли по DOI и сохранить JSON;
    stats  — построить тематическую статистику по названиям
             (вход: JSON от fetch либо простой текстовый файл, одно название в строке).

Зависимостей нет — только стандартная библиотека.
"""

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict

API = "https://api.crossref.org/works"
UA = "crossref-drones/1.0 (https://github.com/tsentavta/firstRepositoryCommiting)"

# --------------------------------------------------------------------------
# Поисковые запросы к Crossref (рус. + англ.)
# --------------------------------------------------------------------------
SEARCH_TERMS = [
    "БПЛА",
    "беспилотный летательный аппарат",
    "беспилотное воздушное судно",
    "беспилотная авиационная система",
    "беспилотник",
    "дрон",
    "квадрокоптер",
    "мультикоптер",
    "unmanned aerial vehicle",
    "unmanned aircraft system",
    "drone",
    "quadcopter",
    "quadrotor",
    "multirotor",
]

# --------------------------------------------------------------------------
# Проверка релевантности: слово должно стоять именно в названии.
# Поиск Crossref нечёткий и цепляет аннотации и списки литературы,
# поэтому всё, что не подтверждается названием, отбрасывается.
# --------------------------------------------------------------------------
TITLE_PATTERNS = [
    r"бпла",
    r"бвс(?![а-яё])",
    r"беспилотн",
    r"(?<![а-яё])дрон",
    r"коптер",
    r"квадророт",
    r"(?<![a-z])uavs?(?![a-z])",
    r"(?<![a-z])uas(?![a-z])",
    r"unmanned aerial",
    r"unmanned aircraft",
    r"(?<![a-z])drones?(?![a-z])",
    r"quadcopter",
    r"quadrotor",
    r"multirotor",
    r"(?<![a-z])fpv(?![a-z])",
]
TITLE_RE = re.compile("|".join(TITLE_PATTERNS), re.IGNORECASE)

# --------------------------------------------------------------------------
# Тематические рубрики. Порядок задаёт приоритет при выборе главной темы.
# Название может попасть сразу в несколько рубрик — это учитывается отдельно.
# --------------------------------------------------------------------------
CATEGORIES = [
    ("Радиолокация, обнаружение и распознавание",
     # tracking сужен намеренно: в литературе по БПЛА он чаще всего означает
     # отслеживание траектории (теория управления), а не сопровождение цели.
     r"(?<![а-яё])радиолокац|(?<![а-яё])рлс(?![а-яё])|(?<![а-яё])радар|"
     r"обнаружен|распознаван|детектир|селекц\w*\s+(?:цел|движущ)|"
     r"(?<![а-яё])эпр(?![а-яё])|отражённ|отраженн|рассеян|доп?плер|"
     r"сопровожден\w*\s+цел|"
     r"(?<![a-z])radars?(?![a-z])|detection|recognition|"
     r"(?:target|object|multi-?target|visual|vehicle|drone|uavs?|pedestrian)"
     r"\s+track(?:ing|er)?(?!\s+control)|"
     r"track\w*\s+(?:of\s+)?(?:target|object)|(?<![a-z])rcs(?![a-z])"),
    ("РЭБ, противодействие и безопасность",
     r"рэб(?![а-яё])|радиоэлектронн\w* борьб|подавлен|глушен|спуфинг|"
     r"противодейств|перехват|защит|безопасност|уязвим|кибер|нейтрализац|"
     r"jamming|spoofing|counter-ua|counter-drone|security|vulnerab|attack"),
    ("Компьютерное зрение, ИИ и машинное обучение",
     r"нейросет|нейронн|машинн\w* обучен|глубок\w* обучен|компьютерн\w* зрен|"
     r"искусственн\w* интеллект|сегментац|классификац изображ|yolo|"
     r"свёрточн|сверточн|neural|machine learning|deep learning|"
     r"computer vision|segmentation|(?<![a-z])cnn(?![a-z])"),
    ("Навигация и позиционирование",
     r"навигац|позиционир|гнсс|gnss|gps|глонасс|инерциальн|бинс(?![а-яё])|"
     r"slam|местоположен|определени\w* координат|курсов|"
     r"navigation|positioning|localization|inertial|odometry"),
    ("Управление, устойчивость, автопилот",
     r"управлен|регулятор|автопилот|стабилизац|(?<!помехо)устойчивост|пид-|"
     r"(?<![a-z])pid(?![a-z])|динамик\w* полёт|динамик\w* полет|манёвр|манев|"
     r"control|autopilot|stabiliz|attitude"),
    ("Маршруты, планирование и групповое применение",
     r"маршрут|траектор|планирован|(?<![а-яё])рой(?![а-яё])|роев|групп|"
     r"формац|обход препятств|облёт|облет|"
     r"swarm|path planning|trajectory|formation|obstacle avoidance"),
    ("Связь, антенны и передача данных",
     r"связ(?![а-яё]*н)|антенн|радиокана|канал\w* переда|передач\w* данных|"
     r"телеметри|ретрансл|(?<![а-яё])сет[иьей](?![а-яё])|mesh|lte|5g|"
     r"помехоустойчив|пропускн\w* способн|"
     r"antenna|communication|telemetry|relay|throughput|(?<![a-z])link"),
    ("Мониторинг, съёмка и картографирование",
     r"мониторинг|съёмк|съемк|аэрофото|фотограмметр|картограф|"
     r"дистанционн\w* зондирован|обследован|инспекц|тепловизион|"
     r"разведк|поисков\w* операц|"
     r"monitoring|survey|mapping|photogramm|remote sensing|inspection|thermal"),
    ("Сельское, лесное хозяйство и экология",
     r"сельск|агро|урожай|посев|(?<![а-яё])лес(?![а-яё])|лесн|почв|"
     r"эколог|растен|опрыскиван|фитосанитар|вегетацион|"
     r"agricultur|crop|forest|soil|ecolog|vegetation|spraying"),
    ("Конструкция, аэродинамика и силовая установка",
     r"аэродинам|конструкц|планер|(?<![а-яё])винт|ротор(?!н\w* систем)|"
     r"двигател|компоновк|прочност|материал|крыл|шасси|вибрац|обтекан|"
     r"aerodynam|airframe|propeller|structural|wing|composite|vibration"),
    ("Энергетика и электропитание",
     r"аккумулятор|батаре|энергетик|энергоэффективн|электропитан|"
     r"топливн\w* элемент|заряд|солнечн|энергопотреблен|"
     r"batter|energy|power supply|fuel cell|charging|solar"),
    ("Полезная нагрузка, доставка и логистика",
     r"доставк|груз|полезн\w* нагрузк|транспортиров|логистик|сброс|"
     r"delivery|payload|cargo|logistic"),
    ("Моделирование, расчёт и испытания",
     r"моделирован|имитацион|симуляц|расчёт|расчет|численн|"
     r"эксперимент|испытан|(?<![а-яё])стенд|цифров\w* двойник|верификац|"
     r"simulat|numerical|test bench|digital twin"),
    ("Правовые, экономические и организационные вопросы",
     r"правов|юридическ|(?<![а-яё])закон|нормативн|регламент|сертификац|"
     r"страхован|экономик|рынок|стоимост|управлени\w* проект|"
     r"подготовк\w* кадр|образован|обучени\w* оператор|методик\w* преподаван|"
     r"legal|regulation|certification|market|economic|training|education"),
]
CATEGORIES = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in CATEGORIES]
OTHER = "Прочее / без явной темы"


# --------------------------------------------------------------------------
# Сбор данных
# --------------------------------------------------------------------------
def http_get(url, retries=4):
    """GET с экспоненциальной паузой при сетевых сбоях и 429/5xx."""
    delay = 2
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            code = getattr(exc, "code", None)
            if code is not None and code not in (429, 500, 502, 503, 504):
                raise
            if attempt == retries:
                break
            sys.stderr.write(f"  сбой ({exc}); повтор через {delay} с\n")
            time.sleep(delay)
            delay *= 2
    raise SystemExit(f"Crossref недоступен: {last}")


def fetch_term(term, rows, issn, from_year, mailto):
    """Постранично выбрать работы по одному термину (курсорная пагинация)."""
    filters = ["type:journal-article"]
    if from_year:
        filters.append(f"from-pub-date:{from_year}-01-01")
    if issn:
        filters.append(f"issn:{issn}")

    cursor, seen, out = "*", 0, []
    while True:
        params = {
            "query.bibliographic": term,
            "filter": ",".join(filters),
            "rows": 100,
            "cursor": cursor,
            "select": "DOI,title,container-title,issued,type,publisher,subject",
        }
        if mailto:
            params["mailto"] = mailto
        data = http_get(API + "?" + urllib.parse.urlencode(params))["message"]
        items = data.get("items", [])
        if not items:
            break
        out.extend(items)
        seen += len(items)
        cursor = data.get("next-cursor")
        if not cursor or seen >= rows or seen >= data.get("total-results", 0):
            break
    return out


def norm_title(item):
    titles = item.get("title") or []
    return " ".join(t.strip() for t in titles if t).strip()


def year_of(item):
    parts = (item.get("issued") or {}).get("date-parts") or [[]]
    return parts[0][0] if parts and parts[0] else None


def cmd_fetch(args):
    by_doi = {}
    for term in SEARCH_TERMS:
        sys.stderr.write(f"Запрос: {term}\n")
        for item in fetch_term(term, args.rows, args.issn, args.from_year, args.mailto):
            title = norm_title(item)
            if not title or not TITLE_RE.search(title):
                continue                      # термин не в названии — отбрасываем
            doi = (item.get("DOI") or "").lower()
            key = doi or title.lower()
            if key in by_doi:
                by_doi[key]["matched_terms"].append(term)
                continue
            by_doi[key] = {
                "doi": doi,
                "title": title,
                "journal": (item.get("container-title") or [""])[0],
                "publisher": item.get("publisher", ""),
                "year": year_of(item),
                "language": item.get("language") or ("ru" if re.search(r"[а-яё]", title, re.I) else "en"),
                "subject": item.get("subject") or [],
                "matched_terms": [term],
            }
        sys.stderr.write(f"  накоплено уникальных: {len(by_doi)}\n")

    records = sorted(by_doi.values(), key=lambda r: (-(r["year"] or 0), r["title"]))
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)
    print(f"Сохранено {len(records)} статей → {args.out}")


# --------------------------------------------------------------------------
# Классификация и статистика
# --------------------------------------------------------------------------
# В этих сочетаниях «detection» — диагностика, безопасность или название
# прибора (LiDAR), а не обнаружение объекта. Слово гасится, определение
# остаётся: «attacks detection» → «attacks» уходит в рубрику РЭБ, как и должно.
DIAGNOSTIC_DETECTION = re.compile(
    r"((?:fault|anomaly|failure|conflict|collision|intrusion|attack|damage|"
    r"crack|defect|leak|disease|weed|light)s?)\s+detection", re.IGNORECASE)


def classify(title):
    """Все подходящие рубрики для названия (multi-label)."""
    title = DIAGNOSTIC_DETECTION.sub(r"\1", title)
    hits = [name for name, rx in CATEGORIES if rx.search(title)]
    return hits or [OTHER]


def load_records(path):
    """JSON от fetch либо текстовый файл с названиями (по одному в строке)."""
    with open(path, encoding="utf-8") as fh:
        raw = fh.read().strip()
    if raw.startswith("["):
        return json.loads(raw)
    recs = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            recs.append({
                "doi": "", "title": line, "journal": "", "publisher": "",
                "year": None,
                "language": "ru" if re.search(r"[а-яё]", line, re.I) else "en",
                "subject": [], "matched_terms": [],
            })
    return recs


def bar(n, top, width=40):
    return "█" * max(1, round(n / top * width)) if n else ""


def cmd_stats(args):
    records = load_records(args.input)
    if args.only_relevant:
        records = [r for r in records if TITLE_RE.search(r["title"])]
    if not records:
        raise SystemExit("Нет данных для анализа.")

    total = len(records)
    multi = Counter()
    primary = Counter()
    per_year = Counter()
    per_journal = Counter()
    per_lang = Counter()
    per_term = Counter()
    year_topic = defaultdict(Counter)

    priority = [name for name, _ in CATEGORIES] + [OTHER]
    for rec in records:
        cats = classify(rec["title"])
        rec["categories"] = cats
        multi.update(cats)
        main = min(cats, key=priority.index)
        rec["primary"] = main
        primary[main] += 1
        if rec.get("year"):
            per_year[rec["year"]] += 1
            year_topic[rec["year"]][main] += 1
        if rec.get("journal"):
            per_journal[rec["journal"]] += 1
        per_lang[rec.get("language") or "?"] += 1
        for term in TITLE_RE.findall(rec["title"].lower()) or []:
            per_term[term] += 1

    out = []
    add = out.append
    add("=" * 72)
    add("СТАТИСТИКА ПО НАЗВАНИЯМ СТАТЕЙ О БПЛА")
    add("=" * 72)
    add(f"Всего статей: {total}")
    if per_year:
        years = sorted(y for y in per_year if y)
        add(f"Диапазон лет: {years[0]}–{years[-1]}")
    add(f"Языки: " + ", ".join(f"{k} — {v}" for k, v in per_lang.most_common()))
    add("")

    add("-" * 72)
    add("ТЕМАТИКА (главная рубрика, каждая статья учтена один раз)")
    add("-" * 72)
    top = primary.most_common(1)[0][1]
    for name, n in primary.most_common():
        add(f"{name:<52} {n:>4}  {n/total*100:5.1f}%  {bar(n, top, 24)}")
    add("")

    add("-" * 72)
    add("ТЕМАТИКА (все совпадения; сумма > 100 %, темы пересекаются)")
    add("-" * 72)
    top = multi.most_common(1)[0][1]
    for name, n in multi.most_common():
        add(f"{name:<52} {n:>4}  {n/total*100:5.1f}%  {bar(n, top, 24)}")
    add("")

    if per_year:
        add("-" * 72)
        add("ПО ГОДАМ")
        add("-" * 72)
        top = max(per_year.values())
        for year in sorted(per_year):
            lead = year_topic[year].most_common(1)[0][0]
            add(f"{year}  {per_year[year]:>4}  {bar(per_year[year], top, 28):<28} {lead}")
        add("")

    if per_journal:
        add("-" * 72)
        add(f"ЖУРНАЛЫ (топ-{args.top})")
        add("-" * 72)
        for name, n in per_journal.most_common(args.top):
            add(f"{n:>4}  {name[:64]}")
        add("")

    if per_term:
        add("-" * 72)
        add("КЛЮЧЕВЫЕ СЛОВА В НАЗВАНИЯХ")
        add("-" * 72)
        for term, n in per_term.most_common():
            add(f"{n:>4}  {term}")
        add("")

    report = "\n".join(out)
    print(report)

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["DOI", "Год", "Язык", "Журнал", "Главная рубрика", "Все рубрики", "Название"])
            for r in records:
                w.writerow([r.get("doi", ""), r.get("year") or "", r.get("language", ""),
                            r.get("journal", ""), r["primary"], "; ".join(r["categories"]),
                            r["title"]])
        print(f"CSV: {args.csv}", file=sys.stderr)

    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(report + "\n")
        print(f"Отчёт: {args.report}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="выгрузить статьи из Crossref")
    f.add_argument("--out", default="works.json")
    f.add_argument("--rows", type=int, default=1000, help="предел записей на один термин")
    f.add_argument("--issn", help="ограничить одним журналом (ISSN)")
    f.add_argument("--from-year", type=int, help="только с этого года публикации")
    f.add_argument("--mailto", help="e-mail для «вежливого пула» Crossref (необязательно)")
    f.set_defaults(func=cmd_fetch)

    s = sub.add_parser("stats", help="статистика по названиям")
    s.add_argument("input", help="JSON от fetch либо .txt со списком названий")
    s.add_argument("--csv", help="выгрузить разметку статей в CSV")
    s.add_argument("--report", help="сохранить текстовый отчёт")
    s.add_argument("--top", type=int, default=15, help="сколько журналов показать")
    s.add_argument("--only-relevant", action="store_true",
                   help="отбросить названия без «дронных» слов")
    s.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
