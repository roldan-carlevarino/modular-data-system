import feedparser
import psycopg2
import hashlib
import math
from datetime import datetime, timezone
import os


TOP_PER_CATEGORY = 10
GLOBAL_THRESHOLD = 3

DEFAULT_WEIGHT = 1.0


def freshness_factor(hours):
    return math.exp(-hours / 36)

def hours_since(pubdate):
    if not pubdate:
        return 0
    delta = datetime.now(timezone.utc) - pubdate
    return delta.total_seconds() / 3600

def compute_hash(title, link):
    raw = (title + link).encode()
    return hashlib.sha256(raw).hexdigest()


def compute_scores(text):
    scores = {
        "ml": 0,
        "markets": 0,
        "quant": 0,
        "politics": 0
    }

    for kw in ["stochastic optimization", "foundation model", "reinforcement", "neural network", "fine-tuning"]:
        if kw in text:
            scores["ml"] += 3

    for kw in ["lasso", "llm"]:
        if kw in text:
            scores["ml"] += 1.5
    scores["ml"] = min(scores["ml"], 10)

    for kw in ["interest rate", "inflation", "monetary policy"]:
        if kw in text:
            scores["markets"] += 2.5

    scores["markets"] = min(scores["markets"], 10)

    for kw in ["cointegration", "time series", "regression"]:
        if kw in text:
            scores["quant"] += 4

    scores["quant"] = min(scores["quant"], 10)

    for kw in ["legislation", "policy", "executive order", "spain", "eu"]:
        if kw in text:
            scores["politics"] += 3

    scores["politics"] = min(scores["politics"], 10)

    return scores


def main():
    conn = psycopg2.connect(os.getenv("TASKS_URL"), sslmode="require")
    cur = conn.cursor()

    cur.execute("""
        SELECT id, name, url, base_weight
        FROM rss_sources
        WHERE is_active = TRUE
    """)

    sources = cur.fetchall()
    candidates = []


    for source_id, name, url, base_weight in sources:
        feed = feedparser.parse(url)

        for entry in feed.entries:
            text = (entry.title + " " + entry.get("summary", "")).lower()

            pubdate = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pubdate = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

            hours = hours_since(pubdate)
            freshness = freshness_factor(hours)

            scores = compute_scores(text)


            weight = float(base_weight) if base_weight else DEFAULT_WEIGHT

            for k in scores:
                scores[k] *= weight     
                scores[k] *= freshness

            if max(scores.values()) < GLOBAL_THRESHOLD:
                continue


            candidates.append({
                "source_id": source_id,
                "title": entry.title,
                "link": entry.link,
                "hash": compute_hash(entry.title, entry.link),
                "scores": scores
            })


    by_category = {
        "ml": [],
        "markets": [],
        "quant": [],
        "politics": []
    }

    for art in candidates:
        max_cat = max(art["scores"], key=art["scores"].get)
        by_category[max_cat].append(art)

    selected_by_category = []
    for cat, articles in by_category.items():
        articles.sort(key=lambda x: x["scores"][cat], reverse=True) #ordenarpor individual category
        top_10 = articles[:TOP_PER_CATEGORY]
        
        for i, art in enumerate(top_10):
            art["top_category"] = cat
            art["category_rank"] = i + 1
            selected_by_category.append(art)

    selected_by_category.sort(key=lambda x: max(x["scores"].values()), reverse=True)
    

    for i, art in enumerate(selected_by_category):
        art["global_rank"] = i + 1


    
    cur.execute("DELETE FROM rss_articles")

    for art in selected_by_category:
        cur.execute("""
            INSERT INTO rss_articles
            (source_id, title, link,
             score_ml, score_markets,
             score_quant, score_politics,
             content_hash,
             global_rank,
             top_category, 
             category_rank)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            art["source_id"],
            art["title"],
            art["link"],
            art["scores"]["ml"],
            art["scores"]["markets"],
            art["scores"]["quant"],
            art["scores"]["politics"],
            art["hash"],
            art["global_rank"],
            art["top_category"],
            art["category_rank"]
        ))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    main()