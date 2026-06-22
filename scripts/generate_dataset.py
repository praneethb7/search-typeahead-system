"""Generate a reproducible search-query dataset (>= 100,000 rows) -> data/queries.csv.

WHY GENERATED (and not a raw download):
  - It is fully reproducible (fixed seed) so anyone re-running this gets the *exact* dataset,
    which keeps the performance numbers comparable.
  - It models real search traffic: a few head queries are hugely popular and there is a long tail
    of rare ones (a Zipf-like / power-law distribution), which is exactly what makes typeahead
    ranking interesting.

HOW:
  We start from an open-source-style list of head terms (brands, products, topics) and "aspect"
  modifiers (price, review, near me, 2024, ...). We combine head x modifier to synthesize queries
  like "iphone 15 price" and assign each a count from a Zipf curve (popular head + popular
  aspect => big count). The seed word list is bundled below so there is no network dependency.

Run:
  python scripts/generate_dataset.py            # -> data/queries.csv  (>= 100k rows)
  python scripts/generate_dataset.py --rows 250000 --out data/queries.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import random

# --- Head terms: chosen to span many starting letters so prefix demos are rich. ----------------
HEADS = [
    # tech / brands
    "iphone", "ipad", "imac", "ipod", "airpods", "android", "samsung galaxy", "google pixel",
    "oneplus", "xiaomi", "macbook", "macbook pro", "macbook air", "windows", "linux", "ubuntu",
    "dell laptop", "hp laptop", "lenovo thinkpad", "asus rog", "acer aspire", "nintendo switch",
    "playstation", "xbox", "smart tv", "bluetooth speaker", "wireless earbuds", "smartwatch",
    "kindle", "gopro", "drone", "nvidia rtx", "amd ryzen", "intel core",
    # programming / education
    "python", "java", "javascript", "typescript", "react", "angular", "vue", "node js",
    "django", "flask", "spring boot", "kubernetes", "docker", "aws", "azure", "system design",
    "data structures", "algorithms", "machine learning", "deep learning", "sql", "mongodb",
    "redis", "kafka", "git", "leetcode", "interview questions", "english grammar", "calculus",
    "physics", "chemistry", "biology",
    # food / home
    "pizza", "burger", "biryani", "pasta", "sushi", "coffee", "green tea", "protein powder",
    "air fryer", "microwave oven", "refrigerator", "washing machine", "vacuum cleaner",
    "office chair", "standing desk", "led bulb", "ceiling fan", "water purifier", "mattress",
    "sofa set", "dining table",
    # shopping / fashion
    "running shoes", "sneakers", "jeans", "t shirt", "winter jacket", "backpack", "sunglasses",
    "wrist watch", "perfume", "lipstick", "face wash", "shampoo", "sunscreen",
    # travel
    "flight tickets", "hotels", "goa", "manali", "paris", "dubai", "singapore", "tokyo",
    "new york", "london", "bali", "train ticket", "visa application", "passport",
    # finance / work
    "stock market", "mutual funds", "credit card", "home loan", "car insurance", "income tax",
    "resume template", "cover letter", "remote jobs", "salary calculator", "cryptocurrency",
    "bitcoin", "ethereum",
    # auto / health / entertainment
    "tesla", "electric car", "bike", "car tyres", "engine oil", "yoga", "meditation",
    "weight loss", "gym workout", "blood pressure", "vitamin d", "netflix", "spotify",
    "youtube", "cricket score", "football", "movie tickets", "concert tickets",
    # misc starting letters for prefix coverage
    "umbrella", "quartz watch", "zebra crossing", "violin", "keyboard", "guitar", "camera lens",
    "printer", "router", "power bank", "headphones", "gaming mouse", "monitor", "ssd drive",
    "graphics card", "solar panel", "electric scooter", "treadmill", "dumbbells", "yoga mat",
]

# --- Aspect modifiers: the "what about it" part of a query. ------------------------------------
ASPECTS = [
    "price", "review", "reviews", "specifications", "specs", "features", "comparison", "vs",
    "near me", "online", "offer", "offers", "deal", "deals", "discount", "coupon", "buy",
    "for sale", "cheap", "best", "top", "cost", "emi", "warranty", "manual", "guide", "tutorial",
    "course", "classes", "certification", "free download", "download", "app", "login", "support",
    "customer care", "service center", "repair", "replacement", "spare parts", "accessories",
    "case", "cover", "charger", "battery", "screen", "size", "weight", "color", "models",
    "latest model", "new launch", "release date", "in india", "in usa", "in uk", "rating",
    "complaints", "problems", "alternatives", "for beginners", "for students", "for office",
    "for home", "for gaming", "second hand", "refurbished", "wholesale", "exchange", "installment",
    "delivery", "stock", "availability", "image", "images", "video", "demo", "unboxing",
    "benchmark", "lifespan", "maintenance", "installation", "setup", "configuration", "vs others",
    "pros and cons", "buying guide", "2021", "2022", "2023", "2024", "2025", "this year",
]

TAILS = ["", "online", "near me", "best", "cheap", "today"]


def build_modifiers() -> list[str]:
    mods: list[str] = []
    for a in ASPECTS:
        mods.append(a)
        for t in TAILS:
            if t:
                mods.append(f"{a} {t}")
    # de-duplicate while preserving order
    return list(dict.fromkeys(mods))


def generate(target_rows: int, seed: int = 42) -> dict[str, int]:
    rng = random.Random(seed)
    mods = build_modifiers()
    rows: dict[str, int] = {}

    # 1) head terms alone get the biggest counts (classic Zipf: count ~ 1/rank).
    for rank, head in enumerate(HEADS, start=1):
        rows[head] = max(int(2_000_000 / rank), 1000)

    # 2) head + modifier. Popularity decays with both the head rank and the modifier rank,
    #    with a little deterministic jitter so ties are broken naturally.
    for hrank, head in enumerate(HEADS, start=1):
        head_base = max(int(2_000_000 / hrank), 500)
        for mrank, mod in enumerate(mods, start=1):
            count = head_base / (mrank + 1.0)
            count *= rng.uniform(0.4, 1.6)
            rows[f"{head} {mod}"] = max(int(count), 1)
            if len(rows) >= target_rows:
                break
        if len(rows) >= target_rows:
            break

    # 3) Safety top-up: if the lists were a touch small, add realistic numbered/version variants
    #    until we comfortably clear the target. Keeps the >= 100k guarantee independent of edits.
    i = 0
    while len(rows) < target_rows:
        head = HEADS[i % len(HEADS)]
        version = (i // len(HEADS)) + 1
        rows[f"{head} model {version}"] = max(int(rng.uniform(1, 50)), 1)
        i += 1

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the search-query dataset.")
    parser.add_argument("--rows", type=int, default=100_000, help="minimum number of queries")
    parser.add_argument("--out", default="data/queries.csv", help="output CSV path")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (reproducibility)")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    rows = generate(args.rows, seed=args.seed)

    # Write sorted by count desc so the file is human-inspectable (most popular first).
    ordered = sorted(rows.items(), key=lambda kv: kv[1], reverse=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["query", "count"])
        writer.writerows(ordered)

    print(f"Wrote {len(ordered):,} queries to {args.out}")
    print("Top 5 by count:")
    for q, c in ordered[:5]:
        print(f"  {c:>10,}  {q}")


if __name__ == "__main__":
    main()
