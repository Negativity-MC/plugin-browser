import os
import httpx
import asyncio
from pathlib import Path
from typing import List, Optional
import json
import traceback

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Input, Button, Static, Markdown, DataTable, Label, ListItem, ListView, Checkbox, Select
from textual.message import Message
from textual import on, work
from textual.binding import Binding

# --- Constants & Config ---
MODRINTH_API_URL = "https://api.modrinth.com/v2"
USER_AGENT = "gemini-cli/plugin-browser/1.0 (gemini-cli-agent)"

# --- API Client ---
class ModrinthAPI:
    def __init__(self):
        self.client = httpx.AsyncClient(
            base_url=MODRINTH_API_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=10.0
        )

    async def search_plugins(self, query: str, loaders: List[str], limit: int = 20, offset: int = 0) -> List[dict]:
        """Search for plugins on Modrinth."""
        try:
            # Base facet: plugins only
            facets = [["project_type:plugin"]]
            
            # Add loaders facet (OR logic within the list)
            if loaders:
                loader_facet = [f"categories:{loader}" for loader in loaders]
                facets.append(loader_facet)
            
            facets_json = json.dumps(facets)
            
            response = await self.client.get(
                "/search",
                params={
                    "query": query,
                    "facets": facets_json,
                    "limit": limit,
                    "offset": offset,
                    "index": "relevance" 
                }
            )
            response.raise_for_status()
            return response.json().get("hits", [])
        except Exception as e:
            return []

    async def get_versions(self, project_slug: str) -> List[dict]:
        """Get versions for a specific project."""
        try:
            response = await self.client.get(f"/project/{project_slug}/version")
            response.raise_for_status()
            return response.json()
        except Exception:
            return []

    async def close(self):
        await self.client.aclose()

# --- UI Components ---

class PluginDetails(Static):
    """Widget to display plugin details."""
    
    current_plugin: Optional[dict] = None
    versions_map: dict = {}

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Label("Select a plugin to view details", id="details-title")
            yield Markdown("", id="details-desc")
            yield Label("Versions:", classes="section-header")
            yield Select([], prompt="Select a version", id="version-select")
            yield Button("Download", variant="primary", id="btn-download", disabled=True)

    def show_plugin(self, plugin: dict, versions: List[dict], allowed_loaders: List[str]):
        self.current_plugin = plugin
        self.versions_map = {}
        
        # Update Title
        title = self.query_one("#details-title", Label)
        author = plugin.get('author', 'Unknown')
        title.update(f"{plugin['title']} (by {author})")
        
        # Update Description
        desc = self.query_one("#details-desc", Markdown)
        # Prefer body (full markdown), fallback to description (short), then default text
        full_text = plugin.get('body') or plugin.get('description') or 'No description available.'
        desc.update(full_text)

        # Filter versions
        select_options = []
        allowed_set = set(allowed_loaders)
        
        for version in versions:
            v_loaders = set(version.get('loaders', []))
            if allowed_set and not (v_loaders & allowed_set):
                continue
                
            version_id = version['id']
            name = version.get('name', version['version_number'])
            files = version.get('files', [])
            if not files:
                continue
                
            self.versions_map[version_id] = {
                'url': files[0]['url'],
                'filename': files[0]['filename']
            }
            
            display_loaders = ", ".join(v_loaders)
            label = f"{name} ({display_loaders})"
            select_options.append((label, version_id))
        
        # Update Select
        sel = self.query_one("#version-select", Select)
        sel.set_options(select_options)
        
        # Reset Download Button
        btn = self.query_one("#btn-download", Button)
        btn.disabled = True

    @on(Select.Changed)
    def on_version_select(self, event: Select.Changed):
        btn = self.query_one("#btn-download", Button)
        if event.value:
            btn.disabled = False
        else:
            btn.disabled = True

class ModrinthBrowser(App):
    CSS = """
    /* --- Global --- */
    Screen {
        layout: vertical;
        background: #1a1b26;
        color: #a9b1d6;
    }

    /* --- Header/Footer --- */
    Header {
        background: #16161e;
        color: #7aa2f7;
        dock: top;
        height: 1;
    }

    Footer {
        background: #16161e;
        color: #565f89;
        dock: bottom;
        height: 1;
    }

    /* --- Search Section --- */
    #search-bar {
        dock: top;
        height: auto;
        padding: 1 2;
        background: #1a1b26;
        border-bottom: solid #414868;
    }

    Input {
        background: #24283b;
        border: none;
        color: #c0caf5;
        padding: 0 1;
        width: 100%;
    }
    Input:focus {
        border: solid #7aa2f7;
    }

    #filter-container {
        layout: horizontal;
        height: auto;
        margin-top: 1;
        background: #1a1b26;
    }
    
    Checkbox {
        margin-right: 2;
        color: #565f89;
    }
    Checkbox.-on {
        color: #bb9af7;
        text-style: bold;
    }

    /* --- Main Content --- */
    #main-content {
        layout: horizontal;
        height: 1fr;
    }

    /* --- Results Pane --- */
    #results-pane {
        width: 40%;
        border-right: solid #414868;
        background: #1a1b26;
    }

    DataTable {
        background: #1a1b26;
        border: none;
        scrollbar-gutter: stable;
    }
    
    DataTable > .datatable--header {
        background: #24283b;
        color: #7aa2f7;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: #3d59a1;
        color: #ffffff;
    }

    DataTable > .datatable--hover {
        background: #292e42;
    }

    #btn-load-more {
        margin: 1;
        background: #24283b;
        color: #7aa2f7;
        border: none;
    }
    #btn-load-more:hover {
        background: #3d59a1;
        color: #ffffff;
    }
    #btn-load-more:disabled {
        background: #1a1b26;
        color: #565f89;
    }

    /* --- Details Pane --- */
    #details-pane {
        width: 60%;
        padding: 1 2;
        background: #1a1b26;
        overflow-y: scroll;
    }

    #details-title {
        text-style: bold;
        color: #9ece6a;
        padding-bottom: 1;
        border-bottom: solid #414868;
        margin-bottom: 1;
        text-align: left;
    }

    #details-desc {
        color: #c0caf5;
        padding-bottom: 1;
    }

    .section-header {
        margin-top: 2;
        margin-bottom: 1;
        text-style: bold;
        color: #bb9af7;
    }

    Select {
        background: #24283b;
        border: none;
        margin-bottom: 1;
    }

    #btn-download {
        background: #7aa2f7;
        color: #1a1b26;
        text-style: bold;
        margin-top: 2;
        border: none;
    }
    #btn-download:hover {
        background: #2ac3de;
    }
    #btn-download:disabled {
        background: #24283b;
        color: #565f89;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
    ]

    def __init__(self):
        super().__init__()
        self.api = ModrinthAPI()
        self.download_dir = self._determine_download_dir()
        self.current_offset = 0
        self.current_query = ""
        self.search_timer: Optional[asyncio.TimerHandle] = None

    def _determine_download_dir(self) -> Path:
        cwd = Path.cwd()
        if cwd.name == "plugins":
            return cwd
        
        plugins_dir = cwd / "plugins"
        if plugins_dir.exists() and plugins_dir.is_dir():
            return plugins_dir
        
        plugins_dir.mkdir(exist_ok=True)
        return plugins_dir

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Input(placeholder="Search for plugins...", id="search-input"),
            Container(
                Checkbox("Paper", value=True, id="chk-paper"),
                Checkbox("Spigot", value=True, id="chk-spigot"),
                Checkbox("Bukkit", value=True, id="chk-bukkit"),
                Checkbox("Purpur", value=True, id="chk-purpur"),
                Checkbox("Fabric", value=False, id="chk-fabric"),
                id="filter-container"
            ),
            id="search-bar"
        )
        yield Container(
            Container(
                DataTable(id="results-table"),
                Button("Load More", variant="default", id="btn-load-more", disabled=True),
                id="results-pane"
            ),
            PluginDetails(id="details-pane"),
            id="main-content"
        )
        yield Footer()

    def on_mount(self):
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("Plugin Name", "Downloads")
        self.query_one("#search-input").focus()

    def action_focus_search(self):
        self.query_one("#search-input").focus()

    def get_active_loaders(self) -> List[str]:
        loaders = []
        if self.query_one("#chk-paper", Checkbox).value: loaders.append("paper")
        if self.query_one("#chk-spigot", Checkbox).value: loaders.append("spigot")
        if self.query_one("#chk-bukkit", Checkbox).value: loaders.append("bukkit")
        if self.query_one("#chk-purpur", Checkbox).value: loaders.append("purpur")
        if self.query_one("#chk-fabric", Checkbox).value: loaders.append("fabric")
        return loaders

    @on(Input.Changed)
    def on_input_changed(self, event: Input.Changed):
        query = event.value.strip()
        
        # Cancel existing timer
        if self.search_timer:
            self.search_timer.stop()
            
        if not query:
            self.query_one(DataTable).clear()
            self.query_one("#btn-load-more", Button).disabled = True
            return

        # Debounce search for 200ms
        self.search_timer = self.set_timer(0.2, lambda: self.trigger_auto_search(query))

    def trigger_auto_search(self, query: str):
        if query != self.current_query:
            self.current_query = query
            self.current_offset = 0
            self.perform_search(reset=True)

    async def on_input_submitted(self, event: Input.Submitted):
        # We can still keep this for manual force refresh if needed
        query = event.value.strip()
        if query:
            if self.search_timer:
                self.search_timer.stop()
            self.current_query = query
            self.current_offset = 0
            self.perform_search(reset=True)

    @work(exclusive=True)
    async def perform_search(self, reset: bool = False):
        try:
            table = self.query_one(DataTable)
            if reset:
                table.clear()
            
            loaders = self.get_active_loaders()
            limit = 20
            hits = await self.api.search_plugins(self.current_query, loaders, limit=limit, offset=self.current_offset)
            
            for hit in hits:
                try:
                    table.add_row(hit['title'], str(hit['downloads']), key=hit['slug'])
                except Exception:
                    pass # Row likely exists
            
            # Enable load more
            btn = self.query_one("#btn-load-more", Button)
            if len(hits) < limit:
                btn.disabled = True
                if not hits and reset:
                     self.notify("No results found.", severity="warning")
            else:
                btn.disabled = False
        except Exception as e:
            with open("error.log", "w") as f:
                f.write(traceback.format_exc())
            self.notify(f"Search error: {e}", severity="error")
            
    @on(Button.Pressed)
    async def on_button_click(self, event: Button.Pressed):
        btn_id = event.button.id
        
        if btn_id == "btn-load-more":
            self.current_offset += 20
            self.perform_search(reset=False)
            
        elif btn_id == "btn-download":
            # Download logic
            details = self.query_one(PluginDetails)
            select = details.query_one("#version-select", Select)
            if select.value:
                version_id = select.value
                file_info = details.versions_map.get(version_id)
                if file_info:
                    self.download_file(file_info['url'], file_info['filename'])

    @on(DataTable.RowSelected)
    async def on_plugin_selected(self, event: DataTable.RowSelected):
        plugin_slug = event.row_key.value
        loaders = self.get_active_loaders()
        self.fetch_plugin_details(plugin_slug, loaders)

    @work(exclusive=True)
    async def fetch_plugin_details(self, plugin_slug: str, loaders: List[str]):
        versions = await self.api.get_versions(plugin_slug)
        try:
             project_resp = await self.api.client.get(f"/project/{plugin_slug}")
             project_resp.raise_for_status()
             project_data = project_resp.json()
             self.query_one(PluginDetails).show_plugin(project_data, versions, loaders)
        except Exception as e:
            self.notify(f"Error loading details: {e}", severity="error")

    @work(thread=True)
    def download_file(self, url: str, filename: str):
        dest = self.download_dir / filename
        self.call_from_thread(self.notify, f"Downloading {filename}...")
        try:
            with httpx.Client() as client:
                resp = client.get(url)
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(resp.content)
            self.call_from_thread(self.notify, f"Saved to {dest}", severity="information")
        except Exception as e:
            self.call_from_thread(self.notify, f"Download failed: {e}", severity="error")

    async def on_unmount(self):
        await self.api.close()

if __name__ == "__main__":
    app = ModrinthBrowser()
    app.run()
