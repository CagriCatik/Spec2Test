"""Two exporters, two names.

Both used to be called ``save_xlsx``; the package re-exported the flat row
exporter under that name and the specification exporter under another. A
caller that imported ``save_xlsx`` and passed a TestSpecification got a
TypeError deep inside pandas. The names now say which is which; the old
ones remain as deprecated aliases.
"""
from .specification import (
    SHEETS,
    save_json,
    save_markdown,
    save_specification_xlsx,
    to_dict,
    to_json,
    to_markdown,
)
from .tabular import COLUMNS, save_csv, save_rows_xlsx, summarise_rows

#: deprecated: ``save_xlsx`` meant the row exporter here. Prefer
#: ``save_rows_xlsx``; it will be removed.
save_xlsx = save_rows_xlsx

__all__ = [
    "COLUMNS",
    "SHEETS",
    "save_csv",
    "save_json",
    "save_markdown",
    "save_rows_xlsx",
    "save_specification_xlsx",
    "save_xlsx",
    "summarise_rows",
    "to_dict",
    "to_json",
    "to_markdown",
]
