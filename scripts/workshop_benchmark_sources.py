import csv
import gzip
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import requests

ROOT = Path("output/workshop-benchmark")
RAW = ROOT / "sources"
RAW.mkdir(parents=True, exist_ok=True)


def fetch(name, url):
    path = RAW / name
    if not path.exists():
        r = requests.get(url, timeout=180)
        r.raise_for_status()
        path.write_bytes(r.content)
    payload = path.read_bytes()
    ledger = ROOT / "downloads.json"
    records = json.loads(ledger.read_text()) if ledger.exists() else {}
    records[name] = {
        "url": url,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    ledger.write_text(json.dumps(records, indent=2) + "\n")
    return payload


def hashed(items, count, key):
    return sorted(items, key=lambda row: hashlib.sha256(str(key(row)).encode()).hexdigest())[:count]


def save(name, rows):
    path = ROOT / (name + ".jsonl")
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows)
    )
    return path, len(rows)


cases = []


def case(name, title, intent, files, count, source, licence, selection="Full source population"):
    cases.append(
        {
            "key": name,
            "name": title,
            "objective": intent,
            "files": [str(p) for p in files],
            "source_rows": count,
            "source_url": source,
            "licence": licence,
            "selection": selection,
            "intent": "eval" if name == "squad" else "train",
        }
    )
    (ROOT / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
    print(json.dumps({"case": name, "rows": count, "files": [str(p) for p in files]}), flush=True)


url = "https://raw.githubusercontent.com/allisonhorst/palmerpenguins/main/inst/extdata/penguins.csv"
payload = fetch("penguins.csv", url)
p = ROOT / "penguins.csv"
p.write_bytes(payload)
case(
    "penguins",
    "Palmer penguins",
    "I want to train a decision model to identify penguin species from these observations.",
    [p],
    len(list(csv.DictReader(io.StringIO(payload.decode())))),
    "https://github.com/allisonhorst/palmerpenguins",
    "CC0",
)

z = zipfile.ZipFile(
    io.BytesIO(fetch("wine.zip", "https://archive.ics.uci.edu/static/public/186/wine+quality.zip"))
)
paths = []
count = 0
for kind in ("red", "white"):
    payload = z.read(f"winequality-{kind}.csv")
    p = ROOT / f"winequality-{kind}.csv"
    p.write_bytes(payload)
    paths.append(p)
    count += len(list(csv.DictReader(io.StringIO(payload.decode()), delimiter=";")))
case(
    "wine",
    "Red and white wine quality",
    "I want to train a decision model to score wine quality from laboratory measurements.",
    paths,
    count,
    "https://archive.ics.uci.edu/dataset/186/wine+quality",
    "CC BY 4.0",
)

z = zipfile.ZipFile(
    io.BytesIO(
        fetch("dry-bean.zip", "https://archive.ics.uci.edu/static/public/602/dry+bean+dataset.zip")
    )
)
name = next(n for n in z.namelist() if n.endswith(".xlsx"))
p = ROOT / "dry-bean.xlsx"
p.write_bytes(z.read(name))
case(
    "beans",
    "Dry bean varieties",
    "I want to train a decision model to identify bean varieties from their measured shape.",
    [p],
    len(pd.read_excel(p)),
    "https://archive.ics.uci.edu/dataset/602/dry+bean+dataset",
    "CC BY 4.0",
)

z = zipfile.ZipFile(
    io.BytesIO(
        fetch("sms.zip", "https://archive.ics.uci.edu/static/public/228/sms+spam+collection.zip")
    )
)
name = next(n for n in z.namelist() if n.endswith("SMSSpamCollection"))
records = []
for index, line in enumerate(z.read(name).decode("utf-8-sig").splitlines()):
    label, text = line.split("\t", 1)
    records.append({"label": label, "text": text, "original_line": index + 1})
p, count = save("sms", records)
case(
    "sms",
    "SMS spam collection",
    "I want to train a decision model to recognise spam messages.",
    [p],
    count,
    "https://archive.ics.uci.edu/dataset/228/sms+spam+collection",
    "CC BY 4.0",
    "Full source, lossless TSV-to-JSONL transport encoding with named label/text fields",
)

z = zipfile.ZipFile(
    io.BytesIO(
        fetch("bikes.zip", "https://archive.ics.uci.edu/static/public/275/bike+sharing+dataset.zip")
    )
)
name = next(n for n in z.namelist() if n.endswith("hour.csv"))
p = ROOT / "bike-hours.csv"
p.write_bytes(z.read(name))
case(
    "bikes",
    "Hourly bike rentals",
    "I want to train a decision model to predict whether an hour will have at least 100 rentals, using information available before that hour begins.",
    [p],
    len(pd.read_csv(p)),
    "https://archive.ics.uci.edu/dataset/275/bike+sharing+dataset",
    "CC BY 4.0",
)

z = zipfile.ZipFile(
    io.BytesIO(
        fetch("retail.zip", "https://archive.ics.uci.edu/static/public/352/online+retail.zip")
    )
)
name = next(n for n in z.namelist() if n.endswith(".xlsx"))
frame = pd.read_excel(io.BytesIO(z.read(name)))
ids = hashed(list(frame.CustomerID.dropna().astype(int).unique()), 500, key=lambda x: x)
frame = frame[frame.CustomerID.isin(ids)].copy()
frame["InvoiceDate"] = frame.InvoiceDate.dt.strftime("%Y-%m-%d %H:%M:%S")
p = ROOT / "retail-customers.csv"
frame.to_csv(p, index=False)
case(
    "retail",
    "Online retail customer histories",
    "I want to train a decision model to predict whether a customer will place another order within 30 days of their first completed order.",
    [p],
    len(frame),
    "https://archive.ics.uci.edu/dataset/352/online+retail",
    "CC BY 4.0",
    "All transaction lines for 500 customers selected by smallest SHA-256 of original customer ID; complete observed histories retained",
)

raw = json.loads(
    fetch("squad-dev-v1.1.json", "https://rajpurkar.github.io/SQuAD-explorer/dataset/dev-v1.1.json")
)
articles = hashed(raw["data"], 5, key=lambda x: x["title"])
p, count = save("squad", articles)
case(
    "squad",
    "SQuAD reading comprehension",
    "I want evaluation data for an assistant that answers questions using the supplied passage.",
    [p],
    count,
    "https://rajpurkar.github.io/SQuAD-explorer/",
    "CC BY-SA 4.0",
    "Five complete articles chosen by smallest SHA-256 of title; all paragraphs, questions and original answer alternatives retained",
)

raw = fetch(
    "dolly.jsonl",
    "https://huggingface.co/datasets/databricks/databricks-dolly-15k/resolve/main/databricks-dolly-15k.jsonl",
)
rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
rows = hashed(rows, 1000, key=lambda r: json.dumps(r, sort_keys=True))
p, count = save("dolly", rows)
case(
    "dolly",
    "Dolly instruction responses",
    "I want to fine-tune an assistant to follow instructions and use the provided context.",
    [p],
    count,
    "https://huggingface.co/datasets/databricks/databricks-dolly-15k",
    "CC BY-SA 3.0",
    "1,000 unchanged records chosen by smallest SHA-256 of canonical original record",
)

payload = fetch(
    "hh-helpful-train.jsonl.gz",
    "https://huggingface.co/datasets/Anthropic/hh-rlhf/resolve/main/helpful-base/train.jsonl.gz",
)
rows = [json.loads(line) for line in gzip.decompress(payload).splitlines() if line.strip()]
rows = hashed(rows, 500, key=lambda r: json.dumps(r, sort_keys=True))
p, count = save("preference", rows)
case(
    "preference",
    "Human helpfulness preferences",
    "I want to train a decision model to choose the more helpful assistant response.",
    [p],
    count,
    "https://huggingface.co/datasets/Anthropic/hh-rlhf",
    "MIT",
    "500 unchanged helpful-base training pairs chosen by smallest SHA-256 of canonical original pair",
)

raw = json.loads(
    fetch(
        "hatexplain.json",
        "https://raw.githubusercontent.com/hate-alert/HateXplain/master/Data/dataset.json",
    )
)
divisions = json.loads(
    fetch(
        "hatexplain-splits.json",
        "https://raw.githubusercontent.com/hate-alert/HateXplain/master/Data/post_id_divisions.json",
    )
)
roles = {key: role for role, keys in divisions.items() for key in keys}
rows = [
    {"post_id": key, **value, "official_split": roles[key]}
    for key, value in raw.items()
    if key in roles
]
rows = hashed(rows, 1000, key=lambda r: r["post_id"])
p, count = save("annotations", rows)
case(
    "annotations",
    "HateXplain annotator judgments",
    "I want to train a decision model to reproduce the distribution of annotator judgments about a post.",
    [p],
    count,
    "https://github.com/hate-alert/HateXplain",
    "MIT",
    "1,000 posts chosen by smallest SHA-256 of original post ID; all annotations and official split membership retained",
)
