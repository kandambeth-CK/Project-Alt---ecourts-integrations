import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import re

# ---------------- CONFIG ----------------
BASE = "https://services.ecourts.gov.in/ecourtindia_v6"
STATE_CODE = "26"   # Delhi (site uses 26)
STATE_NAME = "Delhi"
OUTPUT_FILE = "ecourts_delhi.xlsx"
RATE_MIN, RATE_MAX = 0.5, 1.5
# ----------------------------------------

session = requests.Session()
session.headers.update({
	"User-Agent": "Mozilla/5.0",
	"Referer": f"{BASE}/?p=casestatus",
	"X-Requested-With": "XMLHttpRequest",
})


def get_app_token() -> str:
	# Visit base to capture a token embedded in links
	resp = session.get(f"{BASE}/", timeout=30)
	resp.raise_for_status()
	# Look for app_token in any href
	match = re.search(r"app_token=([a-f0-9]{64})", resp.text)
	if match:
		return match.group(1)
	# Try the casestatus landing too
	resp2 = session.get(f"{BASE}/?p=casestatus", timeout=30)
	match2 = re.search(r"app_token=([a-f0-9]{64})", resp2.text)
	return match2.group(1) if match2 else ""


def prime_session_with_token(token: str) -> None:
	# Open casestatus with token to initialize session state
	if token:
		session.get(f"{BASE}/?p=casestatus/index&app_token={token}", timeout=30)


def safe_get(url, params=None) -> str:
	r = session.get(url, params=params, timeout=30, allow_redirects=True)
	r.raise_for_status()
	return r.text


def router_post(endpoint: str, data: dict, token: str) -> str:
	url = f"{BASE}/?p={endpoint}"
	form = dict(data)
	form["ajax_req"] = "true"
	if token:
		form["app_token"] = token
	r = session.post(url, data=form, timeout=30, allow_redirects=True)
	r.raise_for_status()
	# Prefer JSON with data_list
	try:
		obj = r.json()
		return obj.get("data_list") or obj.get("data") or r.text
	except ValueError:
		return r.text


def parse_options(html_text):
	soup = BeautifulSoup(html_text, "html.parser")
	return [(opt.get("value", "").strip(), opt.text.strip()) for opt in soup.find_all("option") if opt.get("value")]


def get_districts(state_code, token: str):
	# Use router endpoint
	return parse_options(router_post("casestatus/getDistrict", {"state_code": state_code}, token))


def get_court_complexes(state_code, dist_code, token: str):
	return parse_options(router_post("casestatus/getCourtComplex", {"state_code": state_code, "dist_code": dist_code}, token))


def get_establishments(state_code, dist_code, court_code, token: str):
	return parse_options(router_post("casestatus/getEstablishment", {
		"state_code": state_code,
		"dist_code": dist_code,
		"court_complex_code": court_code
	}, token))


def random_sleep():
	time.sleep(random.uniform(RATE_MIN, RATE_MAX))


def scrape_state(state_code, state_name):
	rows = []
	token = get_app_token()
	prime_session_with_token(token)
	districts = get_districts(state_code, token)
	print(f"{state_name}: {len(districts)} districts found")

	for dist_val, dist_label in districts:
		random_sleep()
		courts = get_court_complexes(state_code, dist_val, token)
		for court_val, court_label in courts:
			random_sleep()
			ests = get_establishments(state_code, dist_val, court_val, token)
			for est_val, est_label in ests:
				rows.append({
					"State": state_name,
					"District": dist_label,
					"Court Complex": court_label,
					"Establishment": est_label
				})

	df = pd.DataFrame(rows)
	df.to_excel(OUTPUT_FILE, index=False)
	print(f"Done. Scraped {len(df)} rows for {state_name}. Saved to {OUTPUT_FILE}")
	return df


if __name__ == "__main__":
	scrape_state(STATE_CODE, STATE_NAME)

