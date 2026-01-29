# Multi-site Price Finder (Streamlit)

A small web app that lets you:
- enter an item in a search box
- add/edit a list of websites **at runtime**
- scrape those websites' search result pages to find prices
- show the lowest price found

> Note: Many sites block scraping. For best results, use sites that don't aggressively block bots, or build site-specific connectors.

## Files
- `app.py` - the Streamlit app
- `requirements.txt` - Python dependencies

## Run locally
1) Install Python 3.10+  
2) Install dependencies:
```bash
pip install -r requirements.txt
```
3) Run:
```bash
streamlit run app.py
```

## Deploy on Streamlit Community Cloud (no local install needed)
1) Create a GitHub repo and upload `app.py` + `requirements.txt`
2) Go to Streamlit Cloud and deploy from your repo
3) Set the main file to `app.py`

## How to configure a site
For each site, supply:
- **Search URL template**: must include `{query}` e.g. `https://example.com/search?q={query}`
- **CSS selectors**:
  - Card selector: selects each product tile in results
  - Title selector: within the card
  - Price selector: within the card
  - Link selector: within the card (usually an `<a>`)

Tip: Use your browser dev tools (Inspect Element) to find the right CSS classes/selectors.
