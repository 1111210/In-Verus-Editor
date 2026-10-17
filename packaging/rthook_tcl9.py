"""Point Tcl/Tk 9 at the library archives embedded in its Windows DLLs."""

import os


# Windows CPython 3.14 bundles the Tcl/Tk 9 script libraries inside the DLLs.
# PyInstaller detects those zipfs paths but does not emit data directories, so
# the frozen process must select them explicitly before tkinter is imported.
os.environ["TCL_LIBRARY"] = "//zipfs:/lib/tcl/tcl_library"
os.environ["TK_LIBRARY"] = "//zipfs:/lib/tk/tk_library"
