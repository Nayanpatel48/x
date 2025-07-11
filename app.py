#!/usr/bin/env python3
from flask import Flask, request, jsonify
from flask_cors import CORS
import time, json, feedparser, urllib.parse, re
from urllib.parse import urlparse
from pathlib import Path
import urllib.request

app = Flask(__name__)
CORS(app)

STORAGE_PATH = "ai_updates.json"
FETCH_LIMIT  = 20

BLOCKED_DOMAINS = {"example-fake-news.com", "india-tabloid.co.in"}
TRUSTED_DOMAINS = {"openai.com": 10, "arxiv.org": 9, "nature.com": 9}

def load_archive():
    if Path(STORAGE_PATH).is_file():
        return json.loads(Path(STORAGE_PATH).read_text())
    return {}

def save_archive(data):
    Path(STORAGE_PATH).write_text(json.dumps(data, indent=2))

def domain_of(url): return urlparse(url).netloc.lower()

def sentence_split(text):
    parts = re.split(r'(?<=[.!?]) +', text.strip())
    return [p.replace('\n', ' ').strip() for p in parts if p.strip()]

def extract_summary(text, max_sentences=3):
    sents = sentence_split(re.sub(r"<.*?>", "", text))
    return " ".join(sents[:max_sentences]) if sents else text

def fetch_page_pubtime_head(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            head_html = r.read(4096).decode(errors="ignore")
        match = re.search(r'property="article:published_time" content="([^"]+)"', head_html)
        if match: return match.group(1)
    except: pass
    return None

def score_item(title, summary, query, domain, pub_rss, pub_page):
    q_words = set(re.findall(r"\w+", query.lower()))
    content_words = re.findall(r"\w+", (title + " " + summary).lower())
    match_score = (sum(1 for w in content_words if w in q_words) / len(content_words)) * 60 if content_words else 0
    domain_score = (TRUSTED_DOMAINS.get(domain, 5) / 10) * 20
    try:
        pub_time = time.mktime(time.strptime(pub_page or pub_rss, "%Y-%m-%dT%H:%M:%SZ"))
        hours_ago = (time.time() - pub_time) / 3600
        recency_score = max(0, (72-hours_ago)/72 * 20)
    except: recency_score = 10
    return round(match_score + domain_score + recency_score, 1)

def build_rss_urls(query):
    q = urllib.parse.quote_plus(query)
    return [f"https://news.google.com/rss/search?q={q}",
            "http://export.arxiv.org/rss/cs.AI"]

def fetch_and_rank(query):
    seen, items = load_archive(), []
    for feed_url in build_rss_urls(query):
        d = feedparser.parse(feed_url)
        for entry in d.entries[:FETCH_LIMIT]:
            link, domain = entry.link, domain_of(entry.link)
            if domain in BLOCKED_DOMAINS: continue
            uid, title = entry.get("id", link), entry.title.strip()
            pub_rss = entry.get("published", time.strftime("%Y-%m-%dT%H:%M:%SZ"))
            summary = extract_summary(getattr(entry, "summary", "") or title)
            pub_page = fetch_page_pubtime_head(link) if domain in TRUSTED_DOMAINS else None
            score = score_item(title, summary, query, domain, pub_rss, pub_page)
            items.append({
                "title": title, "link": link, "source": domain,
                "published_rss": pub_rss, "published_page": pub_page or "unknown",
                "summary": summary, "score": score
            })
            if uid not in seen:
                seen[uid] = {"title": title, "link": link, "published": pub_rss}
    save_archive(seen)
    return sorted(items, key=lambda x: (x["score"], x["published_rss"]), reverse=True)[:20]

@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "No query provided."}), 400
    results = fetch_and_rank(query)
    return jsonify(results)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
