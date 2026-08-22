import os
import sys


if getattr(sys, "_MEIPASS", None):
    # The local Python distribution has a damaged Tkinter hook, so the
    # package is bundled as data instead of being placed in the PYZ archive.
    # Put the extracted bundle directory first so tkinter imports from it.
    sys.path.insert(0, sys._MEIPASS)
    os.environ.setdefault(
        "TCL_LIBRARY",
        os.path.join(sys._MEIPASS, "tcl", "tcl8.6"),
    )
    os.environ.setdefault(
        "TK_LIBRARY",
        os.path.join(sys._MEIPASS, "tcl", "tk8.6"),
    )
