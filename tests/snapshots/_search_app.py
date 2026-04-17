from langusta.tui.app import LangustaApp
from langusta.tui.screens.search import SearchScreen
INITIAL = 'printer'
class TestApp(LangustaApp):
    def on_mount(self):
        self.push_screen(SearchScreen(initial_query=INITIAL))
app = TestApp()
