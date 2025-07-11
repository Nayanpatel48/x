from flask import Flask, request, jsonify
from flask_cors import CORS
import time, json, feedparser, urllib.parse, re, os
from urllib.parse import urlparse
from pathlib import Path
import urllib.request

# Initialize Flask app and CORS
app = Flask(__name__)
CORS(app)

# Constants
STORAGE_PATH = "ai_updates.json"
FETCH_LIMIT = 20

# Trusted and blocked domains
BLOCKED_DOMAINS = {"example-fake-news.com", "india-tabloid.co.in"}
TRUSTED_DOMAINS = {
    "openai.com": 10,
    "arxiv.org": 9,
    "nature.com": 9
}

# Load saved articles archive
def load_archive():
    if Path(STORAGE_PATH).is_file():
        return json.loads(Path(STORAGE_PATH).read_text())
    return {}

# Save articles archive
def save_archive(data):
    Path(STORAGE_PATH).write_text(json.dumps(data, indent=2))

# Extract domain from URL
def domain_of(url):
    return urlparse(url).netloc.lower()

# Split text into sentences
def sentence_split(text):
    parts = re.split(r'(?<=[.!?]) +', text.strip())
    return [p.replace('\n', ' ').strip() for p in parts if p.strip()]

# Clean HTML tags and extract summary
def extract_summary(text, max_sentences=3):
    clean_text = re.sub(r"<.*?>", "", text)
    sents = sentence_split(clean_text)
    return " ".join(sents[:max_sentences]) if sents else text

# Fetch publish time from page metadata
def fetch_page_pubtime_head(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            head_html = r.read(4096).decode(errors="ignore")
        match = re.search(r'property="article:published_time" content="([^"]+)"', head_html)
        if match:
            return match.group(1)
    except Exception as e:
        print(f"Error fetching pub time for {url}: {e}")
    return None

# Score each article
def score_item(title, summary, query, domain, pub_rss, pub_page):
    q_words = set(re.findall(r"\w+", query.lower()))
    content_words = re.findall(r"\w+", (title + " " + summary).lower())
    match_score = (sum(1 for w in content_words if w in q_words) / len(content_words)) * 60 if content_words else 0
    domain_score = (TRUSTED_DOMAINS.get(domain, 5) / 10) * 20

    try:
        pub_time = time.mktime(time.strptime(pub_page or pub_rss, "%Y-%m-%dT%H:%M:%SZ"))
        hours_ago = (time.time() - pub_time) / 3600
        recency_score = max(0, (72 - hours_ago) / 72 * 20)
    except Exception as e:
        print(f"Date parse error: {e}")
        recency_score = 10

    total_score = round(match_score + domain_score + recency_score, 1)
    return total_score

# Build RSS feed URLs for a given query
def build_rss_urls(query):
    q = urllib.parse.quote_plus(query)
    return [
        f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en",
        "http://export.arxiv.org/rss/cs.AI"
    ]

# Fetch, parse, score, and rank articles
def fetch_and_rank(query):
    seen, items = load_archive(), []
    for feed_url in build_rss_urls(query):
        d = feedparser.parse(feed_url)
        if not d.entries:
            continue
        for entry in d.entries[:FETCH_LIMIT]:
            link = entry.link
            domain = domain_of(link)
            if domain in BLOCKED_DOMAINS:
                continue

            uid = entry.get("id", link)
            title = entry.title.strip()
            pub_rss = entry.get("published", time.strftime("%Y-%m-%dT%H:%M:%SZ"))
            summary = extract_summary(getattr(entry, "summary", "") or title)
            pub_page = fetch_page_pubtime_head(link) if domain in TRUSTED_DOMAINS else None
            score = score_item(title, summary, query, domain, pub_rss, pub_page)

            items.append({
                "title": title,
                "link": link,
                "source": domain,
                "published_rss": pub_rss,
                "published_page": pub_page or "unknown",
                "summary": summary,
                "score": score
            })

            if uid not in seen:
                seen[uid] = {"title": title, "link": link, "published": pub_rss}

    save_archive(seen)
    return sorted(items, key=lambda x: (x["score"], x["published_rss"]), reverse=True)[:20]

# API route: search news
@app.route('/api/search', methods=['GET'])
def search_news():
    query = request.args.get('q')
    if not query:
        return jsonify({"error": "Missing query parameter `q`"}), 400
    results = fetch_and_rank(query)
    return jsonify(results)

# Health check/home route
@app.route('/')
def home():
    return "✅ AI News API is running."

# Run the app
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
