import argparse
import sys
import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import random
import os
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def create_session(user_agent: str) -> requests.Session:
	"""Create a configured requests session with default headers."""
	session = requests.Session()
	session.headers.update({
		"User-Agent": user_agent.strip() or "Mozilla/5.0 (compatible; TestBot/1.0)"
	})
	return session


def safe_get(
	session: requests.Session,
	url: str,
	params: Optional[Dict[str, str]] = None,
	max_retries: int = 5,
	backoff_factor: float = 0.8,
	per_request_timeout_seconds: float = 30.0,
) -> requests.Response:
	"""
	HTTP GET with retries and exponential backoff with jitter.
	Retries on network errors, 429, and 5xx. Raises on other 4xx immediately.
	"""
	attempt_index = 0
	while True:
		try:
			response = session.get(
				url,
				params=params,
				timeout=per_request_timeout_seconds,
				allow_redirects=True,
			)
			status = response.status_code
			# Retry on 429 and 5xx
			if status == 429 or 500 <= status < 600:
				raise requests.HTTPError(f"Transient HTTP error: {status}", response=response)
			# Raise on other error codes
			response.raise_for_status()
			return response
		except requests.HTTPError as http_error:
			response = getattr(http_error, "response", None)
			status = getattr(response, "status_code", None)
			# Retry only for 429 and 5xx
			if status not in {429} and not (status is not None and 500 <= status < 600):
				raise
		except (requests.ConnectionError, requests.Timeout) as transient_error:
			# retry
			pass
		except requests.RequestException:
			# Non-retryable
			raise

		if attempt_index >= max_retries:
			raise

		# Exponential backoff with jitter
		delay_seconds = (backoff_factor * (2 ** attempt_index)) + random.uniform(0.0, 0.3)
		time.sleep(delay_seconds)
		attempt_index += 1


def parse_options(html_text: str) -> List[Tuple[str, str]]:
	soup = BeautifulSoup(html_text, "html.parser")
	options: List[Tuple[str, str]] = []
	for option in soup.find_all("option"):
		value = option.get("value", "").strip()
		label = option.text.strip()
		if value:
			options.append((value, label))
	return options


def get_states(session: requests.Session, base_url: str, **request_kwargs) -> List[Tuple[str, str]]:
	url = f"{base_url}/casestatus/getState"
	return parse_options(safe_get(session, url, **request_kwargs).text)


def get_districts(session: requests.Session, base_url: str, state_code: str, **request_kwargs) -> List[Tuple[str, str]]:
	url = f"{base_url}/casestatus/getDistrict"
	return parse_options(safe_get(session, url, params={"state_code": state_code}, **request_kwargs).text)


def get_court_complexes(
	session: requests.Session,
	base_url: str,
	state_code: str,
	district_code: str,
	**request_kwargs,
) -> List[Tuple[str, str]]:
	url = f"{base_url}/casestatus/getCourtComplex"
	return parse_options(
		safe_get(
			session,
			url,
			params={"state_code": state_code, "dist_code": district_code},
			**request_kwargs,
		).text
	)


def get_establishments(
	session: requests.Session,
	base_url: str,
	state_code: str,
	district_code: str,
	court_complex_code: str,
	**request_kwargs,
) -> List[Tuple[str, str]]:
	url = f"{base_url}/casestatus/getEstablishment"
	return parse_options(
		safe_get(
			session,
			url,
			params={
				"state_code": state_code,
				"dist_code": district_code,
				"court_complex_code": court_complex_code,
			},
			**request_kwargs,
		).text
	)


def random_sleep(min_seconds: float, max_seconds: float) -> None:
	if max_seconds <= 0:
		return
	wait_seconds = random.uniform(max(0.0, min_seconds), max_seconds)
	time.sleep(wait_seconds)


def load_existing_rows(csv_path: str) -> Tuple[List[Dict[str, str]], "set[Tuple[str, str, str, str]]"]:
	if os.path.exists(csv_path):
		df_existing = pd.read_csv(csv_path, dtype=str)
		rows_existing = df_existing.fillna("").to_dict("records")
		seen_existing = set(
			(
				row.get("State", ""),
				row.get("District", ""),
				row.get("Court Complex", ""),
				row.get("Establishment", ""),
			)
			for row in rows_existing
		)
		print(f"Resuming from {len(rows_existing)} previously saved rows.")
		return rows_existing, seen_existing
	return [], set()


def save_rows(rows: List[Dict[str, str]], csv_path: str, xlsx_path: Optional[str]) -> None:
	df = pd.DataFrame(rows)
	df.to_csv(csv_path, index=False)
	if xlsx_path:
		# Write Excel only when path provided
		df.to_excel(xlsx_path, index=False)


def scrape(
	base_url: str,
	session: requests.Session,
	output_csv_path: str,
	output_xlsx_path: Optional[str],
	resume_from_csv: bool,
	rate_min_seconds: float,
	rate_max_seconds: float,
	checkpoint_every_rows: int,
	row_limit: int,
	request_timeout_seconds: float,
	max_retries: int,
	backoff_factor: float,
) -> None:
	if resume_from_csv:
		rows, seen = load_existing_rows(output_csv_path)
	else:
		rows, seen = [], set()

	request_kwargs = {
		"max_retries": max_retries,
		"backoff_factor": backoff_factor,
		"per_request_timeout_seconds": request_timeout_seconds,
	}

	states = get_states(session, base_url, **request_kwargs)
	print(f"Found {len(states)} states. Starting scrape...")

	total_states = len(states)
	rows_written_since_checkpoint = 0

	stop_requested = False
	for state_index, (state_code, state_label) in enumerate(states, start=1):
		print(f"\nProcessing State {state_index}/{total_states}: {state_label}")
		random_sleep(rate_min_seconds, rate_max_seconds)

		districts = get_districts(session, base_url, state_code, **request_kwargs)
		for district_code, district_label in districts:
			random_sleep(rate_min_seconds, rate_max_seconds)

			courts = get_court_complexes(session, base_url, state_code, district_code, **request_kwargs)
			for court_code, court_label in courts:
				random_sleep(rate_min_seconds, rate_max_seconds)

				establishments = get_establishments(
					session,
					base_url,
					state_code,
					district_code,
					court_code,
					**request_kwargs,
				)
				for establishment_code, establishment_label in establishments:
					combination = (state_label, district_label, court_label, establishment_label)
					if combination in seen:
						continue

					row = {
						"State": state_label,
						"District": district_label,
						"Court Complex": court_label,
						"Establishment": establishment_label,
					}
					rows.append(row)
					seen.add(combination)
					rows_written_since_checkpoint += 1

					count = len(rows)
					if checkpoint_every_rows > 0 and rows_written_since_checkpoint >= checkpoint_every_rows:
						save_rows(rows, output_csv_path, output_xlsx_path)
						rows_written_since_checkpoint = 0
						print(f"\nCheckpoint: {count} combinations scraped. Last 5:")
						print(pd.DataFrame(rows).tail(5).to_string(index=False))

					if row_limit > 0 and count >= row_limit:
						stop_requested = True
						break

					random_sleep(rate_min_seconds, rate_max_seconds)
				if stop_requested:
					break
			if stop_requested:
				break
		if stop_requested:
			break

	# Final save
	save_rows(rows, output_csv_path, output_xlsx_path)
	print(f"\nDone. Scraped total {len(rows)} rows. Saved to {output_csv_path}{' and ' + output_xlsx_path if output_xlsx_path else ''}.")


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Scrape eCourts state/district/court/establishment combinations with resume and checkpoints.")
	parser.add_argument("--base", default="https://services.ecourts.gov.in/ecourtindia_v6", help="Base URL for the eCourts service.")
	parser.add_argument("--output-csv", default="ecourts_combinations.csv", help="Path to CSV output file.")
	parser.add_argument("--output-xlsx", default="ecourts_combinations.xlsx", help="Path to XLSX output file. Use --no-xlsx to disable writing Excel.")
	parser.add_argument("--xlsx", dest="write_xlsx", action="store_true", help="Write XLSX (default)")
	parser.add_argument("--no-xlsx", dest="write_xlsx", action="store_false", help="Disable writing XLSX file.")
	parser.set_defaults(write_xlsx=True)
	parser.add_argument("--resume", dest="resume", action="store_true", help="Resume from existing CSV if present (default)")
	parser.add_argument("--no-resume", dest="resume", action="store_false", help="Do not resume from existing CSV.")
	parser.set_defaults(resume=True)
	parser.add_argument("--checkpoint-every", type=int, default=100, help="Autosave checkpoint frequency in rows. 0 disables periodic checkpoints.")
	parser.add_argument("--max-rows", type=int, default=0, help="Stop after this many rows (>0). 0 means no limit.")
	parser.add_argument("--rate-min", type=float, default=0.5, help="Minimum delay between requests (seconds). Use 0 to disable.")
	parser.add_argument("--rate-max", type=float, default=1.5, help="Maximum delay between requests (seconds). Use 0 to disable.")
	parser.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout in seconds.")
	parser.add_argument("--retries", type=int, default=5, help="Max retries for transient errors (429/5xx, timeouts, connection errors).")
	parser.add_argument("--backoff", type=float, default=0.8, help="Backoff factor for exponential backoff.")
	parser.add_argument("--user-agent", default="Mozilla/5.0 (compatible; TestBot/1.0)", help="User-Agent header to send.")
	parser.add_argument("--dry-run", action="store_true", help="Print configuration and exit without scraping.")
	return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
	parser = build_parser()
	args = parser.parse_args(argv)

	# Validate rate bounds
	if args.rate_max < args.rate_min:
		parser.error("--rate-max must be >= --rate-min")

	output_xlsx_path: Optional[str] = args.output_xlsx if args.write_xlsx else None

	if args.dry_run:
		print("Dry run: no network calls will be made. Configuration:")
		print(f" base={args.base}")
		print(f" output_csv={args.output_csv}")
		print(f" output_xlsx={output_xlsx_path}")
		print(f" resume={args.resume}")
		print(f" checkpoint_every={args.checkpoint_every}")
		print(f" max_rows={args.max_rows}")
		print(f" rate_min={args.rate_min}")
		print(f" rate_max={args.rate_max}")
		print(f" timeout={args.timeout}")
		print(f" retries={args.retries}")
		print(f" backoff={args.backoff}")
		print(f" user_agent={args.user_agent}")
		return 0

	session = create_session(args.user_agent)

	try:
		scrape(
			base_url=args.base,
			session=session,
			output_csv_path=args.output_csv,
			output_xlsx_path=output_xlsx_path,
			resume_from_csv=args.resume,
			rate_min_seconds=args.rate_min,
			rate_max_seconds=args.rate_max,
			checkpoint_every_rows=args.checkpoint_every,
			row_limit=args.max_rows,
			request_timeout_seconds=args.timeout,
			max_retries=args.retries,
			backoff_factor=args.backoff,
		)
		return 0
	except KeyboardInterrupt:
		print("\nInterrupted by user. Partial results (if any) have been saved.")
		return 130
	except Exception as exc:
		print(f"\nError: {exc}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main())

