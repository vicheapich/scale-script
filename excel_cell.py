"""
Write a scale weight into the selected cell of the Excel workbook that is
already open, then move the selection one row down (Windows only).

Click the first cell of the weight column in Excel; each parcel then fills
the next row of that column. Uses Excel's COM interface (pywin32), so it
never types into another window by mistake.

Needs pywin32 (pip install -r requirements.txt).
"""

import time

RETRIES = 10           # Excel rejects COM calls while a cell is being edited
RETRY_DELAY = 0.3      # seconds between tries (about 3 s in total)


class ExcelError(Exception):
    pass


def to_number(value):
    """Scale text such as "2.70" or "2700" -> 2.7 / 2700 (a number cell in Excel)."""
    w = float(value)
    return int(w) if w.is_integer() else w


def write_selected_cell(value):
    """Put the number into Excel's active cell and select the cell below it.

    Returns a description like "Book1 / Sheet1 / B2". Raises ExcelError.
    """
    import pywintypes
    import win32com.client

    last = None
    for _ in range(RETRIES):
        try:
            xl = win32com.client.GetActiveObject("Excel.Application")
        except pywintypes.com_error:
            raise ExcelError("Excel is not running - open your workbook first")
        try:
            cell = xl.ActiveCell
            if cell is None:
                raise ExcelError("no cell selected - open a workbook and click a cell")
            sheet = cell.Worksheet
            where = f"{sheet.Parent.Name} / {sheet.Name} / {cell.Address.replace('$', '')}"
            cell.Value = to_number(value)
            sheet.Cells(cell.Row + 1, cell.Column).Select()  # Cells() is 1-based, absolute
            return where
        except pywintypes.com_error as e:
            last = e   # usually "Call was rejected by callee": a cell is being edited
            time.sleep(RETRY_DELAY)
    raise ExcelError("Excel is busy (is a cell being edited? press Enter or Esc in Excel): "
                     f"{last.args[1] if last and len(last.args) > 1 else last}")
