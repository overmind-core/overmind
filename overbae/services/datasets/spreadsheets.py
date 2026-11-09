import datetime as dt
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from overbae.core.errors import InputValidationError

MAX_BYTES = 100 * 1024**2
MAX_EXPANDED_BYTES = 512 * 1024**2
LOCATION = "_overmind_spreadsheet"


class SpreadsheetError(InputValidationError):
    pass


def rows(path):
    try:
        with ZipFile(path) as archive:
            if sum(member.file_size for member in archive.infolist()) > MAX_EXPANDED_BYTES:
                raise SpreadsheetError("Expanded XLSX contents exceed 512 MiB.")
        # Staged uploads have no extension; use a stream. Formulas remain source text.
        with path.open("rb") as stream:
            workbook = load_workbook(stream, read_only=True, data_only=False, keep_links=False)
            try:
                for sheet in workbook:
                    sheet.reset_dimensions()
                    iterator = sheet.iter_rows()
                    header = next(iterator, ())
                    if not header or all(cell.value is None for cell in header):
                        if any(cell.value is not None for row in iterator for cell in row):
                            raise SpreadsheetError(
                                f"Sheet {sheet.title!r} has no first-row header."
                            )
                        continue
                    names = []
                    for index, cell in enumerate(header, 1):
                        if cell.data_type in {"f", "e"}:
                            raise SpreadsheetError(
                                f"Sheet {sheet.title!r} has a nonliteral header."
                            )
                        name = str(cell.value).strip() if cell.value is not None else ""
                        name = name or f"column_{index}"
                        if name.lower() in {n.lower() for n in names} | {LOCATION}:
                            raise SpreadsheetError(
                                f"Sheet {sheet.title!r} has an ambiguous column {name!r}."
                            )
                        names.append(name)
                    for number, cells in enumerate(iterator, 2):
                        if len(cells) > len(names) and any(
                            cell.value is not None for cell in cells[len(names) :]
                        ):
                            raise SpreadsheetError(
                                f"Sheet {sheet.title!r} row {number} is wider than its header."
                            )
                        record = dict.fromkeys(names)
                        types = {}
                        for name, cell in zip(names, cells, strict=False):
                            value = cell.value
                            if isinstance(value, (dt.datetime, dt.date, dt.time)):
                                types[name] = type(value).__name__
                                value = value.isoformat()
                            elif isinstance(value, dt.timedelta):
                                types[name] = "duration_seconds"
                                value = value.total_seconds()
                            elif cell.data_type in {"f", "e"}:
                                types[name] = "formula" if cell.data_type == "f" else "error"
                            record[name] = value
                        record[LOCATION] = {
                            "sheet": sheet.title,
                            "row": number,
                            "cell_types": types,
                        }
                        yield record
            finally:
                workbook.close()
    except SpreadsheetError:
        raise
    except (BadZipFile, InvalidFileException, OSError, ValueError, KeyError, SyntaxError) as exc:
        raise SpreadsheetError("The file is not a readable XLSX workbook.") from exc
