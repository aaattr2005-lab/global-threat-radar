import os
import re
import json
import time
from pathlib import Path
from datetime import datetime, timezone

import requests


# =========================================================
# SETTINGS
# =========================================================

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

BASE_DIR = Path(__file__).resolve().parents[1]
STATE_FILE = BASE_DIR / "state" / "seen.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

MIN_CONFIDENCE = os.getenv("MIN_CONFIDENCE", "Unverified")
MAX_ALERTS = int(os.getenv("MAX_ALERTS", "10"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes"}

# GDELT accepted format: 1h / 2h / etc.
# We use 1h because the live endpoint rejected 15min in testing.
GDELT_TIMESPAN = "1h"
GDELT_MAX_RECORDS = 250
GDELT_RETRY_SECONDS = 6

CONFIDENCE_RANK = {
    "Unverified": 1,
    "Likely": 2,
    "Confirmed": 3,
}


# =========================================================
# TRUSTED SOURCES
# =========================================================

TIER_1_DOMAINS = {
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "bbc.co.uk",
    "aljazeera.com",
    "france24.com",
    "dw.com",
    "bloomberg.com",
    "cnn.com",
    "skynews.com",
    "euronews.com",
    "arabnews.com",
    "aa.com.tr",
    "theguardian.com",
    "nytimes.com",
    "wsj.com",
}


# =========================================================
# LOCATIONS
# Keep cities before countries so the alert is as specific as possible.
# =========================================================

LOCATIONS = [
    # Saudi Arabia
    ("الرياض", ["riyadh"]),
    ("جدة", ["jeddah"]),
    ("مكة", ["makkah", "mecca"]),
    ("المدينة المنورة", ["medina"]),
    ("أبها", ["abha"]),
    ("خميس مشيط", ["khamis mushait"]),
    ("جازان", ["jazan", "jizan"]),
    ("نجران", ["najran"]),
    ("الدمام", ["dammam"]),
    ("الظهران", ["dhahran"]),
    ("السعودية", ["saudi arabia", "saudi"]),

    # Yemen
    ("صنعاء", ["sanaa", "sana'a"]),
    ("عدن", ["aden"]),
    ("الحديدة", ["hodeidah", "hudaydah"]),
    ("صعدة", ["saada", "sa'dah"]),
    ("مأرب", ["marib", "ma'rib", "mareb"]),
    ("تعز", ["taiz", "taizz"]),
    ("عمران", ["amran"]),
    ("حجة", ["hajjah", "hajja"]),
    ("الجوف - اليمن", ["al jawf", "al-jawf"]),
    ("شبوة", ["shabwah", "shabwa"]),
    ("ذمار", ["dhamar"]),
    ("إب", ["ibb"]),
    ("المكلا", ["mukalla", "al mukalla"]),
    ("اليمن", ["yemen"]),

    # Middle East
    ("طهران", ["tehran"]),
    ("إيران", ["iran"]),
    ("بغداد", ["baghdad"]),
    ("العراق", ["iraq"]),
    ("تل أبيب", ["tel aviv"]),
    ("القدس", ["jerusalem"]),
    ("إسرائيل", ["israel"]),
    ("بيروت", ["beirut"]),
    ("لبنان", ["lebanon"]),
    ("دمشق", ["damascus"]),
    ("سوريا", ["syria"]),
    ("الأردن", ["jordan"]),
    ("الإمارات", ["united arab emirates", "uae"]),
    ("قطر", ["qatar"]),
    ("الكويت", ["kuwait"]),
    ("البحرين", ["bahrain"]),
    ("عُمان", ["oman"]),
    ("مصر", ["egypt"]),
    ("تركيا", ["turkey", "türkiye"]),

    # Europe / Russia / Ukraine
    ("كييف", ["kyiv", "kiev"]),
    ("أوكرانيا", ["ukraine"]),
    ("موسكو", ["moscow"]),
    ("روسيا", ["russia"]),
    ("بولندا", ["poland"]),
    ("رومانيا", ["romania"]),
    ("مولدوفا", ["moldova"]),
    ("بيلاروسيا", ["belarus"]),

    # Asia
    ("الهند", ["india"]),
    ("باكستان", ["pakistan"]),
    ("أفغانستان", ["afghanistan"]),
    ("الصين", ["china"]),
    ("تايوان", ["taiwan"]),
    ("اليابان", ["japan"]),
    ("كوريا الجنوبية", ["south korea"]),
    ("كوريا الشمالية", ["north korea"]),
    ("الفلبين", ["philippines"]),

    # West
    ("الولايات المتحدة", ["united states", "usa"]),
    ("بريطانيا", ["united kingdom", "uk"]),
    ("فرنسا", ["france"]),
    ("ألمانيا", ["germany"]),
]


# =========================================================
# TEXT HELPERS
# =========================================================

STOP_WORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for",
    "from", "with", "and", "or", "after", "before", "by",
    "says", "say", "said", "report", "reports", "reported",
    "breaking", "live", "latest", "new", "near", "into",
    "amid", "during", "is", "are", "was", "were", "be",
    "this", "that", "it", "its", "has", "have", "had",
}


def normalize_tokens(text):
    text = (text or "").lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9\u0600-\u06FF\s-]", " ", text)

    tokens = []
    seen = set()

    for word in text.split():
        if len(word) <= 2 or word in STOP_WORDS:
            continue
        if word not in seen:
            seen.add(word)
            tokens.append(word)

    return tokens


def jaccard(tokens_a, tokens_b):
    a = set(tokens_a)
    b = set(tokens_b)

    if not a or not b:
        return 0.0

    return len(a & b) / len(a | b)


# =========================================================
# EVENT DETECTION
# =========================================================

def detect_event_type(title):
    text = title.lower()

    drone = re.search(
        r"\b(drone|drones|uav|uavs|uas|unmanned aerial|shahed)\b",
        text,
    )

    missile = re.search(
        r"\b(missile|missiles|ballistic missile|ballistic missiles|"
        r"cruise missile|cruise missiles|rocket|rockets)\b",
        text,
    )

    interception = re.search(
        r"\b(intercept|intercepts|intercepted|interception|"
        r"shot down|downed|air defense|air defence|destroyed incoming)\b",
        text,
    )

    alert = re.search(
        r"\b(air raid|air-raid|siren|sirens|take shelter|warning|alert)\b",
        text,
    )

    if drone and interception:
        return "Drone interception"

    if missile and interception:
        return "Missile interception"

    if drone:
        return "Drone"

    if missile:
        return "Missile"

    if alert:
        return "Air-raid alert"

    return None


def event_type_ar(event_type):
    return {
        "Drone": "مسيّرة",
        "Missile": "صاروخ",
        "Drone interception": "اعتراض مسيّرة",
        "Missile interception": "اعتراض صاروخ",
        "Air-raid alert": "إنذار خطر جوي",
    }.get(event_type, event_type)


# =========================================================
# LOCATION
# =========================================================

def detect_location(title):
    text = title.lower()

    for arabic_name, aliases in LOCATIONS:
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias.lower())}\b", text):
                return arabic_name

    return "غير محدد"


# =========================================================
# SOURCE SCORING
# =========================================================

def is_official_domain(domain):
    domain = (domain or "").lower()

    patterns = [
        r"(^|\.)gov(\.|$)",
        r"(^|\.)mil(\.|$)",
        r"\.gov\.[a-z]{2,3}$",
        r"\.mil\.[a-z]{2,3}$",
        r"\.gov\.sa$",
    ]

    return any(re.search(pattern, domain) for pattern in patterns)


def source_score(article):
    domain = (article.get("domain") or "").lower()
    title = (article.get("title") or "").lower()

    score = 0
    official = False

    if is_official_domain(domain):
        score += 100
        official = True
    elif domain in TIER_1_DOMAINS:
        score += 55
    else:
        score += 20

    if re.search(
        r"\b(defense ministry|defence ministry|ministry of defense|"
        r"ministry of defence|civil defense|civil defence|"
        r"official statement|military says|government says)\b",
        title,
    ):
        score += 20

    if re.search(r"\b(video|footage|geolocated|confirmed by)\b", title):
        score += 10

    if re.search(
        r"\b(reportedly|alleged|unconfirmed|rumor|rumour|claims|claimed)\b",
        title,
    ):
        score -= 15

    return score, official


# =========================================================
# GDELT
# =========================================================

def parse_gdelt_json(response):
    """Parse JSON regardless of a misleading Content-Type header."""
    body = response.text.strip()

    if not body:
        print("GDELT returned an empty response.")
        return None

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        print("GDELT returned non-JSON response:")
        print(body[:1500])
        return None


def request_gdelt(query, retry_on_429=True):
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": GDELT_MAX_RECORDS,
        "timespan": GDELT_TIMESPAN,
        "sort": "DateDesc",
    }

    headers = {
        "User-Agent": "GlobalThreatRadar/1.1",
        "Accept": "application/json,text/plain,*/*",
    }

    try:
        response = requests.get(
            GDELT_URL,
            params=params,
            headers=headers,
            timeout=40,
        )
    except requests.RequestException as exc:
        print("GDELT connection error:")
        print(exc)
        return None

    print("GDELT URL:", response.url)
    print("GDELT status:", response.status_code)
    print("GDELT content-type:", response.headers.get("content-type"))

    if response.status_code == 429 and retry_on_429:
        print(f"GDELT rate limited us. Waiting {GDELT_RETRY_SECONDS} seconds...")
        time.sleep(GDELT_RETRY_SECONDS)
        return request_gdelt(query, retry_on_429=False)

    if response.status_code != 200:
        print("GDELT HTTP error:")
        print(response.text[:1000])
        return None

    return parse_gdelt_json(response)


def fetch_articles():
    primary_query = (
        '(missile OR "ballistic missile" OR "cruise missile" '
        'OR drone OR UAV OR UAS OR "air raid") '
        '(launch OR launched OR attack OR strike OR intercept '
        'OR intercepted OR interception OR siren OR warning OR alert)'
    )

    data = request_gdelt(primary_query)

    # Fallback only if the main query failed.
    # Wait first so we respect the API rate limit seen in the live response.
    if data is None:
        print(f"Waiting {GDELT_RETRY_SECONDS} seconds before fallback...")
        time.sleep(GDELT_RETRY_SECONDS)

        print("Trying fallback GDELT query...")

        fallback_query = 'missile OR drone OR UAV OR "air raid"'
        data = request_gdelt(fallback_query)

    if data is None:
        print("GDELT unavailable. Exiting cleanly.")
        return []

    articles = data.get("articles", [])

    if not isinstance(articles, list):
        print("Unexpected GDELT response structure:")
        print(str(data)[:1500])
        return []

    print("Articles received:", len(articles))
    return articles


# =========================================================
# STATE / DEDUPLICATION
# =========================================================

def load_state():
    if not STATE_FILE.exists():
        return {"events": []}

    try:
        with STATE_FILE.open("r", encoding="utf-8") as file:
            state = json.load(file)

        if not isinstance(state.get("events"), list):
            state["events"] = []

        return state
    except Exception as exc:
        print("Could not read state file:", exc)
        return {"events": []}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    temporary = STATE_FILE.with_suffix(".tmp")

    with temporary.open("w", encoding="utf-8") as file:
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2,
        )

    temporary.replace(STATE_FILE)


def prune_state(state):
    cutoff = time.time() - (48 * 60 * 60)

    state["events"] = [
        event
        for event in state.get("events", [])
        if event.get("timestamp", 0) >= cutoff
    ]

    return state


# =========================================================
# ARTICLE PROCESSING
# =========================================================

def prepare_articles(raw_articles):
    output = []

    for article in raw_articles:
        title = str(article.get("title") or "").strip()
        url = str(article.get("url") or "").strip()
        domain = str(article.get("domain") or "").lower().strip()

        if not title or not url:
            continue

        detected_type = detect_event_type(title)

        if detected_type is None:
            continue

        score, official = source_score(article)

        output.append({
            "title": title,
            "url": url,
            "domain": domain,
            "event_type": detected_type,
            "location": detect_location(title),
            "tokens": normalize_tokens(title),
            "score": score,
            "official": official,
            "date": article.get("seendate", ""),
        })

    return output


# =========================================================
# CLUSTERING
# =========================================================

def cluster_articles(articles):
    clusters = []

    for article in articles:
        selected_cluster = None

        for cluster in clusters:
            if cluster["event_type"] != article["event_type"]:
                continue

            cluster_location = cluster["location"]
            article_location = article["location"]

            location_compatible = (
                cluster_location == article_location
                or cluster_location == "غير محدد"
                or article_location == "غير محدد"
            )

            if not location_compatible:
                continue

            similarities = [
                jaccard(existing["tokens"], article["tokens"])
                for existing in cluster["articles"]
            ]

            if max(similarities, default=0) >= 0.34:
                selected_cluster = cluster
                break

        if selected_cluster:
            selected_cluster["articles"].append(article)

            if (
                selected_cluster["location"] == "غير محدد"
                and article["location"] != "غير محدد"
            ):
                selected_cluster["location"] = article["location"]

        else:
            clusters.append({
                "event_type": article["event_type"],
                "location": article["location"],
                "articles": [article],
            })

    return clusters


# =========================================================
# CONFIDENCE
# =========================================================

def analyze_cluster(cluster):
    articles = cluster["articles"]

    domains = {
        article["domain"]
        for article in articles
        if article["domain"]
    }

    best = max(articles, key=lambda article: article["score"])
    score = best["score"]
    official = any(article["official"] for article in articles)

    if not official:
        if len(domains) >= 3:
            score += 40
        elif len(domains) == 2:
            score += 30

    score = max(0, min(100, score))

    if official or score >= 75:
        confidence = "Confirmed"
    elif score >= 50:
        confidence = "Likely"
    else:
        confidence = "Unverified"

    tokens = normalize_tokens(
        " ".join(article["title"] for article in articles)
    )

    return {
        "event_type": cluster["event_type"],
        "location": cluster["location"],
        "articles": articles,
        "domains": list(domains),
        "best": best,
        "score": score,
        "confidence": confidence,
        "tokens": tokens,
    }


# =========================================================
# DUPLICATE CHECK
# =========================================================

def find_previous_event(event, state):
    now = time.time()

    for previous in state["events"]:
        if now - previous.get("timestamp", 0) > 2 * 60 * 60:
            continue

        if previous.get("event_type") != event["event_type"]:
            continue

        previous_location = previous.get("location", "غير محدد")

        if (
            previous_location != event["location"]
            and previous_location != "غير محدد"
            and event["location"] != "غير محدد"
        ):
            continue

        similarity = jaccard(
            previous.get("tokens", []),
            event["tokens"],
        )

        if similarity >= 0.42:
            return previous

    return None


# =========================================================
# TELEGRAM
# =========================================================

def confidence_ar(confidence):
    return {
        "Confirmed": "مؤكد",
        "Likely": "مرجح",
        "Unverified": "غير مؤكد",
    }[confidence]


def confidence_icon(confidence):
    return {
        "Confirmed": "✅",
        "Likely": "🟡",
        "Unverified": "⚪",
    }[confidence]


def create_message(event, update=False):
    best = event["best"]
    header = "🔄 تحديث حالة حدث سابق" if update else "🚨 تنبيه OSINT"

    sorted_articles = sorted(
        event["articles"],
        key=lambda article: article["score"],
        reverse=True,
    )

    sources = []

    for number, article in enumerate(sorted_articles[:3], start=1):
        domain = article["domain"] or "Source"
        sources.append(
            f"{number}) {domain}\n{article['url']}"
        )

    event_time = (
        best.get("date")
        or datetime.now(timezone.utc).isoformat()
    )

    return (
        f"{header}\n\n"
        f"📍 الموقع: {event['location']}\n"
        f"🛰️ النوع: {event_type_ar(event['event_type'])}\n"
        f"{confidence_icon(event['confidence'])} الحالة: "
        f"{confidence_ar(event['confidence'])} / {event['confidence']}\n"
        f"📊 درجة الثقة: {event['score']}/100\n"
        f"🧩 مصادر مستقلة: {len(event['domains'])}\n"
        f"🕒 وقت المصدر: {event_time}\n\n"
        f"📰 {best['title']}\n\n"
        f"المصادر:\n"
        + "\n".join(sources)
        + "\n\n"
        "⚠️ هذه معلومات OSINT من مصادر مفتوحة وليست بديلاً عن "
        "تنبيهات الجهات الرسمية أو الدفاع المدني."
    )


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing")

    endpoint = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }

    response = requests.post(
        endpoint,
        json=payload,
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            "Telegram API error: "
            f"{response.status_code}\n{response.text}"
        )

    print("Telegram message sent successfully.")


# =========================================================
# MAIN
# =========================================================

def main():
    print("=================================")
    print("Global Threat Radar v1.1 starting...")
    print("=================================")

    state = prune_state(load_state())

    raw_articles = fetch_articles()

    if not raw_articles:
        print("No articles returned.")
        return

    articles = prepare_articles(raw_articles)

    print("Relevant security articles:", len(articles))

    clusters = cluster_articles(articles)

    print("Detected event clusters:", len(clusters))

    events = [
        analyze_cluster(cluster)
        for cluster in clusters
    ]

    events.sort(
        key=lambda event: (
            CONFIDENCE_RANK[event["confidence"]],
            event["score"],
            len(event["domains"]),
        ),
        reverse=True,
    )

    alerts_sent = 0
    state_changed = False

    minimum_rank = CONFIDENCE_RANK.get(MIN_CONFIDENCE, 1)

    for event in events:
        if CONFIDENCE_RANK[event["confidence"]] < minimum_rank:
            continue

        previous = find_previous_event(event, state)
        update = False

        if previous:
            previous_confidence = previous.get(
                "confidence",
                "Unverified",
            )

            if (
                CONFIDENCE_RANK[event["confidence"]]
                <= CONFIDENCE_RANK[previous_confidence]
            ):
                continue

            update = True

        message = create_message(event, update)

        if DRY_RUN:
            print("\n==========================")
            print(message)
            print("==========================\n")
        else:
            send_telegram(message)

        now = time.time()

        if previous:
            previous.update({
                "confidence": event["confidence"],
                "score": event["score"],
                "tokens": event["tokens"],
                "timestamp": now,
                "title": event["best"]["title"],
            })
        else:
            state["events"].append({
                "event_type": event["event_type"],
                "location": event["location"],
                "confidence": event["confidence"],
                "score": event["score"],
                "tokens": event["tokens"],
                "timestamp": now,
                "title": event["best"]["title"],
            })

        state_changed = True
        alerts_sent += 1

        if alerts_sent >= MAX_ALERTS:
            break

    if state_changed:
        save_state(state)

    print("=================================")
    print("Finished.")
    print("Alerts sent:", alerts_sent)
    print("Minimum confidence:", MIN_CONFIDENCE)
    print("=================================")


if __name__ == "__main__":
    main()
