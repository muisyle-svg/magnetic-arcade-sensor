import os
import sys

if getattr(sys, "_MEIPASS", None):
    # The local Python distribution has a damaged Tkinter hook, so the
    # package is bundled as data instead of being placed in the PYZ archive.
    # Put the extracted bundle directory first so tkinter imports from it and
    # directs Tcl/Tk to the bundled script libraries.
    sys.path.insert(0, sys._MEIPASS)
    from pathlib import Path

    extracted_directory = Path(sys._MEIPASS)
    tcl_directory = extracted_directory / "tcl" / "tcl8.6"
    tk_directory = extracted_directory / "tcl" / "tk8.6"
    os.environ["TCL_LIBRARY"] = tcl_directory.as_posix()
    os.environ["TK_LIBRARY"] = tk_directory.as_posix()
