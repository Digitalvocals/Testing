import asyncio
import tkinter as tk
from tkinter import ttk
from twitchAPI.twitch import Twitch
from twitchAPI.helper import first
from twitchAPI.type import TwitchAPIException
from collections import namedtuple
import os
import json
import time
from dotenv import load_dotenv
import threading
import queue
from ttkbootstrap.constants import *

# --- Load environment variables from specified file ---
load_dotenv(dotenv_path='twitch.key.ring.env')

APP_ID = os.getenv("TWITCH_APP_ID")
APP_SECRET = os.getenv("TWITCH_APP_SECRET")

# --- File paths ---
GAME_LIST_FILE = r"C:\Users\digit\Documents\TwitchScrapper\my_games.txt"
CACHE_FILE = r"C:\Users\digit\Documents\TwitchScrapper\cache.json"

# --- Scoring weights and cache settings ---
WEIGHT_ENGAGEMENT = 0.80
WEIGHT_DISCOVERY = 0.20
CACHE_STALE_TIME_SECONDS = 900  # 15 minutes

# Named tuple for clear data structure
GameData = namedtuple('GameData', ['name', 'viewers', 'channels', 'engagement_score', 'discovery_score', 'overall_score'])

def calculate_scores(viewers, channels):
    """Calculates the individual scores for a game based on current Twitch data."""
    engagement_score = viewers / channels if channels > 0 else 0
    if viewers > 0:
        popularity_factor = min(viewers, 50000) / 50000.0
        channel_factor = min(channels, 250) / 250.0
        discovery_score = (popularity_factor + channel_factor) / 2
    else:
        discovery_score = 0
    overall_score = (
        (engagement_score * WEIGHT_ENGAGEMENT) +
        (discovery_score * WEIGHT_DISCOVERY)
    )
    return engagement_score, discovery_score, overall_score

def load_cache():
    """Loads game data from a JSON cache file."""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'r') as f:
            try:
                raw_cache = json.load(f)
                return {
                    name: {"timestamp": data.get("timestamp"), "data": GameData(**data["data"])}
                    for name, data in raw_cache.items()
                }
            except (json.JSONDecodeError, KeyError):
                return {}
    return {}

def save_cache(cache):
    """Saves game data to a JSON cache file."""
    with open(CACHE_FILE, 'w') as f:
        cache_to_save = {
            name: {"timestamp": cache[name]["timestamp"], "data": cache[name]["data"]._asdict()}
            for name in cache
        }
        json.dump(cache_to_save, f, indent=4)

def is_cache_stale(cache_entry):
    """Checks if a cache entry is older than CACHE_STALE_TIME_SECONDS."""
    if cache_entry and "timestamp" in cache_entry:
        return (time.time() - cache_entry["timestamp"]) > CACHE_STALE_TIME_SECONDS
    return True

async def get_twitch_game_data(twitch: Twitch, game_name: str, game_id: str, results_queue: queue.Queue):
    """Fetches Twitch data for a specific game."""
    try:
        streams_result = twitch.get_streams(game_id=game_id)
        viewers = 0
        channels = 0
        async for stream in streams_result:
            viewers += stream.viewer_count
            channels += 1
        engagement, discovery, overall = calculate_scores(viewers, channels)
        game_data = GameData(game_name, viewers, channels, engagement, discovery, overall)
        results_queue.put(("fresh", game_data))
    except Exception as e:
        results_queue.put(("error", f"Error fetching detailed data for {game_name}: {e}"))

async def get_games_from_iterator(iterator):
    """Helper function to collect all games from an async_generator."""
    games = []
    async for game in iterator:
        games.append(game)
    return games

async def filter_games(twitch: Twitch, game_list: list) -> list:
    """Filters games by their existence and potential viewership on Twitch,
    handling API limits by batching requests."""
    found_games_with_ids = {}
    chunk_size = 100
    chunks = [game_list[i:i + chunk_size] for i in range(0, len(game_list), chunk_size)]
    tasks = [get_games_from_iterator(twitch.get_games(names=chunk)) for chunk in chunks]
    all_game_results = await asyncio.gather(*tasks)
    for game_list_from_chunk in all_game_results:
        for game in game_list_from_chunk:
            found_games_with_ids[game.name] = game.id
    filtered_games = []
    for game in game_list:
        if game in found_games_with_ids:
            filtered_games.append((game, found_games_with_ids[game]))
    return filtered_games

async def run_analysis(app, results_queue: queue.Queue):
    """The core logic to fetch and process Twitch data with caching and incremental updates."""
    # Corrected: Pass color name and bootstyle constant separately
    results_queue.put(("status", "Status: Starting analysis...", "blue", INFO))

    if not APP_ID or not APP_SECRET:
        results_queue.put(("error", "Error: Twitch credentials not found. Check your twitch.key.ring.env file."))
        return

    try:
        with open(GAME_LIST_FILE, 'r') as f:
            my_games = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        results_queue.put(("error", f"Error: The file '{GAME_LIST_FILE}' was not found."))
        return

    try:
        twitch = await Twitch(APP_ID, APP_SECRET)
    except Exception as e:
        results_queue.put(("error", f"Error: Failed to connect to Twitch API. {e}"))
        return

    cached_data = load_cache()
    games_to_fetch_fresh = []
    
    for game_name in my_games:
        if game_name in cached_data and not is_cache_stale(cached_data[game_name]):
            results_queue.put(("cached", cached_data[game_name]['data']))
        else:
            games_to_fetch_fresh.append(game_name)

    if games_to_fetch_fresh:
        # Corrected: Pass color name and bootstyle constant separately
        results_queue.put(("status", f"Fetching fresh data for {len(games_to_fetch_fresh)} games...", "blue", INFO))
        filtered_games_with_ids = await filter_games(twitch, games_to_fetch_fresh)
        
        tasks = [get_twitch_game_data(twitch, name, game_id, results_queue) for name, game_id in filtered_games_with_ids]
        
        await asyncio.gather(*tasks)
        
    results_queue.put("complete", None)

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.root = self
        self.title("Twitch Stream Recommender")
        self.geometry("800x600")
        self.game_data_list = []
        self.results_queue = queue.Queue()
        self.thread = None

        try:
            from ttkbootstrap import Style
            self.style = Style(theme="superhero") 
        except ImportError:
            self.style = ttk.Style(self)

        self.create_widgets()

    def create_widgets(self):
        self.header_frame = ttk.Frame(self.root, padding=20)
        self.header_frame.pack(fill=tk.X)

        self.title_label = ttk.Label(self.header_frame, text="Twitch Stream Recommender", font=("Helvetica", 20, "bold"), foreground="#9146FF")
        self.title_label.pack(side=tk.LEFT)
        
        self.run_button = ttk.Button(self.header_frame, text="Run Analysis", command=self.start_analysis_thread, bootstyle="primary")
        self.run_button.pack(side=tk.RIGHT)

        self.status_label = ttk.Label(self.root, text="Status: Ready", padding=(20, 10), anchor="w", bootstyle="info")
        self.status_label.pack(fill=tk.X)

        self.table_frame = ttk.Frame(self.root, padding=10)
        self.table_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        columns = ("Rank", "Game", "Overall Score", "Viewers", "Channels", "Engagement", "Discovery")
        self.tree = ttk.Treeview(self.table_frame, columns=columns, show="headings", bootstyle="dark")

        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, anchor=tk.CENTER, width=100)
            if col not in ("Rank", "Game"):
                self.tree.heading(col, command=lambda c=col: self.treeview_sort_column(c))
        
        self.tree.pack(fill=tk.BOTH, expand=True)

        self.scrollbar = ttk.Scrollbar(self.table_frame, orient=tk.VERTICAL, command=self.tree.yview, bootstyle="light")
        self.tree.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def treeview_sort_column(self, col):
        """Sorts the Treeview data by the specified column."""
        column_map = {
            'Overall Score': 'overall_score',
            'Viewers': 'viewers',
            'Channels': 'channels',
            'Engagement': 'engagement_score',
            'Discovery': 'discovery_score'
        }
        key = column_map.get(col)
        
        if not key:
            return

        reverse_sort = getattr(self, 'reverse_sort', False)
        reverse_sort = not reverse_sort
        setattr(self, 'reverse_sort', reverse_sort)

        self.game_data_list.sort(key=lambda item: getattr(item, key), reverse=reverse_sort)
        self.populate_table(self.game_data_list)
        self.tree.heading(col, text=f"{col} {'▼' if reverse_sort else '▲'}")

    def start_analysis_thread(self):
        """Starts the analysis in a separate thread to prevent GUI freezing."""
        self.status_label.config(text="Status: Starting analysis...", foreground="blue", bootstyle="info")
        self.run_button.config(state=tk.DISABLED)
        self.clear_table()
        self.thread = threading.Thread(target=lambda: asyncio.run(run_analysis(self, self.results_queue)), daemon=True)
        self.thread.start()
        self.poll_queue()

    def poll_queue(self):
        """Polls the results queue for updates from the worker thread."""
        try:
            while True:
                task_type, *data = self.results_queue.get_nowait()
                if task_type == "cached" or task_type == "fresh":
                    game_data = data[0]
                    self.game_data_list.append(game_data)
                    self.populate_table_row(game_data)
                elif task_type == "status":
                    self.update_status(*data)
                elif task_type == "complete":
                    self.update_gui_after_analysis()
                    break
                elif task_type == "error":
                    self.update_gui_error(*data)
                    break
        except queue.Empty:
            pass
        finally:
            if self.thread and self.thread.is_alive():
                self.after(100, self.poll_queue)
            else:
                self.update_status("Status: Analysis complete!", "green", SUCCESS)
                self.run_button.config(state=tk.NORMAL)


    def update_status(self, message, color="blue", bootstyle="info"):
        """Updates the status label."""
        self.status_label.config(text=message, bootstyle=bootstyle)
        self.status_label.config(foreground=color)

    def update_gui_after_analysis(self):
        self.game_data_list.sort(key=lambda x: x.overall_score, reverse=True)
        self.populate_table(self.game_data_list)
        self.update_status("Status: Analysis complete!", "green", SUCCESS)
        self.run_button.config(state=tk.NORMAL)

    def update_gui_error(self, message):
        self.status_label.config(text=message, foreground="red", bootstyle="danger")
        self.run_button.config(state=tk.NORMAL)

    def populate_table_row(self, game_data):
        self.tree.insert("", tk.END, values=(
            "",
            game_data.name,
            f"{game_data.overall_score:.2f}",
            f"{game_data.viewers:,}",
            game_data.channels,
            f"{game_data.engagement_score:.2f}",
            f"{game_data.discovery_score:.2f}"
        ))

    def update_table_row(self, game_data):
        # The incremental update logic is now handled in populate_table_row
        # and doesn't need a separate update function.
        pass

    def clear_table(self):
        self.game_data_list = []
        for item in self.tree.get_children():
            self.tree.delete(item)

    def populate_table(self, games_list):
        self.clear_table()
        for rank, game in enumerate(games_list):
            self.tree.insert("", tk.END, values=(
                rank + 1,
                game.name,
                f"{game.overall_score:.2f}",
                f"{game.viewers:,}",
                game.channels,
                f"{game.engagement_score:.2f}",
                f"{game.discovery_score:.2f}"
            ))

if __name__ == '__main__':
    app = App()
    app.mainloop()
