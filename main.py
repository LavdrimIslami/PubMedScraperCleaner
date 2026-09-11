import json
import os
import sys
import time
import warnings

import requests
from bs4 import BeautifulSoup

try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# We deliberately require lxml rather than falling back to Python's built-in
# html.parser. html.parser follows HTML5 rules, which treat several tag names
# that are common in JATS XML (e.g. <source>, used for journal names in
# references) as "void" HTML elements with no content -- silently dropping
# their text instead of raising an error. A hard failure is much safer than
# quietly corrupted data here.
def ensure_lxml():
    try:
        import lxml  # noqa: F401
        return True
    except ImportError:
        print("The 'lxml' package is required to parse PMC's XML. Attempting to install it...")
        try:
            import subprocess
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", "--break-system-packages", "lxml"]
            )
            # bs4 only registers the lxml-xml tree builder if lxml was importable
            # at the moment bs4 itself was first imported. Since bs4 is already
            # loaded in this process, purge it (and lxml) from sys.modules and
            # re-import so the builder registration re-runs now that lxml exists.
            import importlib
            for mod_name in list(sys.modules):
                if mod_name == "bs4" or mod_name.startswith("bs4.") or mod_name == "lxml" or mod_name.startswith("lxml."):
                    del sys.modules[mod_name]
            importlib.invalidate_caches()
            global BeautifulSoup
            import lxml  # noqa: F401
            from bs4 import BeautifulSoup  # re-imported with lxml now registered
            print("lxml installed successfully.\n")
            return True
        except Exception as e:
            print(f"Automatic install failed: {e}")
            print("Please run:  pip install lxml")
            return False


XML_PARSER = "lxml-xml"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def normalize_pmcid(raw):
    """Accepts 'PMC1234567', 'pmc1234567', or '1234567' and returns 'PMC1234567'."""
    raw = raw.strip().upper()
    if not raw:
        raise ValueError("empty PMCID")
    if not raw.startswith("PMC"):
        raw = "PMC" + raw
    digits = raw[3:]
    if not digits.isdigit():
        raise ValueError(f"'{raw}' doesn't look like a PMCID (expected 'PMC' + digits)")
    return raw


def text_or_none(tag):
    return tag.get_text(strip=True) if tag else None


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def fetch_pmc_xml(pmcid, api_key=None, email=None, tool="pmc-fetch-script"):
    """Calls EFetch for the given PMCID and returns the raw XML response text."""
    params = {
        "db": "pmc",
        "id": pmcid,
        "retmode": "xml",
        "rettype": "full",
        "tool": tool,
    }
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key

    resp = requests.get(EFETCH_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.text


# --------------------------------------------------------------------------
# Parsing (NLM/JATS XML -> dict)
# --------------------------------------------------------------------------

def parse_authors(article_meta):
    authors = []
    for contrib in article_meta.find_all("contrib", {"contrib-type": "author"}):
        name_tag = contrib.find("name")
        if name_tag:
            surname = text_or_none(name_tag.find("surname"))
            given = text_or_none(name_tag.find("given-names"))
            full_name = " ".join(p for p in [given, surname] if p)
        else:
            full_name = text_or_none(contrib.find("collab"))
        aff_ids = [x.get("rid") for x in contrib.find_all("xref", {"ref-type": "aff"})]
        authors.append({"name": full_name, "affiliation_ids": aff_ids})
    return authors


def parse_affiliations(article_meta):
    return {
        aff.get("id"): aff.get_text(" ", strip=True)
        for aff in article_meta.find_all("aff")
        if aff.get("id")
    }


def parse_pub_date(article_meta):
    pub_date = article_meta.find("pub-date")
    if not pub_date:
        return None
    parts = [text_or_none(pub_date.find(tag)) for tag in ("year", "month", "day")]
    parts = [p for p in parts if p]
    return "-".join(parts) if parts else None


def parse_section(sec):
    """Recursively parses a <sec> element into heading/paragraphs/subsections."""
    return {
        "heading": text_or_none(sec.find("title", recursive=False)),
        "paragraphs": [p.get_text(" ", strip=True) for p in sec.find_all("p", recursive=False)],
        "subsections": [parse_section(s) for s in sec.find_all("sec", recursive=False)],
    }


def parse_body(article):
    body = article.find("body")
    if not body:
        return []
    return [parse_section(sec) for sec in body.find_all("sec", recursive=False)]


def parse_references(article):
    refs = []
    ref_list = article.find("ref-list")
    if not ref_list:
        return refs
    for ref in ref_list.find_all("ref"):
        citation = ref.find("element-citation") or ref.find("mixed-citation")
        if not citation:
            continue
        refs.append({
            "id": ref.get("id"),
            "authors": [n.get_text(" ", strip=True) for n in citation.find_all("name")],
            "title": text_or_none(citation.find("article-title")),
            "source": text_or_none(citation.find("source")),
            "year": text_or_none(citation.find("year")),
            "doi": text_or_none(citation.find("pub-id", {"pub-id-type": "doi"})),
        })
    return refs


def parse_article(xml_text, requested_pmcid=None):
    soup = BeautifulSoup(xml_text, XML_PARSER)

    error_tag = soup.find(lambda t: t.name and t.name.lower() == "error")
    if error_tag is not None:
        raise ValueError(f"NCBI returned an error for {requested_pmcid}: {error_tag.get_text(strip=True)}")

    article = soup.find("article")
    if article is None:
        raise ValueError(
            f"No <article> full text returned for {requested_pmcid}. It may not be part of "
            "the PMC Open Access subset, so EFetch only returns citation metadata, not body text."
        )

    article_meta = article.find("article-meta")
    journal_meta = article.find("journal-meta")

    pmcid = pmid = doi = None
    for aid in article_meta.find_all("article-id"):
        id_type = aid.get("pub-id-type")
        if id_type == "pmc":
            pmcid = normalize_pmcid(aid.get_text(strip=True))
        elif id_type == "pmid":
            pmid = aid.get_text(strip=True)
        elif id_type == "doi":
            doi = aid.get_text(strip=True)

    affiliations = parse_affiliations(article_meta)
    authors = parse_authors(article_meta)
    for author in authors:
        author["affiliations"] = [affiliations[i] for i in author["affiliation_ids"] if i in affiliations]

    body_sections = parse_body(article)

    return {
        "pmcid": pmcid or requested_pmcid,
        "pmid": pmid,
        "doi": doi,
        "title": text_or_none(article_meta.find("article-title")),
        "journal": text_or_none(journal_meta.find("journal-title")) if journal_meta else None,
        "issn": text_or_none(journal_meta.find("issn")) if journal_meta else None,
        "authors": authors,
        "publication_date": parse_pub_date(article_meta),
        "keywords": [k.get_text(strip=True) for k in article_meta.find_all("kwd")],
        "abstract": text_or_none(article_meta.find("abstract")),
        "body": body_sections,
        "has_full_text": bool(body_sections),
        "references": parse_references(article),
    }


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    if not ensure_lxml():
        sys.exit(1)

    raw = input("PMCID(s) (e.g. PMC1234567 — separate multiple with spaces/commas): ")
    ids = [x for x in raw.replace(",", " ").split() if x]
    if not ids:
        print("No PMCID provided.")
        sys.exit(1)

    # Optional: set these env vars to raise NCBI's rate limit from 3/sec to 10/sec
    # and to follow NCBI's usage guidelines (https://www.ncbi.nlm.nih.gov/books/NBK25497/)
    api_key = os.environ.get("NCBI_API_KEY")
    email = os.environ.get("NCBI_EMAIL")

    articles = []
    for i, raw_id in enumerate(ids):
        try:
            pmcid = normalize_pmcid(raw_id)
        except ValueError as e:
            print(f"Skipping '{raw_id}': {e}")
            continue

        print(f"Fetching {pmcid} ...")
        try:
            xml_text = fetch_pmc_xml(pmcid, api_key=api_key, email=email)
            articles.append(parse_article(xml_text, requested_pmcid=pmcid))
        except (requests.RequestException, ValueError) as e:
            print(f"  -> failed: {e}")
            articles.append({"pmcid": pmcid, "error": str(e)})

        if i < len(ids) - 1:
            time.sleep(0.11 if api_key else 0.34)  # stay under NCBI's rate limit

    if not articles:
        print("Nothing was fetched.")
        sys.exit(1)

    output = articles[0] if len(articles) == 1 else articles
    out_name = f"{articles[0]['pmcid']}.json" if len(articles) == 1 else "pmc_articles.json"

    with open(out_name, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nSaved {len(articles)} article(s) to {out_name}")


if __name__ == "__main__":
    main()