import sys

from common import canonical, read, write


def messages(records):
    for row in records:
        features = {
            key: row[key] for key in ("Pclass", "Sex", "Age", "SibSp", "Parch", "Fare", "Embarked")
        }
        yield {
            **row,
            "messages": [
                {
                    "role": "system",
                    "content": "Predict historical Titanic passenger survival. Return 0 or 1. Missing values remain unknown.",
                },
                {"role": "user", "content": canonical(features)},
                {"role": "assistant", "content": str(row["Survived"])},
            ],
        }


write(messages(sorted(read(sys.argv[1]), key=lambda row: row["source_row"])), sys.argv[2])
