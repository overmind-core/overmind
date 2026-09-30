from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def project_references(value, project_id):
    if isinstance(value, dict):
        return {key: project_references(item, project_id) for key, item in value.items()}
    if isinstance(value, list):
        return [project_references(item, project_id) for item in value]
    if isinstance(value, str) and value.startswith("overmind://"):
        parsed = urlsplit(value)
        query = dict(parse_qsl(parsed.query))
        query["project_id"] = str(project_id)
        return urlunsplit(parsed._replace(query=urlencode(query)))
    return value
