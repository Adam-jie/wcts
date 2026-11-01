"""Open the prepared example with the supplied WCTP desktop editor."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path.cwd()))
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication
from src.gui.main_window import MainWindow


app = QApplication(sys.argv)
app.setApplicationName('WCTP')
window = MainWindow()
window.showMaximized()
case = Path.cwd() / 'repository/taskboard_demo/cases/case_TaskboardFlow.yaml'
QTimer.singleShot(0, lambda: window._load_case_file(str(case)))
sys.exit(app.exec_())
