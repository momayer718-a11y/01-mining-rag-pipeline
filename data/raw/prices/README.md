# Authorized Price CSV Input

Put licensed or user-authorized LME, SHFE and Mysteel exports in this folder as `.csv` files.

The collector treats these rows as official numeric price evidence because they were supplied through an authorized local file. Do not place scraped login-wall, paid-feed, CAPTCHA-protected or Cloudflare-protected data here unless you have permission to use it.

Required columns:

```csv
date,commodity,price,currency,unit,source,title,url,region
2026-06-24,copper,9800,USD,t,LME,LME Copper Official Price,https://www.lme.com/en/Metals/Non-ferrous/LME-Copper,
```

`price` must be a decimal string. `date` must be an ISO date. `url` should be the original vendor/source page or licensed export reference.

Before indexing, validate and import a licensed CSV:

```bash
python3 scripts/import_price_csv.py /path/to/lme_prices.csv --strict
PYTHONPATH=. python3 -m pipeline.ingest --out data/runtime_full --per-source 200
```

For authorized APIs, set comma-separated endpoints that return either JSON rows or CSV with the same schema:

```bash
export AUTHORIZED_PRICE_API_URLS="https://vendor.example/prices/lme.csv,https://vendor.example/prices/shfe.json"
export AUTHORIZED_PRICE_API_TOKEN="your_vendor_token"
```

Public FRED/Yahoo rows, when enabled, are proxies only and must not be described as LME/SHFE/Mysteel official data.
