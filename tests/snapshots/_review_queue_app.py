from langusta.tui.app import LangustaApp
from langusta.tui.screens.review_queue import ReviewQueueScreen
class TestApp(LangustaApp):
    def on_mount(self):
        self.push_screen(ReviewQueueScreen())
app = TestApp()
