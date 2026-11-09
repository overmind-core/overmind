import datetime as dt


class ManifestBuilder:
    def __init__(self):
        self.kinds = {}
        self.inexact_floats = set()

    def add(self, row):
        for name, value in row.items():
            name = str(name)
            kinds = self.kinds.setdefault(name, set())
            if value is None or (isinstance(value, (float, dt.datetime)) and value != value):
                continue
            if isinstance(value, (dict, list, tuple)):
                kinds.add("json")
            elif isinstance(value, bool):
                kinds.add("boolean")
            elif isinstance(value, int):
                if not -(2**63) <= value < 2**63:
                    kinds.add("json")
                else:
                    kinds.add("integer")
                    if int(float(value)) != value:
                        self.inexact_floats.add(name)
            elif isinstance(value, float):
                kinds.add("number")
            elif isinstance(value, dt.datetime):
                kinds.add("datetime")
            else:
                kinds.add("string")

    def manifest(self):
        result = []
        for name, kinds in self.kinds.items():
            if "json" in kinds or ("number" in kinds and name in self.inexact_floats):
                kind = "json"
            elif kinds and kinds <= {"integer", "number"}:
                kind = "number" if "number" in kinds else "integer"
            else:
                kind = next(iter(kinds)) if len(kinds) == 1 else "json" if kinds else "string"
            result.append({"name": name, "type": kind})
        return result


def normalize_json_rows(records):
    """Match table materialization at a replay step boundary without a Parquet round trip."""
    builder = ManifestBuilder()
    for row in records:
        builder.add(row)
    manifest = builder.manifest()
    return [
        {
            column["name"]: (
                float(row[column["name"]])
                if column["type"] == "number" and row.get(column["name"]) is not None
                else row.get(column["name"])
            )
            for column in manifest
        }
        for row in records
    ]
