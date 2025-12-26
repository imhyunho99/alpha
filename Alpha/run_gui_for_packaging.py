# This script is a dedicated entry point for PyInstaller.
# It ensures that the 'alpha' package is correctly added to the Python path
# so that PyInstaller can find all the necessary modules.

import sys
import os

# Add the project directory (the parent of this script's directory) to the Python path.
# This allows for absolute imports like 'from alpha.gui import start_gui'.
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from alpha.gui import start_gui

if __name__ == '__main__':
    start_gui()
