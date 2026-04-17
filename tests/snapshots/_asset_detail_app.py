from langusta.tui.app import LangustaApp
from langusta.tui.screens.asset_detail import AssetDetailScreen
AID = 1
class TestApp(LangustaApp):
    def on_mount(self):
        self.push_screen(AssetDetailScreen(asset_id=AID))
app = TestApp()
