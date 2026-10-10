import json
import sys

FEATURES = ("Pclass", "Sex", "Age", "SibSp", "Parch", "Fare", "Cabin", "Embarked")
with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    for line in source:
        row = json.loads(line)
        assert type(row["Survived"]) is int and row["Survived"] in (0, 1)
        row["model_evidence"] = {key: row[key] for key in FEATURES}
        row["review_flags"] = [f"missing_{key}" for key in FEATURES if row[key] in (None, "")]
        output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
