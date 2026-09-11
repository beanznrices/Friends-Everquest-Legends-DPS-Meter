"""Tkinter front-end.

The per-combatant panel is a ttk.Treeview: a full raid scales without new
widgets being built per member, columns are sortable by click, and
selecting a row drives a second "ability breakdown" table for that
combatant.
"""

from __future__ import annotations

import csv
import os
import threading
import time
import tkinter as tk
from tkinter import font, ttk
from typing import Dict, List, Optional

from . import audio, config
from .model import Segment
from .state import MeterState, replay_full_history
from .watcher import LogWatcher

COLUMNS = [
    ("name", "Name", 190, "w"),
    ("dmg", "Damage", 90, "e"),
    ("dps", "DPS", 70, "e"),
    ("pct", "% of Total", 130, "w"),
    ("hits", "Hits", 55, "e"),
    ("crit", "Crit%", 60, "e"),
    ("max", "Max Hit", 70, "e"),
]

# Per-metric label overrides for the "dmg"/"dps" columns — same table and
# sorting machinery underneath, just relabeled so "Taken" doesn't say "DPS".
METRIC_LABELS = {
    "damage": ("Damage", "DPS"),
    "healing": ("Healing", "HPS"),
    "taken": ("Taken", "DTPS"),
}

ABILITY_COLUMNS = [
    ("ability", "Ability", 160, "w"),
    ("hits", "Hits", 55, "e"),
    ("total", "Total", 80, "e"),
    ("avg", "Avg", 70, "e"),
    ("crit", "Crit%", 60, "e"),
    ("max", "Max", 70, "e"),
]

AA_BONUS_OPTIONS = [
    ("Auto (from /alt list)", None),
    ("None (0%)", 0.0),
    ("Rank 1 (5%)", 0.05),
    ("Rank 2 (15%)", 0.15),
    ("Rank 3 (30%)", 0.30),
    ("Rank 4 (50%)", 0.50),
]


def _bar(pct: float) -> str:
    filled = round(pct / 100 * config.BAR_WIDTH)
    filled = max(0, min(config.BAR_WIDTH, filled))
    return "█" * filled + "░" * (config.BAR_WIDTH - filled) + f"  {pct:4.1f}%"


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, round(seconds))
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


_SORT_KEYS = {
    "name": lambda c: c.name.lower(),
    "dmg": lambda c: c.total,
    "dps": lambda c: c.total,
    "pct": lambda c: c.total,
    "hits": lambda c: c.hits,
    "crit": lambda c: c.crit_rate,
    "max": lambda c: c.max_hit,
}

# You first, then your pet(s) — permanent or currently-charmed — directly
# below, regardless of which column the table is sorted by; party and
# strangers fill in after.
_KIND_ROW_PRIORITY = {"you": 0, "pet": 1, "party": 2, "other": 3}


class MeterApp:
    def __init__(self, log_dir: str):
        self.cfg = config.load_config()

        self.state = MeterState(track_others=self.cfg["track_others"])
        self.watcher = LogWatcher(log_dir)
        self.running = True
        self.is_live_mode = True

        self.selected_encounter_id = "Live / Latest"
        self.selected_combatant: Optional[str] = None
        self._row_name_by_iid: Dict[str, str] = {}
        self._sort_col = "dmg"
        self._sort_desc = True
        self.metric = "damage"
        self.breakdown_by = "ability"
        self._charm_loss_seen = 0

        self.root = tk.Tk()
        self.root.title("Friends EverQuest Legends DPS Meter")
        self.root.configure(bg=config.BG)
        self.root.geometry("800x940")
        self.root.minsize(660, 700)
        self.root.attributes("-topmost", bool(self.cfg["always_on_top"]))

        self._init_style()
        self._build_ui()
        self._start_tail_thread()
        self._schedule_update()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── style ────────────────────────────────────────────────────────────

    def _init_style(self):
        self.font_title = font.Font(family="Segoe UI", size=11, weight="bold")
        self.font_normal = font.Font(family="Segoe UI", size=9)
        self.font_mono = font.Font(family="Consolas", size=10)
        self.font_big = font.Font(family="Consolas", size=13, weight="bold")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Treeview",
            background=config.PANEL_BG,
            fieldbackground=config.PANEL_BG,
            foreground=config.FG,
            rowheight=22,
            font=self.font_mono,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background=config.BTN_BG,
            foreground=config.MINT,
            font=self.font_normal,
            relief="flat",
        )
        style.map("Treeview", background=[("selected", "#2a3a44")])
        style.configure("TCombobox", fieldbackground=config.PANEL_BG, background=config.BTN_BG)

    # ── layout ───────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_status_row()
        self._build_party_panel()
        self._build_main_table()
        self._build_ability_table()
        self._build_buffs_panel()
        self._build_footer()

    def _build_header(self):
        header = tk.Frame(self.root, bg=config.PANEL_BG, padx=10, pady=8)
        header.pack(fill="x", padx=8, pady=(8, 4))

        tk.Label(header, text="⚔  EVERQUEST LEGENDS — DAMAGE METER",
                 font=self.font_title, fg=config.MINT, bg=config.PANEL_BG).pack(anchor="w", pady=(0, 4))

        sel_row = tk.Frame(header, bg=config.PANEL_BG)
        sel_row.pack(fill="x", pady=2)

        tk.Label(sel_row, text="Char:", font=self.font_normal, fg=config.DIM, bg=config.PANEL_BG).pack(side="left")
        chars, _ = self.watcher.scan_available_logs()
        self.char_var = tk.StringVar(value=self.cfg["char"])
        self.char_combo = ttk.Combobox(sel_row, textvariable=self.char_var,
                                        values=["Auto (Newest)"] + chars, state="readonly", width=14)
        self.char_combo.pack(side="left", padx=(4, 12))
        self.char_combo.bind("<<ComboboxSelected>>", self._on_selection_changed)

        tk.Label(sel_row, text="Server:", font=self.font_normal, fg=config.DIM, bg=config.PANEL_BG).pack(side="left")
        self.server_var = tk.StringVar(value=self.cfg["server"])
        self.server_combo = ttk.Combobox(sel_row, textvariable=self.server_var,
                                          values=["Auto (Newest)"] + config.STANDARD_SERVERS,
                                          state="readonly", width=16)
        self.server_combo.pack(side="left", padx=(4, 0))
        self.server_combo.bind("<<ComboboxSelected>>", self._on_selection_changed)

        self.watcher.set_override(self.char_var.get(), self.server_var.get())

        file_row = tk.Frame(header, bg=config.PANEL_BG)
        file_row.pack(fill="x", pady=(6, 0))
        self.lbl_log = tk.Label(file_row, text="Log: —", font=self.font_normal, fg=config.DIM, bg=config.PANEL_BG)
        self.lbl_log.pack(anchor="w")

        filter_row = tk.Frame(header, bg=config.PANEL_BG)
        filter_row.pack(fill="x", pady=(6, 0))

        tk.Label(filter_row, text="Encounter:", font=self.font_normal, fg=config.DIM, bg=config.PANEL_BG).pack(side="left")
        self.enc_filter_var = tk.StringVar(value="Live / Latest")
        self.enc_combo = ttk.Combobox(filter_row, textvariable=self.enc_filter_var, state="readonly", width=22)
        self.enc_combo.pack(side="left", padx=(4, 0))
        self.enc_combo.bind("<<ComboboxSelected>>", self._on_encounter_selected)

    def _build_status_row(self):
        status_frame = tk.Frame(self.root, bg=config.BG, padx=10, pady=2)
        status_frame.pack(fill="x")
        self.lbl_status = tk.Label(status_frame, text="● IDLE", font=self.font_normal, fg=config.DIM, bg=config.BG)
        self.lbl_status.pack(side="left")
        self.lbl_matched = tk.Label(status_frame, text="matched 0", font=self.font_normal, fg=config.DIM, bg=config.BG)
        self.lbl_matched.pack(side="left", padx=(10, 0))

        self.track_others_var = tk.BooleanVar(value=self.cfg["track_others"])
        tk.Checkbutton(
            status_frame, text="Track others", variable=self.track_others_var,
            command=self._on_track_others_toggled, bg=config.BG, fg=config.FG,
            selectcolor=config.PANEL_BG, activebackground=config.BG, activeforeground=config.FG,
            font=self.font_normal,
        ).pack(side="left", padx=(10, 0))

        self.topmost_var = tk.BooleanVar(value=self.cfg["always_on_top"])
        tk.Checkbutton(
            status_frame, text="Always on top", variable=self.topmost_var,
            command=self._on_topmost_toggled, bg=config.BG, fg=config.FG,
            selectcolor=config.PANEL_BG, activebackground=config.BG, activeforeground=config.FG,
            font=self.font_normal,
        ).pack(side="left", padx=(10, 0))

        button_row = tk.Frame(self.root, bg=config.BG, padx=10)
        button_row.pack(fill="x", pady=(2, 4))
        for text, color, cmd in [
            ("Reset", config.PINK, self._do_reset),
            ("Replay Full Log", config.MINT, self._do_replay),
            ("Export CSV", config.MINT, self._do_export),
        ]:
            tk.Button(
                button_row, text=text, command=cmd,
                bg=config.BTN_BG, fg=color, activebackground="#30363d", activeforeground=color,
                relief="flat", padx=8, pady=1, font=self.font_normal,
            ).pack(side="right", padx=(6, 0))

        tk.Label(button_row, text="Opacity:", font=self.font_normal, fg=config.DIM, bg=config.BG).pack(side="left")
        self.opacity_var = tk.IntVar(value=round(self.cfg["opacity"] * 100))
        self.opacity_scale = ttk.Scale(
            button_row, from_=40, to=100, orient="horizontal", length=100,
            command=self._on_opacity_changed,
        )
        self.opacity_scale.pack(side="left", padx=(4, 4))
        self.lbl_opacity = tk.Label(button_row, text=f"{self.opacity_var.get()}%", font=self.font_normal,
                                     fg=config.DIM, bg=config.BG, width=4, anchor="w")
        self.lbl_opacity.pack(side="left")
        self.opacity_scale.set(self.opacity_var.get())  # applies -alpha via _on_opacity_changed

        metric_row = tk.Frame(self.root, bg=config.BG, padx=10)
        metric_row.pack(fill="x", pady=(0, 4))

        tk.Label(metric_row, text="Metric:", font=self.font_normal, fg=config.DIM, bg=config.BG).pack(side="left")
        self.metric_buttons = {}
        for key, label in [("damage", "Damage"), ("healing", "Healing"), ("taken", "Taken")]:
            btn = tk.Button(
                metric_row, text=label, command=lambda k=key: self._on_metric_selected(k),
                bg=config.BTN_BG, fg=config.FG, activebackground="#30363d", activeforeground=config.MINT,
                relief="flat", padx=8, pady=1, font=self.font_normal,
            )
            btn.pack(side="left", padx=(4, 0))
            self.metric_buttons[key] = btn
        self._refresh_metric_buttons()

        self.charm_alert_var = tk.BooleanVar(value=self.cfg["charm_alert"])
        tk.Checkbutton(
            metric_row, text="🔔 Charm break alert", variable=self.charm_alert_var,
            command=self._on_charm_alert_toggled, bg=config.BG, fg=config.FG,
            selectcolor=config.PANEL_BG, activebackground=config.BG, activeforeground=config.FG,
            font=self.font_normal,
        ).pack(side="right")

    def _build_party_panel(self):
        party_frame = tk.LabelFrame(self.root, text=" PARTY STATUS ", font=self.font_normal,
                                     fg=config.MINT, bg=config.PANEL_BG, padx=8, pady=6)
        party_frame.pack(fill="x", padx=8, pady=4)

        self.lbl_group_state = tk.Label(party_frame, text="Status: Solo", font=self.font_title,
                                         fg=config.DIM, bg=config.PANEL_BG)
        self.lbl_group_state.pack(anchor="w", pady=(0, 2))

        self.lbl_party = tk.Label(party_frame, text="Members: None", font=self.font_mono,
                                   fg=config.FG, bg=config.PANEL_BG)
        self.lbl_party.pack(anchor="w")

    def _build_main_table(self):
        enc = tk.LabelFrame(self.root, text=" ENCOUNTER — click a row for its ability breakdown ",
                             font=self.font_normal, fg=config.MINT, bg=config.PANEL_BG, padx=6, pady=6)
        enc.pack(fill="both", expand=True, padx=8, pady=4)

        self.tree = ttk.Treeview(enc, columns=[c[0] for c in COLUMNS], show="headings", height=8)
        for key, label, width, anchor in COLUMNS:
            self.tree.heading(key, text=label, command=lambda k=key: self._on_sort(k))
            self.tree.column(key, width=width, anchor=anchor, stretch=(key == "pct"))
        self.tree.pack(fill="both", expand=True, side="left")
        self.tree.bind("<<TreeviewSelect>>", self._on_row_selected)

        for kind, color in config.KIND_COLOR.items():
            self.tree.tag_configure(kind, foreground=color)

        scroll = ttk.Scrollbar(enc, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)

        self.lbl_elapsed = tk.Label(self.root, text="elapsed 00:00", font=self.font_normal,
                                     fg=config.DIM, bg=config.BG)
        self.lbl_elapsed.pack(anchor="e", padx=12)

    def _build_ability_table(self):
        frame = tk.LabelFrame(self.root, text=" BREAKDOWN — click a row above ", font=self.font_normal,
                               fg=config.MINT, bg=config.PANEL_BG, padx=6, pady=6)
        frame.pack(fill="both", padx=8, pady=4)

        toggle_row = tk.Frame(frame, bg=config.PANEL_BG)
        toggle_row.pack(fill="x", pady=(0, 4))
        self.breakdown_buttons = {}
        for key, label in [("ability", "By Ability"), ("target", "By Target")]:
            btn = tk.Button(
                toggle_row, text=label, command=lambda k=key: self._on_breakdown_by_selected(k),
                bg=config.BTN_BG, fg=config.FG, activebackground="#30363d", activeforeground=config.MINT,
                relief="flat", padx=8, pady=1, font=self.font_normal,
            )
            btn.pack(side="left", padx=(0, 4))
            self.breakdown_buttons[key] = btn
        self._refresh_breakdown_buttons()

        self.ability_tree = ttk.Treeview(frame, columns=[c[0] for c in ABILITY_COLUMNS], show="headings", height=5)
        for key, label, width, anchor in ABILITY_COLUMNS:
            self.ability_tree.heading(key, text=label)
            self.ability_tree.column(key, width=width, anchor=anchor)
        self.ability_tree.pack(fill="both", expand=True)

    def _build_buffs_panel(self):
        frame = tk.LabelFrame(self.root, text=" BUFFS — durations self-learned from your own casts ",
                               font=self.font_normal, fg=config.MINT, bg=config.PANEL_BG, padx=6, pady=6)
        frame.pack(fill="both", padx=8, pady=4)

        aa_row = tk.Frame(frame, bg=config.PANEL_BG)
        aa_row.pack(fill="x", pady=(0, 4))
        tk.Label(aa_row, text="Spell Casting Reinforcement:", font=self.font_normal, fg=config.DIM,
                 bg=config.PANEL_BG).pack(side="left")
        self.aa_var = tk.StringVar(value=AA_BONUS_OPTIONS[0][0])
        self.aa_combo = ttk.Combobox(aa_row, textvariable=self.aa_var, state="readonly", width=22,
                                      values=[label for label, _ in AA_BONUS_OPTIONS])
        self.aa_combo.pack(side="left", padx=(4, 8))
        self.aa_combo.bind("<<ComboboxSelected>>", self._on_aa_selected)
        self.lbl_aa_effective = tk.Label(aa_row, text="effective: 0%", font=self.font_normal, fg=config.FG,
                                          bg=config.PANEL_BG)
        self.lbl_aa_effective.pack(side="left")

        cols_row = tk.Frame(frame, bg=config.PANEL_BG)
        cols_row.pack(fill="both", expand=True)

        active_col = tk.Frame(cols_row, bg=config.PANEL_BG)
        active_col.pack(side="left", fill="both", expand=True, padx=(0, 4))
        tk.Label(active_col, text="Active — soonest to expire first", font=self.font_normal, fg=config.DIM,
                 bg=config.PANEL_BG).pack(anchor="w")
        self.active_buffs_tree = ttk.Treeview(active_col, columns=["spell", "remaining"], show="headings", height=5)
        self.active_buffs_tree.heading("spell", text="Spell")
        self.active_buffs_tree.heading("remaining", text="Remaining")
        self.active_buffs_tree.column("spell", width=140, anchor="w")
        self.active_buffs_tree.column("remaining", width=80, anchor="e")
        self.active_buffs_tree.pack(fill="both", expand=True)

        expired_col = tk.Frame(cols_row, bg=config.PANEL_BG)
        expired_col.pack(side="left", fill="both", expand=True, padx=(4, 0))
        tk.Label(expired_col, text="What fell off — shortest actual lifespan first", font=self.font_normal,
                 fg=config.DIM, bg=config.PANEL_BG).pack(anchor="w")
        self.expired_buffs_tree = ttk.Treeview(expired_col, columns=["spell", "actual", "expected"],
                                                show="headings", height=5)
        self.expired_buffs_tree.heading("spell", text="Spell")
        self.expired_buffs_tree.heading("actual", text="Actual")
        self.expired_buffs_tree.heading("expected", text="Expected")
        self.expired_buffs_tree.column("spell", width=120, anchor="w")
        self.expired_buffs_tree.column("actual", width=70, anchor="e")
        self.expired_buffs_tree.column("expected", width=70, anchor="e")
        self.expired_buffs_tree.pack(fill="both", expand=True)

    def _build_footer(self):
        sess = tk.LabelFrame(self.root, text=" SESSION TOTALS ", font=self.font_normal,
                              fg=config.MINT, bg=config.PANEL_BG, padx=10, pady=6)
        sess.pack(fill="x", padx=8, pady=4)
        self.lbl_session = tk.Label(sess, text="—", font=self.font_mono, fg=config.FG, bg=config.PANEL_BG)
        self.lbl_session.pack(anchor="w")

        foot = tk.Frame(self.root, bg=config.BG)
        foot.pack(fill="x", side="bottom", padx=8, pady=4)
        self.lbl_footer = tk.Label(foot, text="Close window to quit", font=self.font_normal,
                                    fg=config.DIM, bg=config.BG)
        self.lbl_footer.pack(side="left")

    # ── event handlers ──────────────────────────────────────────────────

    def _on_selection_changed(self, event=None):
        self.watcher.set_override(self.char_var.get(), self.server_var.get())
        self.cfg["char"] = self.char_var.get()
        self.cfg["server"] = self.server_var.get()
        config.save_config(self.cfg)
        if not self.is_live_mode:
            self._do_replay()

    def _on_track_others_toggled(self):
        self.state.track_others = self.track_others_var.get()
        self.cfg["track_others"] = self.state.track_others
        config.save_config(self.cfg)

    def _on_metric_selected(self, metric: str):
        self.metric = metric
        self._refresh_metric_buttons()
        dmg_label, dps_label = METRIC_LABELS[metric]
        self.tree.heading("dmg", text=dmg_label)
        self.tree.heading("dps", text=dps_label)

    def _refresh_metric_buttons(self):
        for key, btn in self.metric_buttons.items():
            btn.config(fg=config.MINT if key == self.metric else config.FG)

    def _on_charm_alert_toggled(self):
        self.cfg["charm_alert"] = self.charm_alert_var.get()
        config.save_config(self.cfg)

    def _on_breakdown_by_selected(self, key: str):
        self.breakdown_by = key
        self._refresh_breakdown_buttons()
        self.ability_tree.heading("ability", text="Ability" if key == "ability" else "Target")

    def _refresh_breakdown_buttons(self):
        for key, btn in self.breakdown_buttons.items():
            btn.config(fg=config.MINT if key == self.breakdown_by else config.FG)

    def _on_aa_selected(self, event=None):
        label = self.aa_var.get()
        bonus = dict(AA_BONUS_OPTIONS).get(label)
        with self.state.lock:
            self.state.aa_bonus_override = bonus

    def _on_topmost_toggled(self):
        self.root.attributes("-topmost", self.topmost_var.get())
        self.cfg["always_on_top"] = self.topmost_var.get()
        config.save_config(self.cfg)

    def _on_opacity_changed(self, value: str):
        pct = round(float(value))
        self.opacity_var.set(pct)
        self.lbl_opacity.config(text=f"{pct}%")
        alpha = pct / 100
        try:
            self.root.attributes("-alpha", alpha)
        except tk.TclError:
            pass  # not supported on this platform/window manager
        self.cfg["opacity"] = alpha
        config.save_config(self.cfg)

    def _on_encounter_selected(self, event=None):
        self.selected_encounter_id = self.enc_filter_var.get()

    def _on_sort(self, col: str):
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = True
        for key, label, _width, _anchor in COLUMNS:
            arrow = ""
            if key == self._sort_col:
                arrow = " ▼" if self._sort_desc else " ▲"
            self.tree.heading(key, text=label + arrow)

    def _on_row_selected(self, event=None):
        sel = self.tree.selection()
        self.selected_combatant = self._row_name_by_iid.get(sel[0]) if sel else None

    def _do_reset(self):
        self.state.reset_all()
        self._update_ui()

    def _do_replay(self):
        self._log_footer("Replaying full log history…")

        def work():
            count = replay_full_history(self.state, self.watcher)
            self.is_live_mode = False
            self.root.after(0, lambda: self._log_footer(
                f"Replayed {count:,} lines — {self.state.encounter_count} encounters found."
            ))

        threading.Thread(target=work, daemon=True).start()

    def _do_export(self):
        seg = self._resolve_display_segment()
        if seg is None or not seg.damage:
            self._log_footer("Nothing to export.")
            return
        out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports")
        os.makedirs(out_dir, exist_ok=True)
        fname = f"encounter_{seg.index}_{int(seg.start)}.csv"
        path = os.path.join(out_dir, fname)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["name", "kind", "damage", "dps", "hits", "crit_rate", "max_hit"])
            now = time.time()
            dur = seg.duration(now)
            for c in seg.ranked():
                w.writerow([c.name, c.kind, c.total, round(c.total / dur, 1), c.hits,
                            round(c.crit_rate * 100, 1), c.max_hit])
        self._log_footer(f"Exported to {path}")

    def _log_footer(self, text: str):
        self.lbl_footer.config(text=text)

    def _on_close(self):
        self.running = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()

    # ── background tailing ──────────────────────────────────────────────

    def _start_tail_thread(self):
        def loop():
            while self.running:
                if self.is_live_mode:
                    line = self.watcher.readline()
                    if line:
                        self.state.ingest_line(line)
                    else:
                        self.watcher.poll()
                        time.sleep(config.TAIL_SLEEP)
                    self.state.tick()
                else:
                    time.sleep(0.5)

        threading.Thread(target=loop, daemon=True).start()

    def _schedule_update(self):
        self._update_ui()
        if self.running:
            self.root.after(config.REFRESH_MS, self._schedule_update)

    # ── rendering ────────────────────────────────────────────────────────

    def _resolve_display_segment(self) -> Optional[Segment]:
        segments = self.state.segments
        if self.selected_encounter_id == "Live / Latest":
            if self.state.active is not None:
                return self.state.active
            return segments[-1] if segments else None
        try:
            idx = int(self.selected_encounter_id.split(":")[0].replace("#", ""))
        except ValueError:
            return segments[-1] if segments else None
        for s in segments:
            if s.index == idx:
                return s
        return segments[-1] if segments else None

    def _update_ui(self):
        with self.state.lock:
            self._refresh_header_widgets()
            self._refresh_party_status()
            self._refresh_status_row()
            self._refresh_encounter_options(self.state.segments)

            display_seg = self._resolve_display_segment()
            self._refresh_main_table(display_seg)
            self._refresh_ability_table(display_seg)
            self._refresh_elapsed(display_seg)
            self._refresh_session_totals(self.state.segments)
            self._refresh_buffs_panel()
            self._check_charm_alert()

    def _refresh_header_widgets(self):
        chars, _ = self.watcher.scan_available_logs()
        wanted = ["Auto (Newest)"] + chars
        if list(self.char_combo["values"]) != wanted:
            self.char_combo["values"] = wanted

        logname = os.path.basename(self.watcher.path) if self.watcher.path else "scanning…"
        mode = "history" if not self.is_live_mode else "live"
        self.lbl_log.config(text=f"Log: {logname} (Char: {self.watcher.character}, {mode})")

        if self.watcher.character and self.watcher.character != "—":
            self.state.set_character_name(self.watcher.character)

    def _refresh_party_status(self):
        if self.state.in_group or self.state.party_members:
            self.lbl_group_state.config(text="Status: In Group", fg=config.PARTY_COLOR)
            members = ", ".join(sorted(self.state.party_members)) if self.state.party_members else "None listed yet"
            self.lbl_party.config(text=f"Members: {members}")
        else:
            self.lbl_group_state.config(text="Status: Solo", fg=config.DIM)
            self.lbl_party.config(text="Members: None")

    def _refresh_status_row(self):
        if self.state.active:
            self.lbl_status.config(text="● IN COMBAT", fg=config.PINK)
        else:
            self.lbl_status.config(text="● IDLE", fg=config.DIM)
        self.lbl_matched.config(text=f"matched {self.state.matched_lines:,} / {self.state.lines_seen:,} lines")

    def _refresh_encounter_options(self, segments: List[Segment]):
        options = ["Live / Latest"] + [f"#{s.index}: {s.mob_name}" for s in reversed(segments)]
        if list(self.enc_combo["values"]) != options:
            self.enc_combo["values"] = options
        if self.selected_encounter_id not in options:
            self.selected_encounter_id = "Live / Latest"
            self.enc_filter_var.set("Live / Latest")

    def _refresh_main_table(self, seg: Optional[Segment]):
        now = time.time()
        rows = []
        if seg is not None:
            bucket = seg.combatants_for(self.metric)
            total = max(seg.total_for(self.metric), 1)
            dur = seg.duration(now)
            key_fn = _SORT_KEYS.get(self._sort_col, _SORT_KEYS["dmg"])
            # Stable two-pass sort: order by the clicked column first, then
            # re-group by kind (You, then pet, then party, then other) — the
            # per-kind ordering from the first pass survives within each group.
            combatants = sorted(bucket.values(), key=key_fn, reverse=self._sort_desc)
            combatants.sort(key=lambda c: _KIND_ROW_PRIORITY.get(c.kind, 9))
            for c in combatants:
                pct = 100.0 * c.total / total
                rows.append((
                    c.name, c.kind,
                    (c.name, f"{c.total:,}", f"{c.total / dur:,.0f}", _bar(pct),
                     f"{c.hits}", f"{c.crit_rate * 100:.0f}%", f"{c.max_hit:,}"),
                ))

        existing = self.tree.get_children()
        self._row_name_by_iid = {}
        for i, (name, kind, values) in enumerate(rows):
            iid = str(i)
            self._row_name_by_iid[iid] = name
            if iid in existing:
                self.tree.item(iid, values=values, tags=(kind,))
            else:
                self.tree.insert("", "end", iid=iid, values=values, tags=(kind,))
        for iid in existing[len(rows):]:
            self.tree.delete(iid)

    def _refresh_ability_table(self, seg: Optional[Segment]):
        existing = self.ability_tree.get_children()
        combatant = None
        if seg is not None:
            bucket = seg.combatants_for(self.metric)
            if self.selected_combatant and self.selected_combatant in bucket:
                combatant = bucket[self.selected_combatant]
            elif "You" in bucket:
                combatant = bucket["You"]

        rows = []
        if combatant is not None:
            source = combatant.top_abilities() if self.breakdown_by == "ability" else combatant.top_targets()
            for name, stats in source:
                crit_pct = (stats.crits / stats.hits * 100) if stats.hits else 0.0
                rows.append((name, stats.hits, f"{stats.total:,}", f"{stats.avg_hit:,.0f}",
                             f"{crit_pct:.0f}%", f"{stats.max_hit:,}"))

        for i, values in enumerate(rows):
            iid = str(i)
            if iid in existing:
                self.ability_tree.item(iid, values=values)
            else:
                self.ability_tree.insert("", "end", iid=iid, values=values)
        for iid in existing[len(rows):]:
            self.ability_tree.delete(iid)

    def _refresh_elapsed(self, seg: Optional[Segment]):
        if seg is None:
            self.lbl_elapsed.config(text="elapsed 00:00")
            return
        now = time.time()
        dur = seg.duration(now)
        mins, secs = int(dur) // 60, int(dur) % 60
        live = "  ● LIVE" if seg.end is None else ""
        self.lbl_elapsed.config(text=f"elapsed {mins:02d}:{secs:02d}  [{seg.mob_name}]{live}")

    def _refresh_session_totals(self, segments: List[Segment]):
        totals = {"you": 0, "pet": 0, "party": 0, "other": 0}
        for s in segments:
            for c in s.combatants_for(self.metric).values():
                totals[c.kind] = totals.get(c.kind, 0) + c.total
        if self.state.active is not None:
            for c in self.state.active.combatants_for(self.metric).values():
                totals[c.kind] = totals.get(c.kind, 0) + c.total

        enc_count = len(segments) + (1 if self.state.active is not None else 0)
        metric_label = METRIC_LABELS[self.metric][0]
        self.lbl_session.config(
            text=(f"[{metric_label}]  you {totals['you']:,}    pet {totals['pet']:,}    "
                  f"party {totals['party']:,}    other {totals['other']:,}    encounters {enc_count}")
        )

    def _refresh_buffs_panel(self):
        self.lbl_aa_effective.config(text=f"effective: {self.state.current_aa_bonus() * 100:.0f}%")

        # Same live-vs-replay clock split as everywhere else in this app:
        # real time while tailing live, the log's own last-seen timestamp
        # while reviewing history — otherwise every buff in a replay reads
        # as expired the instant "now" (today, in real life) is compared
        # against a cast_ts from the log's own (much earlier) clock.
        now = time.time() if self.is_live_mode else (self.state.last_activity_ts or time.time())
        active_rows = []
        for key, (cast_ts, _bonus) in self.state.active_buffs.items():
            remaining = self.state.buff_remaining(key, now)
            # A known duration that's been over for a while with no "wears
            # off" line ever seen almost always means an instant-effect
            # spell (Gate, Feign Death, ...) that was never a real timed
            # buff — drop it rather than let it squat at the top forever.
            if remaining is not None and remaining < -60:
                continue
            display = self.state.buff_display_names.get(key, key)
            sort_key = remaining if remaining is not None else float("inf")
            text = _fmt_duration(remaining) if remaining is not None else "learning…"
            active_rows.append((sort_key, display, text))
        active_rows.sort(key=lambda r: r[0])
        active_rows = active_rows[:30]

        existing = self.active_buffs_tree.get_children()
        for i, (_key, display, text) in enumerate(active_rows):
            iid = str(i)
            values = (display, text)
            if iid in existing:
                self.active_buffs_tree.item(iid, values=values)
            else:
                self.active_buffs_tree.insert("", "end", iid=iid, values=values)
        for iid in existing[len(active_rows):]:
            self.active_buffs_tree.delete(iid)

        expired = sorted(self.state.expired_buffs, key=lambda b: b.actual_duration)[:30]
        existing = self.expired_buffs_tree.get_children()
        for i, b in enumerate(expired):
            iid = str(i)
            expected_text = _fmt_duration(b.expected_duration) if b.expected_duration is not None else "—"
            values = (b.spell, _fmt_duration(b.actual_duration), expected_text)
            if iid in existing:
                self.expired_buffs_tree.item(iid, values=values)
            else:
                self.expired_buffs_tree.insert("", "end", iid=iid, values=values)
        for iid in existing[len(expired):]:
            self.expired_buffs_tree.delete(iid)

    def _check_charm_alert(self):
        count = len(self.state.charm_loss_events)
        if count > self._charm_loss_seen and self.charm_alert_var.get():
            audio.play_charm_break_alert()
        self._charm_loss_seen = count
