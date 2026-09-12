# PubMedScraperCleaner

Note: made with claude

# PMC Article Fetcher

Fetch the full text of open-access PubMed Central (PMC) articles by PMCID and
save them as clean, structured JSON — titles, authors, abstracts, section-by-section
body text, and references, all parsed out of NCBI's XML.

Two ways to use it:
- **GUI** — a small desktop window where you paste in one or more PMCIDs/URLs and hit Submit.
- **CLI** — a one-line terminal prompt, useful for scripting or headless machines.

---

## Contents

- [PubMedScraperCleaner](#pubmedscrapercleaner)
- [PMC Article Fetcher](#pmc-article-fetcher)
  - [Contents](#contents)
  - [What it does](#what-it-does)
  - [Features](#features)
  - [Project structure](#project-structure)
  - [Prerequisites](#prerequisites)
  - [Setup on Windows](#setup-on-windows)
  - [Setup on macOS](#setup-on-macos)
  - [Usage — GUI](#usage--gui)
  - [Usage — CLI](#usage--cli)
  - [Output format](#output-format)
  - [Troubleshooting](#troubleshooting)
  - [Known limitations](#known-limitations)
  - [Optional: building a standalone executable](#optional-building-a-standalone-executable)

---

## What it does

Given a PMCID (e.g. `PMC1234567`), the tool calls NCBI's **E-utilities EFetch**
endpoint (`efetch.fcgi?db=pmc`), which returns the article's full text in
NLM/JATS XML — the same structured format publishers submit to PMC. It then
parses that XML into a plain JSON dictionary containing:

- Title, journal, ISSN, DOI, PMID
- Authors, with their affiliations resolved
- Publication date and keywords
- Abstract
- The full body, broken into headings, paragraphs, and nested subsections
- The reference list

> **Why EFetch, and not the old PMC Open Access Web Service or FTP bulk files?**
> As of August 2026, NCBI retired both of those in favor of a few supported
> retrieval methods. EFetch is the one that still returns full JATS XML for a
> single article by PMCID, so that's what this project uses. Full body text is
> only returned for articles in the **PMC Open Access subset** — for anything
> outside it, EFetch (and therefore this tool) only returns citation/abstract
> metadata, which is reflected in the output's `has_full_text` field.

## Features
An interactive GUI, with support for multiple scans at once

<img width="562" height="412" alt="image" src="https://github.com/user-attachments/assets/17930b5f-a09f-4b7f-aa2e-8cfdf3fe3e23" />

<img width="695" height="412" alt="image" src="https://github.com/user-attachments/assets/61a8533e-1683-4678-b590-59b9c77788ff" />


Built in duplication detection

<img width="382" height="172" alt="image" src="https://github.com/user-attachments/assets/a406e7f3-64ce-4d8b-858e-e014aedef6fe" />




## Project structure

```
main.py    Core logic: fetch, parse, and save. Also the single entry point —
           running it opens the GUI by default, or the terminal prompt with --cli.
gui.py     Tkinter front-end. Imports its fetching/parsing functions from main.py
           rather than duplicating them.
```

You need both files in the same folder — `gui.py` imports from `main.py`.

## Prerequisites

- **Python 3.8 or newer**
- **pip** (comes with Python)
- **Tkinter** — needed for the GUI only; not needed for `--cli`. It ships
  with the official python.org installers on both Windows and macOS, but see
  the platform notes below if you installed Python a different way.
- An internet connection, to reach `eutils.ncbi.nlm.nih.gov` and, on first
  run, to let pip install `lxml` if it isn't already present.

---

## Setup on Windows

1. **Install Python.**
   Download the installer from [python.org/downloads](https://www.python.org/downloads/).
   On the first install screen, check **"Add python.exe to PATH"** before
   clicking Install. Tkinter is bundled with this installer, so no extra step
   is needed for the GUI.

2. **Verify the install**, in Command Prompt or PowerShell:
   ```bat
   python --version
   python -m tkinter
   ```
   The second command should pop up a tiny "Tk" test window — close it once it appears.
   If you get `'python' is not recognized`, reopen your terminal (PATH changes
   need a fresh shell) or reinstall with the PATH checkbox checked.

3. **(Recommended) Create a virtual environment**, from the project folder:
   ```bat
   python -m venv venv
   venv\Scripts\activate
   ```

4. **Install the dependencies:**
   ```bat
   pip install requests beautifulsoup4 lxml
   ```
   (`main.py` will also try to install `lxml` automatically on first run if
   it's missing — but installing it up front avoids any first-run delay.)

5. **Run it:**
   ```bat
   python main.py
   ```

## Setup on macOS

1. **Install Python.**
   Download the installer from [python.org/downloads](https://www.python.org/downloads/)
   and run it. This build includes Tkinter (via a bundled Tcl/Tk), so the GUI
   works out of the box.

   > If you instead install Python via **Homebrew** (`brew install python`),
   > Tkinter is *not* included by default. Add it separately:
   > ```bash
   > brew install python-tk
   > ```

2. **Verify the install**, in Terminal:
   ```bash
   python3 --version
   python3 -m tkinter
   ```
   A small test window should appear — close it once it shows up.

3. **(Recommended) Create a virtual environment**, from the project folder:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

4. **Install the dependencies:**
   ```bash
   pip3 install requests beautifulsoup4 lxml
   ```

5. **Run it:**
   ```bash
   python3 main.py
   ```

---

## Usage — GUI

Run:

```bash
python main.py
```

(On macOS, use `python3` if that's how your system is set up.)

1. A window opens with **one input box**, labeled for a PMCID or a PMC
   article URL. You can type either form — e.g. `PMC1234567`, `1234567`, or
   `https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/`.
2. Click **"+ Add another"** to add more input rows, one per article. Each
   row has a small **"−"** button to remove it again (there's always at
   least one row left).
3. **"Save to"** lets you pick the output folder for the JSON files (Browse…
   opens a folder picker). It defaults to the folder you ran the app from.
4. Click **Submit**. It's validated first:
   - Nothing entered → an error dialog, nothing is fetched.
   - An entry that isn't recognizable as a PMCID or PMC URL → an error dialog
     naming which entry, nothing is fetched.
5. Once validated, articles are fetched one at a time in the background (the
   window stays responsive). You'll land on one of two result screens:
   - **✅ All articles fetched successfully** — every JSON file was saved.
   - **⚠ Some articles could not be fetched** — a per-article list showing
     which succeeded (and where the file went) and which failed (and why).
     Anything that *did* succeed is still saved to disk even if others failed.
6. **"Start over"** clears the results and returns you to the input screen.

## Usage — CLI

For terminal-only or scripted use:

```bash
python main.py --cli
```

You'll be prompted once for one or more PMCIDs, separated by spaces or commas:

```
PMCID(s) (e.g. PMC1234567 — separate multiple with spaces/commas): PMC1234567, PMC7654321
```

- One PMCID → saved as `<PMCID>.json` in the current directory.
- Multiple PMCIDs → saved together as a JSON array in `pmc_articles.json`.

**Optional environment variables** (CLI only) — set these to raise NCBI's
rate limit from 3 to 10 requests/second and to follow their usage guidelines:

```bash
export NCBI_API_KEY=your_key_here   # Windows: set NCBI_API_KEY=your_key_here
export NCBI_EMAIL=you@example.com   # Windows: set NCBI_EMAIL=you@example.com
```

*(Note: the GUI currently always fetches without an API key. If you're
pulling a lot of articles, the CLI with these variables set will be faster.)*

## Output format

Each article becomes a JSON object like this (abridged):

```json
{
  "pmcid": "PMC1234567",
  "pmid": "98765432",
  "doi": "10.1000/testdoi",
  "title": "A Test Article About Testing",
  "journal": "Journal of Testing",
  "issn": "1234-5678",
  "authors": [
    {
      "name": "Jane Smith",
      "affiliation_ids": ["aff1"],
      "affiliations": ["Department of Testing, Test University"]
    }
  ],
  "publication_date": "2025-03-14",
  "keywords": ["testing", "software"],
  "abstract": "This is the abstract text describing the test study.",
  "body": [
    {
      "heading": "Introduction",
      "paragraphs": ["This is the introduction paragraph."],
      "subsections": [
        {
          "heading": "Background",
          "paragraphs": ["Some background text."],
          "subsections": []
        }
      ]
    }
  ],
  "has_full_text": true,
  "references": [
    {
      "id": "R1",
      "authors": ["A Brown"],
      "title": "Prior work",
      "source": "Some Journal",
      "year": "2020",
      "doi": null
    }
  ]
}
```

If `has_full_text` is `false`, the article isn't in the PMC Open Access
subset — you'll still get the title/authors/abstract/metadata, but `body`
will be empty since EFetch doesn't return full text for it.

## Troubleshooting

- **`Couldn't find a tree builder with the features you requested: lxml-xml`**
  — `lxml` isn't installed. `main.py` tries to install it automatically the
  moment this happens; if that fails (e.g. no internet), run
  `pip install lxml` yourself and try again.
- **GUI won't open / `ModuleNotFoundError: No module named 'tkinter'`** — your
  Python build doesn't include Tkinter. On Windows, reinstall from
  python.org. On macOS with Homebrew, run `brew install python-tk`. In the
  meantime, `python main.py --cli` still works with no GUI dependency.
- **An article returns `has_full_text: false` or an "NCBI returned an error"
  message** — the PMCID may not exist, or the article isn't part of the PMC
  Open Access subset, so only metadata is available (see [Known limitations](#known-limitations)).
- **Fetching many articles is slow / rate-limited** — see the `NCBI_API_KEY`
  / `NCBI_EMAIL` note under [Usage — CLI](#usage--cli).

## Known limitations

- Full body text is only available for the **PMC Open Access subset**; other
  articles will come back as metadata-only.
- The GUI fetches sequentially and doesn't currently accept an NCBI API key
  (the CLI does).
- No retry logic on transient network failures — a failed article can simply
  be re-submitted.

---

## Optional: building a standalone executable

If you'd like to hand this to someone without them installing Python, you
can bundle it into a single executable with [PyInstaller](https://pyinstaller.org/):

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "PMC Article Fetcher" main.py
```

- `--windowed` suppresses the console window when the GUI launches (drop it
  if you want `--cli` to still work from the built executable).
- The output lands in `dist/` — `dist\PMC Article Fetcher.exe` on Windows,
  `dist/PMC Article Fetcher` (or a `.app` bundle, depending on PyInstaller
  version) on macOS.
- Since `gui.py` is imported dynamically from inside `main.py`, PyInstaller
  should detect and bundle it automatically. If it doesn't, add
  `--hidden-import gui` to the command above.
- Build separately on each OS — a Windows build only produces a `.exe`, and a
  macOS build only produces a Mac app; PyInstaller doesn't cross-compile.
