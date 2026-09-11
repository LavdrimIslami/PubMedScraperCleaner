"""
GUI front-end for fetching PMC articles by PMCID or PMC article URL.

Flow:
  1. A window opens with one input row (PMCID or PMC article URL).
  2. "+ Add another" appends more rows as needed.
  3. "Submit" validates every non-empty row, then fetches each article
     (reusing main.py's EFetch + JATS-parsing logic) and saves it as JSON.
  4. A confirmation screen is shown if everything succeeded, or an error
     screen if anything failed -- either way, files that *did* succeed are
     saved to disk.
"""

import json
import os
import re
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import main as pmc  # reuses normalize_pmcid / fetch_pmc_xml / parse_article / ensure_lxml

PMCID_IN_TEXT_RE = re.compile(r"PMC\d+", re.IGNORECASE)


def extract_pmcid(raw_text):
    """
    Accepts a bare PMCID ('PMC1234567', '1234567') or a PMC article URL
    (e.g. https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/) and returns a
    normalized 'PMC1234567' string. Raises ValueError if nothing usable
    could be found in the text.
    """
    raw_text = raw_text.strip()
    if not raw_text:
        raise ValueError("empty")

    match = PMCID_IN_TEXT_RE.search(raw_text)
    if match:
        return pmc.normalize_pmcid(match.group(0))

    # No "PMC..." substring found in there -- try treating it as a bare numeric ID.
    return pmc.normalize_pmcid(raw_text)


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


class PMCFetcherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PMC Article Fetcher")
        self.root.minsize(560, 380)

        self.rows = []
        self.result_queue = queue.Queue()

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

        # + Add row button
        add_row_frame = ttk.Frame(self.main_frame)
        add_row_frame.pack(fill="x", pady=(8, 4))
        ttk.Button(add_row_frame, text="+ Add another", command=self.add_row).pack(anchor="w")

        # Output folder picker
        out_frame = ttk.Frame(self.main_frame)
        out_frame.pack(fill="x", pady=(8, 4))
        ttk.Label(out_frame, text="Save to:").pack(side="left")
        self.output_dir = tk.StringVar(value=os.getcwd())
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

        out_dir = self.output_dir.get().strip() or os.getcwd()
        if not os.path.isdir(out_dir):
            messagebox.showerror("Invalid folder", f"'{out_dir}' is not a valid folder.")
            return

        self.submit_btn.config(state="disabled")
        self.status_label.config(text=f"Fetching {len(pmcids)} article(s)...")
        threading.Thread(target=self._fetch_all, args=(pmcids, out_dir), daemon=True).start()
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
        for i, pmcid in enumerate(pmcids):
            try:
                xml_text = pmc.fetch_pmc_xml(pmcid)
                data = pmc.parse_article(xml_text, requested_pmcid=pmcid)
                out_path = os.path.join(out_dir, f"{data['pmcid']}.json")
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                results.append({"pmcid": pmcid, "ok": True, "path": out_path})
            except Exception as e:
                results.append({"pmcid": pmcid, "ok": False, "error": str(e)})

            if i < len(pmcids) - 1:
                time.sleep(0.34)  # stay under NCBI's no-API-key rate limit

        self.result_queue.put(results)

    def _poll_queue(self):
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
