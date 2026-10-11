import sys

from common import read, write


def project(records):
    for row in records:
        features = {
            side: {key: row[side][key] for key in ("caption", "schema", "properties")}
            for side in ("left", "right")
        }
        yield {
            key: value
            for key, value in {**row, "features": features}.items()
            if key not in {"left", "right"}
        }


write(project(read(sys.argv[1])), sys.argv[2])
