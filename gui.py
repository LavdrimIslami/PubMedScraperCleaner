"""
GUI front-end for fetching PMC articles by PMCID or PMC article URL.

Flow:
  1. A window opens with one input row (PMCID or PMC article URL).
  2. "+ Add another" appends more rows as needed, or "Paste a list..." opens
     a modal textbox for pasting many PMCIDs/URLs at once -- any separator,
     or none at all -- with a Go button to fetch them immediately.
  3. Either path checks each PMCID against what's already been saved to the
     output folder before any request is sent -- if a match is found, a
     popup asks whether to skip it or fetch it again as a copy.
  4. Remaining PMCIDs are queued and fetched (reusing main.py's EFetch +
     JATS-parsing logic + rate-limited queue) and saved as JSON in the
     output folder (an "output" directory inside the project folder, by
     default).
  5. A confirmation screen is shown if everything succeeded, or an error
     screen if anything failed -- either way, files that *did* succeed are
     saved to disk.
"""

import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import main as pmc  # reuses normalize_pmcid / extract_all_pmcids / fetch_pmc_xml / parse_article / ...


def extract_pmcid(raw_text):
    """
    Accepts a bare PMCID ('PMC1234567', '1234567') or a PMC article URL
    (e.g. https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/) and returns a
    normalized 'PMC1234567' string. Raises ValueError if nothing usable
    could be found in the text. A thin wrapper around main.py's
    extract_all_pmcids, which does the actual parsing.
    """
    ids = pmc.extract_all_pmcids(raw_text)
    if not ids:
        raise ValueError("empty or unrecognized")
    return ids[0]


class EntryRow:
    """A single input row: one text entry plus a small remove button."""

    def __init__(self, parent, on_remove):
        self.frame = ttk.Frame(parent)
        self.entry = ttk.Entry(self.frame, width=55)
        self.entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.remove_btn = ttk.Button(self.frame, text="\u2212", width=2,
                                      command=lambda: on_remove(self))
        self.remove_btn.pack(side="left")

    def get(self):
        return self.entry.get()

    def destroy(self):
        self.frame.destroy()


class ProgressBar:
    """A plain-tk (not ttk) green progress bar. ttk.Progressbar's color
    can't be reliably changed under Windows' native theme, so this draws a
    filled rectangle on a Canvas instead, which renders the same everywhere."""

    def __init__(self, parent, height=18):
        self.canvas = tk.Canvas(
            parent, height=height, bg="#e0e0e0",
            highlightthickness=1, highlightbackground="#bbbbbb",
        )
        self.bar = self.canvas.create_rectangle(0, 0, 0, height, fill="#2e9e44", width=0)
        self.maximum = 1
        self.value = 0
        # Canvas width isn't known until it's laid out, and can change if the
        # window is resized, so redraw whenever its size changes.
        self.canvas.bind("<Configure>", lambda e: self._redraw())

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)

    def set_maximum(self, maximum):
        self.maximum = max(maximum, 1)
        self.set_value(0)

    def set_value(self, value):
        self.value = min(value, self.maximum)
        self._redraw()

    def _redraw(self):
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        self.canvas.coords(self.bar, 0, 0, width * (self.value / self.maximum), height)


class PMCFetcherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PMC Article Fetcher")
        self.root.minsize(560, 380)

        self.rows = []
        self.result_queue = queue.Queue()
        self.progress_queue = queue.Queue()  # one tick per finished article
        self._progress_value = 0

        self._build_input_screen()

    # ------------------------------------------------------------------
    # Screen 1: PMCID / URL entry
    # ------------------------------------------------------------------
    def _build_input_screen(self):
        self.main_frame = ttk.Frame(self.root, padding=16)
        self.main_frame.pack(fill="both", expand=True)

        ttk.Label(
            self.main_frame,
            text="Enter one or more PMC article URLs or PMCIDs:",
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        # Scrollable area, in case a lot of rows get added
        canvas_frame = ttk.Frame(self.main_frame)
        canvas_frame.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(canvas_frame, height=200, highlightthickness=0)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self.canvas.yview)
        self.rows_container = ttk.Frame(self.canvas)

        self.rows_container.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.create_window((0, 0), window=self.rows_container, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # + Add row / paste-a-list buttons
        add_row_frame = ttk.Frame(self.main_frame)
        add_row_frame.pack(fill="x", pady=(8, 4))
        ttk.Button(add_row_frame, text="+ Add another", command=self.add_row).pack(side="left")
        ttk.Button(add_row_frame, text="Paste a list...", command=self.open_paste_modal).pack(
            side="left", padx=(8, 0)
        )

        # Output folder picker
        out_frame = ttk.Frame(self.main_frame)
        out_frame.pack(fill="x", pady=(8, 4))
        ttk.Label(out_frame, text="Save to:").pack(side="left")
        self.output_dir = tk.StringVar(value=pmc.ensure_output_dir())
        ttk.Entry(out_frame, textvariable=self.output_dir, width=40).pack(
            side="left", fill="x", expand=True, padx=6
        )
        ttk.Button(out_frame, text="Browse...", command=self.browse_output_dir).pack(side="left")

        # Submit button + status label
        bottom_frame = ttk.Frame(self.main_frame)
        bottom_frame.pack(fill="x", pady=(14, 0))
        self.status_label = ttk.Label(bottom_frame, text="")
        self.status_label.pack(side="left")
        self.submit_btn = ttk.Button(bottom_frame, text="Submit", command=self.on_submit)
        self.submit_btn.pack(side="right")

        # Progress bar: created now, but only packed (shown) once a fetch starts
        self.progress = ProgressBar(self.main_frame)

        # Start with exactly one row, as specified
        self.add_row()

    def add_row(self):
        row = EntryRow(self.rows_container, self.remove_row)
        row.frame.pack(fill="x", pady=3)
        self.rows.append(row)

    def remove_row(self, row):
        if len(self.rows) <= 1:
            return  # always keep at least one row on screen
        row.destroy()
        self.rows.remove(row)

    def browse_output_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.output_dir.get() or os.getcwd())
        if chosen:
            self.output_dir.set(chosen)

    def open_paste_modal(self):
        """A modal textbox for pasting a whole list of PMCIDs/URLs at once --
        any separator, or none at all -- then a Go button to fetch them."""
        modal = tk.Toplevel(self.root)
        modal.title("Paste a list of PMCIDs")
        modal.geometry("640x420")
        modal.minsize(500, 300)
        modal.transient(self.root)
        modal.grab_set()  # modal: blocks interaction with the main window until closed

        ttk.Label(
            modal,
            text="Paste PMCIDs or PMC URLs below. Any separator is fine --\n"
                 "newlines, commas, spaces, or nothing at all.",
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        # Packed before the textbox so it reserves its space first: the
        # expanding textbox can then only take what's left, which keeps the
        # Go button visible however the window is sized.
        btn_frame = ttk.Frame(modal)
        btn_frame.pack(side="bottom", fill="x", padx=12, pady=12)

        text = tk.Text(modal, wrap="word")
        text.pack(fill="both", expand=True, padx=12)
        text.focus_set()

        def go():
            pmcids = pmc.extract_all_pmcids(text.get("1.0", "end"))
            if not pmcids:
                messagebox.showerror("Nothing found", "No PMCIDs were found in that text.", parent=modal)
                return
            modal.destroy()
            self._start_fetch(pmcids)

        ttk.Button(btn_frame, text="Go", command=go).pack(side="right")
        ttk.Button(btn_frame, text="Cancel", command=modal.destroy).pack(side="right", padx=(0, 8))

    # ------------------------------------------------------------------
    # Submission / validation
    # ------------------------------------------------------------------
    def on_submit(self):
        raw_values = [row.get() for row in self.rows]
        raw_values = [v for v in raw_values if v.strip()]

        if not raw_values:
            messagebox.showerror(
                "Nothing to submit", "Please enter at least one PMCID or PMC article URL."
            )
            return

        pmcids = []
        invalid = []
        for raw in raw_values:
            try:
                pmcids.append(extract_pmcid(raw))
            except ValueError:
                invalid.append(raw)

        if invalid:
            messagebox.showerror(
                "Invalid entry",
                "These entries don't look like a PMCID or PMC article URL:\n\n"
                + "\n".join(f"\u2022 {v}" for v in invalid),
            )
            return

        self._start_fetch(pmcids)

    def _start_fetch(self, pmcids):
        """Shared by both the row-based Submit button and the paste modal's
        Go button: validates the output folder, checks for duplicates, then
        launches the background fetch."""
        out_dir = self.output_dir.get().strip() or pmc.DEFAULT_OUTPUT_DIR
        if not os.path.isdir(out_dir):
            messagebox.showerror("Invalid folder", f"'{out_dir}' is not a valid folder.")
            return

        # Check each PMCID against what's already been saved, *before* any
        # request goes out. A modal popup per duplicate is the simplest way
        # to ask -- far less code than an inline widget beside every row.
        to_fetch = []
        for pmcid in pmcids:
            existing = pmc.existing_json_path(pmcid, out_dir)
            if existing and not messagebox.askyesno(
                "Already scraped",
                f"{pmcid} already has a saved JSON:\n{existing}\n\n"
                "Fetch it again and save as a copy? Choose No to skip it.",
            ):
                continue
            to_fetch.append(pmcid)

        if not to_fetch:
            messagebox.showinfo("Nothing to do", "Every entry was skipped.")
            return

        self.submit_btn.config(state="disabled")
        self.status_label.config(text=f"Fetching {len(to_fetch)} article(s)...")
        self._progress_value = 0
        self.progress.set_maximum(len(to_fetch))
        self.progress.pack(fill="x", pady=(8, 0))
        threading.Thread(target=self._fetch_all, args=(to_fetch, out_dir), daemon=True).start()
        self.root.after(100, self._poll_queue)

    # ------------------------------------------------------------------
    # Background fetch (runs off the main/UI thread)
    # ------------------------------------------------------------------
    def _fetch_all(self, pmcids, out_dir):
        try:
            if not pmc.ensure_lxml():
                self.result_queue.put([{
                    "pmcid": "-",
                    "ok": False,
                    "error": "The 'lxml' package could not be installed automatically. "
                             "Please run: pip install lxml, then try again.",
                }])
                return
        except Exception as e:
            self.result_queue.put([{"pmcid": "-", "ok": False, "error": str(e)}])
            return

        results = []

        def handle_one(pmcid):
            try:
                xml_text = pmc.fetch_pmc_xml(pmcid)
                data = pmc.parse_article(xml_text, requested_pmcid=pmcid)
                out_path = os.path.join(out_dir, f"{data['pmcid']}.json")
                out_path = pmc.make_unique_path(out_path)  # e.g. "PMC123.json" -> "PMC123 (2).json"
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                results.append({"pmcid": pmcid, "ok": True, "path": out_path})
            except Exception as e:
                results.append({"pmcid": pmcid, "ok": False, "error": str(e)})
            self.progress_queue.put(1)  # picked up by _poll_queue on the UI thread

        # The GUI doesn't currently take an NCBI API key, so it always uses
        # the no-key limit, with requests spaced evenly rather than bursted.
        pmcid_queue = pmc.build_pmcid_queue(pmcids)
        pmc.drain_queue_rate_limited(pmcid_queue, handle_one, pmc.REQUESTS_PER_SECOND_NO_KEY)

        self.result_queue.put(results)

    def _poll_queue(self):
        ticks = 0
        while True:
            try:
                self.progress_queue.get_nowait()
                ticks += 1
            except queue.Empty:
                break
        if ticks:
            self._progress_value += ticks
            self.progress.set_value(self._progress_value)

        try:
            results = self.result_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_queue)
            return
        self._show_results_screen(results)

    # ------------------------------------------------------------------
    # Screen 2: confirmation (all succeeded) or error (something failed)
    # ------------------------------------------------------------------
    def _show_results_screen(self, results):
        self.main_frame.destroy()

        all_ok = all(r["ok"] for r in results)

        self.result_frame = ttk.Frame(self.root, padding=16)
        self.result_frame.pack(fill="both", expand=True)

        if all_ok:
            header = "\u2705 All articles fetched successfully"
        else:
            header = "\u26a0 Some articles could not be fetched"

        ttk.Label(
            self.result_frame, text=header, font=("TkDefaultFont", 12, "bold")
        ).pack(anchor="w", pady=(0, 10))

        list_frame = ttk.Frame(self.result_frame)
        list_frame.pack(fill="both", expand=True)

        text = tk.Text(list_frame, wrap="word", height=14)
        text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(list_frame, command=text.yview)
        scroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=scroll.set)

        for r in results:
            if r["ok"]:
                text.insert("end", f"\u2713 {r['pmcid']} \u2192 saved to {r['path']}\n")
            else:
                text.insert("end", f"\u2717 {r['pmcid']}: {r['error']}\n")
        text.configure(state="disabled")

        ttk.Button(self.result_frame, text="Start over", command=self._reset).pack(pady=(12, 0))

    def _reset(self):
        self.result_frame.destroy()
        self.rows = []
        self._build_input_screen()


def main():
    root = tk.Tk()
    PMCFetcherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()