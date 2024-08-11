.PHONY: install test unit integration server scrape

install:
	poetry install

server:
	poetry run uvicorn src.db.server:app --reload

test:
	poetry run python -m unittest discover -s tests

unit:
	poetry run python -m unittest discover -s tests/unit

integration:
	poetry run python -m unittest discover -s tests/integration

test-scraper:
	SHOW_DATA=true poetry run python -m unittest tests.unit.test_CourseScraper

test-fetcher:
	SHOW_DATA=true poetry run python -m unittest tests.integration.test_DataFetcher

scrape:
	poetry run python src/scraper/CourseScraper.py
