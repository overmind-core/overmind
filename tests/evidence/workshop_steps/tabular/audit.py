import sys

from common import read, write


def audit(records):
    for row in records:
        if row["Survived"] not in (0, 1):
            raise ValueError("Unknown historical survival target.")
        yield {
            **row,
            "needs_review": row["Age"] is None or row["Embarked"] is None,
            "passenger_group": str(row["Ticket"]),
        }


write(audit(read(sys.argv[1])), sys.argv[2])
